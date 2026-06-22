from __future__ import annotations

from pathlib import Path
from typing import Callable

from app import cloud_jobs
from app.cloud_storage import build_gcs_uri


def dispatch_cloud_restoration(
    *,
    job_id: str,
    original_path: Path,
    colorize: bool,
    profile: str,
    color_mode: str | None = None,
    color_style: str = "historical_natural",
    audit_store,
    sync_job_to_gcs: Callable[[str], list[str]],
    sync_job_metadata_to_bigquery: Callable[[str], None],
    safe_sync_job_to_gcs: Callable[[str], list[str]],
) -> None:
    """Prepara entrades persistents i arrenca el render remot."""
    sync_job_to_gcs(job_id)
    sync_job_metadata_to_bigquery(job_id)
    original_gcs_uri = build_gcs_uri(f"originals/{job_id}/{original_path.name}")
    audit_store.update_job(job_id, "queued")
    sync_job_metadata_to_bigquery(job_id)
    operation = cloud_jobs.dispatch_restoration_job(
        job_id=job_id,
        input_uri=original_gcs_uri,
        output_uri=build_gcs_uri(f"final/{job_id}/restored.mp4"),
        comparison_uri=build_gcs_uri(f"final/{job_id}/comparison.mp4"),
        profile=profile,
        colorize=colorize,
        color_mode=color_mode,
        color_style=color_style,
    )
    audit_store.event(
        job_id,
        "cloud_run",
        "cloud_run_job_dispatched",
        "Restoration dispatched to Cloud Run Job",
        {"profile": profile, "color_mode": color_mode, "color_style": color_style, "operation": operation.get("name")},
    )
    safe_sync_job_to_gcs(job_id)
