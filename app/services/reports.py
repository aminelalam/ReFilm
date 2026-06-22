from __future__ import annotations


def build_job_report(job_id: str, job: dict) -> dict:
    """Construeix l'informe públic d'un treball."""
    quality = job.get("quality")
    if not quality:
        return {
            "job_id": job_id,
            "status": job["status"],
            "summary": "Quality metrics are not available yet.",
        }

    change = quality["change"]
    restored = quality["restored"]
    return {
        "job_id": job_id,
        "status": job["status"],
        "profile": job.get("processing_profile"),
        "summary": (
            f"Restored output: {restored['width']}x{restored['height']}. "
            f"Sharpness gain: {_format_metric(change.get('sharpness_gain'), 'x')}. "
            f"Contrast gain: {_format_metric(change.get('contrast_gain'), 'x')}. "
            f"Estimated noise delta: {_format_metric(change.get('noise_delta'))}."
        ),
        "quality": quality,
    }


def _format_metric(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.2f}{suffix}"
