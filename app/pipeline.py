from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable

from app.ai_upscale import upscale_video_with_realesrgan
from app.audit import AuditStore
from app.colorization import colorize_video_file, model_metadata
from app.config import QUALITY_SAMPLE_FRAMES
from app.media import (
    ffmpeg_available,
    ffmpeg_concat,
    ffmpeg_copy,
    ffmpeg_extract_segment,
    ffmpeg_filter,
    ffprobe_metadata,
    make_side_by_side,
)
from app.quality import compare_videos
from app.profiles import (
    COLOR_ENHANCEMENT_FILTER,
    DEFAULT_COLOR_STYLE,
    PROFILES,
    normalize_color_mode,
    normalize_profile,
)
from app.scene_detection import detect_scene_ranges
from app.storage import LocalBucket


class VideoPipeline:
    """Pipeline local amb punts d'integració per GCP i models reals."""

    def __init__(self, audit: AuditStore | None = None, bucket: LocalBucket | None = None) -> None:
        self.audit = audit or AuditStore()
        self.bucket = bucket or LocalBucket()

    def run(
        self,
        job_id: str,
        original_path: Path,
        *,
        colorize: bool = False,
        color_mode: str | None = None,
        color_style: str = DEFAULT_COLOR_STYLE,
        profile: str = "quality",
    ) -> None:
        started = time.perf_counter()
        profile = normalize_profile(profile)
        color_mode = normalize_color_mode(color_mode, legacy_colorize=colorize)
        self.audit.update_job(job_id, "processing")
        self.audit.event(
            job_id,
            "pipeline",
            "pipeline_started",
            "Restoration pipeline started",
            {"profile": profile, "color_mode": color_mode, "color_style": color_style},
        )
        try:
            if not ffmpeg_available():
                raise RuntimeError("FFmpeg is required for video processing. Install ffmpeg and retry.")

            metadata = self._validate(job_id, original_path)
            scenes = self._split_scenes(job_id, original_path, metadata)
            restored_scenes: list[Path] = []

            for index, scene_path in enumerate(scenes):
                scene_id = f"scene_{index:03d}"
                restored_scenes.append(
                    self._restore_scene(
                        job_id,
                        scene_id,
                        scene_path,
                        metadata,
                        color_mode=color_mode,
                        color_style=color_style,
                        profile=profile,
                    )
                )

            final_path = self.bucket.final_path(job_id)
            self._merge(job_id, restored_scenes, final_path)
            self._create_comparison(job_id, original_path, final_path)
            self._metrics(job_id, original_path, final_path, time.perf_counter() - started)
            self.audit.update_job(job_id, "restored", final_path=final_path)
            self.audit.event(job_id, "pipeline", "pipeline_finished", "Restoration pipeline completed")
            self._write_audit_json(job_id)
        except Exception as exc:  # pragma: no cover - exercised through integration use
            self.audit.update_job(job_id, "error", error=str(exc))
            self.audit.event(job_id, "pipeline", "pipeline_failed", str(exc))
            raise

    def _restore_scene(
        self,
        job_id: str,
        scene_id: str,
        input_path: Path,
        metadata: dict,
        *,
        color_mode: str,
        color_style: str,
        profile: str,
    ) -> Path:
        if profile == "ai_realesrgan":
            settings = PROFILES[profile]
            details = {
                "profile": profile,
                "model": settings["model"],
                "scale": settings["scale"],
                "engine": "Real-ESRGAN NCNN Vulkan",
                "audio_preserved": True,
                "color_mode": color_mode,
            }
            upscaled_path = self._tracked_step(
                job_id,
                scene_id,
                "restore_ai_realesrgan",
                input_path,
                lambda output: upscale_video_with_realesrgan(
                    input_path,
                    output,
                    colorize=color_mode == "enhance",
                    scale=int(settings["scale"]),
                ),
                details,
            )
            if color_mode == "ai_natural":
                return self._colorize_scene(job_id, scene_id, upscaled_path, color_style, profile)
            return upscaled_path

        settings = PROFILES[profile]
        source_width = self._source_width(metadata)
        scale = 2 if source_width == 0 or source_width < 960 else 1
        scale_flags = str(settings.get("scale_flags", "lanczos"))
        filters = [
            str(settings["denoise"]),
            f"scale=iw*{scale}:ih*{scale}:flags={scale_flags}",
        ]
        if settings.get("deband"):
            filters.append(str(settings["deband"]))
        filters.append(str(settings["sharpen"]))
        if settings.get("tone"):
            filters.append(str(settings["tone"]))
        adapters = [
            "ProPainter-compatible deartifact adapter",
            "Real-ESRGAN-compatible super-resolution adapter",
        ]
        if color_mode == "enhance":
            filters.append(COLOR_ENHANCEMENT_FILTER)
            adapters.append("classic color enhancement adapter")
        filters.append("format=yuv420p")

        details = {
            "profile": profile,
            "adapters": adapters,
            "scale": scale,
            "ffmpeg_filter": ",".join(filters),
            "audio_preserved": True,
            "color_mode": color_mode,
            "note": "Adapters are combined into one encode to reduce latency and generation loss.",
        }
        restored_path = self._tracked_step(
            job_id,
            scene_id,
            "restore_balanced_single_pass",
            input_path,
            lambda output: ffmpeg_filter(
                input_path,
                output,
                details["ffmpeg_filter"],
                preserve_audio=True,
                preset=str(settings["preset"]),
                crf=int(settings["crf"]),
            ),
            details,
        )
        if color_mode == "ai_natural":
            return self._colorize_scene(job_id, scene_id, restored_path, color_style, profile)
        return restored_path

    def _colorize_scene(self, job_id: str, scene_id: str, input_path: Path, color_style: str, profile: str) -> Path:
        details = {
            "profile": profile,
            "color_mode": "ai_natural",
            "color_style": color_style,
            "real_colorization": True,
            **model_metadata(),
        }
        return self._tracked_step(
            job_id,
            scene_id,
            "colorize_video_ai_natural",
            input_path,
            lambda output: colorize_video_file(
                input_path,
                output,
                color_style=color_style,
                preset="veryfast",
                crf=20,
            ),
            details,
        )

    @staticmethod
    def _source_width(metadata: dict) -> int:
        for stream in metadata.get("streams", []):
            if stream.get("width"):
                return int(stream["width"])
        return 0

    def _validate(self, job_id: str, original_path: Path) -> dict:
        step_id = self.audit.start_step(job_id, "source", "validate_video", original_path)
        try:
            metadata = ffprobe_metadata(original_path)
            self.audit.finish_step(step_id, "completed", output_path=original_path, details=metadata)
            return metadata
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"error": str(exc)})
            raise

    def _split_scenes(self, job_id: str, original_path: Path, metadata: dict) -> list[Path]:
        step_id = self.audit.start_step(
            job_id,
            "source",
            "split_scenes",
            original_path,
            {"strategy": "PySceneDetect with safe single-scene fallback"},
        )
        try:
            ranges, strategy = detect_scene_ranges(original_path)
            if len(ranges) <= 1:
                scene_path = self.bucket.scene_path(job_id, "scene_000")
                ffmpeg_copy(original_path, scene_path)
                scenes = [scene_path]
            else:
                scenes = []
                for index, (start, end) in enumerate(ranges):
                    scene_path = self.bucket.scene_path(job_id, f"scene_{index:03d}")
                    ffmpeg_extract_segment(original_path, scene_path, start, end)
                    scenes.append(scene_path)
            details = {"scene_count": len(scenes), "strategy": strategy, "metadata": metadata}
            self.audit.finish_step(step_id, "completed", output_path=scenes[0], details=details)
            return scenes
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"error": str(exc)})
            raise

    def _tracked_step(
        self,
        job_id: str,
        scene_id: str,
        step_name: str,
        input_path: Path,
        operation: Callable[[Path], None],
        details: dict,
    ) -> Path:
        output_path = self.bucket.processed_path(job_id, scene_id, step_name)
        step_id = self.audit.start_step(job_id, scene_id, step_name, input_path, details)
        try:
            operation(output_path)
            self.audit.finish_step(step_id, "completed", output_path=output_path, details=details)
            return output_path
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details=details | {"error": str(exc)})
            raise

    def _merge(self, job_id: str, restored_scenes: list[Path], final_path: Path) -> None:
        step_id = self.audit.start_step(
            job_id,
            "final",
            "merge_scenes",
            restored_scenes[0] if restored_scenes else None,
            {"scene_count": len(restored_scenes)},
        )
        try:
            if len(restored_scenes) == 1:
                shutil.copyfile(restored_scenes[0], final_path)
            else:
                ffmpeg_concat(restored_scenes, final_path)
            self.audit.finish_step(step_id, "completed", output_path=final_path)
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"error": str(exc)})
            raise

    def _create_comparison(self, job_id: str, original_path: Path, final_path: Path) -> None:
        comparison_path = self.bucket.comparison_path(job_id)
        step_id = self.audit.start_step(job_id, "final", "create_before_after", final_path)
        try:
            make_side_by_side(original_path, final_path, comparison_path)
            self.audit.finish_step(step_id, "completed", output_path=comparison_path)
        except Exception as exc:
            self.audit.finish_step(step_id, "failed", details={"warning": str(exc)})
            self.audit.event(job_id, "pipeline", "comparison_failed", str(exc))

    def _metrics(self, job_id: str, original_path: Path, final_path: Path, elapsed_seconds: float) -> None:
        original_size = original_path.stat().st_size
        final_size = final_path.stat().st_size
        self.audit.metric(job_id, "elapsed_seconds", elapsed_seconds)
        self.audit.metric(job_id, "original_size_bytes", float(original_size))
        self.audit.metric(job_id, "final_size_bytes", float(final_size))
        self.audit.metric(
            job_id,
            "size_ratio",
            final_size / original_size if original_size else None,
            {"note": "Proxy metric for MVP; add VMAF/SSIM for production evaluation."},
        )
        try:
            summary = compare_videos(
                original_path,
                final_path,
                sample_frames=QUALITY_SAMPLE_FRAMES,
            )
            self.audit.metric(job_id, "quality_summary", None, summary)
        except Exception as exc:
            self.audit.event(job_id, "metrics", "quality_metrics_failed", str(exc))

    def _write_audit_json(self, job_id: str) -> None:
        job = self.audit.get_job(job_id)
        path = self.bucket.audit_json_path(job_id)
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
