from __future__ import annotations

import json
import time
from pathlib import Path

import cv2

from app.audit import AuditStore
from app.colorization import (
    ColorizationModelUnavailable,
    compare_images,
    colorize_image_array,
    enhance_existing_color,
    make_image_comparison,
    model_metadata,
    restore_image_array,
)
from app.profiles import DEFAULT_COLOR_STYLE, normalize_color_mode
from app.storage import LocalBucket


class ImagePipeline:
    """Pipeline de restauració de fotografia amb colorització neuronal opcional."""

    def __init__(self, audit: AuditStore | None = None, bucket: LocalBucket | None = None) -> None:
        self.audit = audit or AuditStore()
        self.bucket = bucket or LocalBucket()

    def run(
        self,
        job_id: str,
        original_path: Path,
        *,
        color_mode: str = "ai_natural",
        color_style: str = DEFAULT_COLOR_STYLE,
    ) -> None:
        started = time.perf_counter()
        color_mode = normalize_color_mode(color_mode)
        self.audit.update_job(job_id, "processing")
        self.audit.event(
            job_id,
            "pipeline",
            "image_pipeline_started",
            "Image restoration pipeline started",
            {"color_mode": color_mode, "color_style": color_style},
        )
        try:
            restored_path = self._restore(job_id, original_path)
            final_path = self._apply_color(job_id, restored_path, color_mode, color_style)
            comparison_path = self.bucket.image_comparison_path(job_id)
            self._comparison(job_id, original_path, final_path, comparison_path)
            self.audit.metric(job_id, "elapsed_seconds", time.perf_counter() - started)
            self.audit.metric(job_id, "quality_summary", None, compare_images(original_path, final_path))
            self.audit.update_job(job_id, "restored", final_path=final_path)
            self.audit.event(job_id, "pipeline", "image_pipeline_finished", "Image restoration pipeline completed")
            self._write_audit_json(job_id)
        except Exception as exc:
            self.audit.update_job(job_id, "error", error=str(exc))
            self.audit.event(job_id, "pipeline", "image_pipeline_failed", str(exc))
            raise

    def _restore(self, job_id: str, original_path: Path) -> Path:
        output_path = self.bucket.image_restored_path(job_id)
        step_id = self.audit.start_step(
            job_id,
            "image",
            "restore_image_base",
            original_path,
            {"denoise": "fastNlMeansDenoisingColored", "contrast": "CLAHE", "sharpen": "unsharp"},
        )
        try:
            image = cv2.imread(str(original_path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Uploaded image is not readable.")
            restored = restore_image_array(image)
            if not cv2.imwrite(str(output_path), restored):
                raise RuntimeError(f"Could not write restored image: {output_path}")
            self.audit.finish_step(
                step_id,
                "completed",
                output_path=output_path,
                details={"width": int(restored.shape[1]), "height": int(restored.shape[0])},
            )
            return output_path
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"error": str(exc)})
            raise

    def _apply_color(self, job_id: str, restored_path: Path, color_mode: str, color_style: str) -> Path:
        if color_mode == "none":
            return restored_path

        output_path = (
            self.bucket.image_colorized_path(job_id)
            if color_mode == "ai_natural"
            else restored_path.with_name("enhanced.png")
        )
        step_name = "colorize_image_ai_natural" if color_mode == "ai_natural" else "enhance_image_color"
        details = {
            "color_mode": color_mode,
            "color_style": color_style,
            "real_colorization": color_mode == "ai_natural",
        }
        if color_mode == "ai_natural":
            details |= model_metadata()
        step_id = self.audit.start_step(job_id, "image", step_name, restored_path, details)
        try:
            image = cv2.imread(str(restored_path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Restored image is not readable.")
            if color_mode == "ai_natural":
                result, model_details = colorize_image_array(image, color_style=color_style)
                details |= model_details
            else:
                result = enhance_existing_color(image)
                details["engine"] = "opencv_classic_color_enhancement"
            if not cv2.imwrite(str(output_path), result):
                raise RuntimeError(f"Could not write image color output: {output_path}")
            self.audit.finish_step(step_id, "completed", output_path=output_path, details=details)
            return output_path
        except ColorizationModelUnavailable:
            self.audit.finish_step(step_id, "failed", details=details | {"error": "model_unavailable"})
            raise
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details=details | {"error": str(exc)})
            raise

    def _comparison(self, job_id: str, original_path: Path, final_path: Path, comparison_path: Path) -> None:
        step_id = self.audit.start_step(job_id, "image", "create_image_before_after", final_path)
        try:
            make_image_comparison(original_path, final_path, comparison_path)
            self.audit.finish_step(step_id, "completed", output_path=comparison_path)
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"warning": str(exc)})
            self.audit.event(job_id, "pipeline", "image_comparison_failed", str(exc))

    def _write_audit_json(self, job_id: str) -> None:
        job = self.audit.get_job(job_id)
        path = self.bucket.audit_json_path(job_id)
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
