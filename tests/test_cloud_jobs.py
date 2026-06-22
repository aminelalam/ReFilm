from __future__ import annotations

from types import SimpleNamespace
from importlib import import_module
from pathlib import Path

from app import cloud_jobs


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"name": "operations/test"}


class FakeSession:
    def __init__(self, credentials: object) -> None:
        self.credentials = credentials
        self.request: tuple[str, dict, int] | None = None

    def post(self, url: str, *, json: dict, timeout: int) -> FakeResponse:
        self.request = (url, json, timeout)
        return FakeResponse()


def test_dispatch_restoration_job_uses_cloud_run_override(monkeypatch) -> None:
    session = FakeSession(object())
    fake_auth = SimpleNamespace(default=lambda scopes: (object(), "project"))
    monkeypatch.setattr(cloud_jobs, "_load_google_auth", lambda: (fake_auth, lambda credentials: session))

    result = cloud_jobs.dispatch_restoration_job(
        job_id="job-1",
        input_uri="gs://bucket/original.mp4",
        output_uri="gs://bucket/restored.mp4",
        comparison_uri="gs://bucket/comparison.mp4",
        profile="quality",
        colorize=True,
    )

    assert result == {"name": "operations/test"}
    assert session.request is not None
    url, payload, timeout = session.request
    assert url.endswith("/jobs/refilm-render-gpu:run")
    assert timeout == 30
    args = payload["overrides"]["containerOverrides"][0]["args"]
    assert args == [
        "--job-id",
        "job-1",
        "--input-uri",
        "gs://bucket/original.mp4",
        "--output-uri",
        "gs://bucket/restored.mp4",
        "--comparison-uri",
        "gs://bucket/comparison.mp4",
        "--profile",
        "quality",
        "--colorize",
        "true",
        "--color-mode",
        "ai_natural",
        "--color-style",
        "historical_natural",
    ]


def test_dispatch_restoration_job_reports_missing_google_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        cloud_jobs,
        "_load_google_auth",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Google Cloud Run Jobs support is not installed. Install google-auth to enable it.")
        ),
    )

    try:
        cloud_jobs.dispatch_restoration_job(
            job_id="job-1",
            input_uri="gs://bucket/original.mp4",
            output_uri="gs://bucket/restored.mp4",
            comparison_uri="gs://bucket/comparison.mp4",
            profile="quality",
            colorize=False,
        )
    except RuntimeError as exc:
        assert "google-auth" in str(exc)
    else:
        raise AssertionError("Expected missing google-auth to raise RuntimeError")


def test_web_dispatch_requires_gcs_and_bigquery_before_cloud_run(monkeypatch) -> None:
    main = import_module("app.main")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(main, "sync_job_to_gcs", lambda job_id: calls.append(("gcs", job_id)))
    monkeypatch.setattr(
        main,
        "sync_job_metadata_to_bigquery",
        lambda job_id: calls.append(("bigquery", job_id)),
    )
    monkeypatch.setattr(main, "safe_sync_job_to_gcs", lambda job_id: [])
    monkeypatch.setattr(main.audit_store, "update_job", lambda job_id, status: calls.append(("status", status)))
    monkeypatch.setattr(main.audit_store, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cloud_jobs,
        "dispatch_restoration_job",
        lambda **kwargs: calls.append(("cloud_run", kwargs["job_id"])) or {"name": "operations/test"},
    )

    main._dispatch_cloud_restoration("job-2", Path("clip.mp4"), False, "quality")

    assert calls[:4] == [
        ("gcs", "job-2"),
        ("bigquery", "job-2"),
        ("status", "queued"),
        ("bigquery", "job-2"),
    ]
    assert calls[4] == ("cloud_run", "job-2")
