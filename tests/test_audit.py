from pathlib import Path

from app.audit import AuditStore


def test_audit_store_records_job_steps_metrics_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = AuditStore(db_path)
    original = tmp_path / "original.mp4"
    final = tmp_path / "final.mp4"
    original.write_bytes(b"original")
    final.write_bytes(b"final")

    store.create_job("job-1", "clip.mp4", original, colorize=True)
    step_id = store.start_step("job-1", "scene_000", "deartifact", original)
    store.finish_step(step_id, "completed", output_path=final, details={"ok": True})
    store.metric("job-1", "elapsed_seconds", 1.25)
    store.metric(
        "job-1",
        "quality_summary",
        None,
        {"restored": {"width": 800, "height": 600}, "change": {"sharpness_gain": 1.2}},
    )
    store.update_job("job-1", "restored", final_path=final)

    job = store.get_job("job-1")

    assert job is not None
    assert job["status"] == "restored"
    assert job["colorize"] == 1
    assert len(job["steps"]) == 1
    assert len(job["metrics"]) == 2
    assert job["quality"]["restored"]["width"] == 800
    assert job["steps"][0]["duration_seconds"] is not None
    assert job["progress"] == {
        "completed_steps": 1,
        "recorded_steps": 1,
        "current_step": "deartifact",
    }
    assert store.list_jobs()[0]["progress"]["completed_steps"] == 1
    assert len(job["events"]) >= 1
