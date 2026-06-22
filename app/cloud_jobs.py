from __future__ import annotations

from typing import Any

from app.cloud_storage import PROJECT_ID
from app.config import CLOUD_RUN_AI_JOB, CLOUD_RUN_JOB_LOCATION, CLOUD_RUN_RENDER_JOB


def _load_google_auth() -> tuple[Any, Any]:
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Google Cloud Run Jobs support is not installed. Install google-auth to enable it."
        ) from exc
    return google.auth, AuthorizedSession


def dispatch_restoration_job(
    *,
    job_id: str,
    input_uri: str,
    output_uri: str,
    comparison_uri: str,
    profile: str,
    colorize: bool,
    color_mode: str | None = None,
    color_style: str = "historical_natural",
) -> dict[str, Any]:
    """Arrenca un Cloud Run Job amb els arguments del vídeo."""
    google_auth, authorized_session = _load_google_auth()

    resolved_color_mode = color_mode or ("ai_natural" if colorize else "none")
    cloud_job = CLOUD_RUN_AI_JOB if profile == "ai_realesrgan" else CLOUD_RUN_RENDER_JOB
    name = f"projects/{PROJECT_ID}/locations/{CLOUD_RUN_JOB_LOCATION}/jobs/{cloud_job}"
    credentials, _ = google_auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = authorized_session(credentials)
    response = session.post(
        f"https://run.googleapis.com/v2/{name}:run",
        json={
            "overrides": {
                "containerOverrides": [
                    {
                        "args": [
                            "--job-id",
                            job_id,
                            "--input-uri",
                            input_uri,
                            "--output-uri",
                            output_uri,
                            "--comparison-uri",
                            comparison_uri,
                            "--profile",
                            profile,
                            "--colorize",
                            str(colorize).lower(),
                            "--color-mode",
                            resolved_color_mode,
                            "--color-style",
                            color_style,
                        ]
                    }
                ],
                "timeout": "86400s",
            }
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
