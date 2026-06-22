from __future__ import annotations

from pathlib import Path
from typing import Any

from app.audit import AuditStore


class AuditService:
    """API d'auditoria estable per a la capa FastAPI."""

    def __init__(self, store: AuditStore | None = None) -> None:
        self.store = store or AuditStore()

    def create_job(
        self,
        job_id: str,
        filename: str,
        original_path: Path,
        colorize: bool,
        processing_profile: str = "quality",
        media_type: str = "video",
        color_mode: str = "none",
        color_style: str = "historical_natural",
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> None:
        self.store.create_job(
            job_id,
            filename,
            original_path,
            colorize,
            processing_profile,
            media_type,
            color_mode,
            color_style,
            model_name,
            model_version,
        )

    def update_job(
        self,
        job_id: str,
        status: str,
        *,
        final_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        self.store.update_job(job_id, status, final_path=final_path, error=error)

    def start_step(
        self,
        job_id: str,
        scene_id: str,
        step_name: str,
        input_path: Path | None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return self.store.start_step(job_id, scene_id, step_name, input_path, details)

    def finish_step(
        self,
        step_id: int,
        status: str,
        *,
        output_path: Path | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.finish_step(step_id, status, output_path=output_path, details=details)

    def metric(
        self,
        job_id: str,
        name: str,
        value: float | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.metric(job_id, name, value, details)

    def event(
        self,
        job_id: str | None,
        actor: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.event(job_id, actor, event_type, message, details)

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.store.get_job(job_id)

    def save_art_audit(
        self,
        audit_id: str,
        reference_path: Path,
        current_path: Path,
        heatmap_path: Path | None,
        report: dict[str, Any],
        status: str,
    ) -> None:
        self.store.save_art_audit(audit_id, reference_path, current_path, heatmap_path, report, status)
