from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH, ensure_data_dirs


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditStore:
    """Capa d'auditoria local amb el mateix model que BigQuery."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        ensure_data_dirs()
        self.db_path = db_path
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    final_path TEXT,
                    status TEXT NOT NULL,
                    colorize INTEGER NOT NULL DEFAULT 0,
                    processing_profile TEXT NOT NULL DEFAULT 'quality',
                    media_type TEXT NOT NULL DEFAULT 'video',
                    color_mode TEXT NOT NULL DEFAULT 'none',
                    color_style TEXT NOT NULL DEFAULT 'historical_natural',
                    model_name TEXT,
                    model_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS scene_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    input_path TEXT,
                    output_path TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS art_audits (
                    id TEXT PRIMARY KEY,
                    reference_path TEXT NOT NULL,
                    current_path TEXT NOT NULL,
                    heatmap_path TEXT,
                    report_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "processing_profile" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN processing_profile TEXT NOT NULL DEFAULT 'quality'"
                )
            migrations = {
                "media_type": "ALTER TABLE jobs ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video'",
                "color_mode": "ALTER TABLE jobs ADD COLUMN color_mode TEXT NOT NULL DEFAULT 'none'",
                "color_style": (
                    "ALTER TABLE jobs ADD COLUMN color_style TEXT NOT NULL DEFAULT 'historical_natural'"
                ),
                "model_name": "ALTER TABLE jobs ADD COLUMN model_name TEXT",
                "model_version": "ALTER TABLE jobs ADD COLUMN model_version TEXT",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    conn.execute(statement)

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
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, filename, original_path, status, colorize, processing_profile, media_type,
                    color_mode, color_style, model_name, model_version, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    filename,
                    str(original_path),
                    int(colorize),
                    processing_profile,
                    media_type,
                    color_mode,
                    color_style,
                    model_name,
                    model_version,
                    now,
                    now,
                ),
            )
        self.event(
            job_id,
            "system",
            "job_created",
            f"{media_type.capitalize()} job created",
            {
                "colorize": colorize,
                "processing_profile": processing_profile,
                "media_type": media_type,
                "color_mode": color_mode,
                "color_style": color_style,
                "model_name": model_name,
                "model_version": model_version,
            },
        )

    def update_job(
        self,
        job_id: str,
        status: str,
        *,
        final_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, final_path = COALESCE(?, final_path), error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, str(final_path) if final_path else None, error, utc_now(), job_id),
            )

    def start_step(
        self,
        job_id: str,
        scene_id: str,
        step_name: str,
        input_path: Path | None,
        details: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scene_steps
                    (job_id, scene_id, step_name, input_path, status, started_at, details_json)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    job_id,
                    scene_id,
                    step_name,
                    str(input_path) if input_path else None,
                    utc_now(),
                    json.dumps(details or {}),
                ),
            )
            return int(cur.lastrowid)

    def finish_step(
        self,
        step_id: int,
        status: str,
        *,
        output_path: Path | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scene_steps
                SET status = ?, output_path = ?, finished_at = ?, details_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    str(output_path) if output_path else None,
                    utc_now(),
                    json.dumps(details or {}),
                    step_id,
                ),
            )

    def metric(self, job_id: str, name: str, value: float | None, details: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO metrics (job_id, metric_name, metric_value, details_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, name, value, json.dumps(details or {}), utc_now()),
            )

    def event(
        self,
        job_id: str | None,
        actor: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (job_id, actor, event_type, message, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, actor, event_type, message, json.dumps(details or {}), utc_now()),
            )

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            return [self._add_progress(conn, dict(row)) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            job = dict(row)
            job["steps"] = [
                self._add_step_duration(dict(step))
                for step in conn.execute(
                    "SELECT * FROM scene_steps WHERE job_id = ? ORDER BY id ASC", (job_id,)
                ).fetchall()
            ]
            self._add_progress(conn, job, steps=job["steps"])
            job["metrics"] = [
                dict(metric)
                for metric in conn.execute(
                    "SELECT * FROM metrics WHERE job_id = ? ORDER BY id ASC", (job_id,)
                ).fetchall()
            ]
            quality_rows = [
                metric for metric in job["metrics"] if metric["metric_name"] == "quality_summary"
            ]
            job["quality"] = json.loads(quality_rows[-1]["details_json"]) if quality_rows else None
            job["events"] = [
                dict(event)
                for event in conn.execute(
                    "SELECT * FROM audit_events WHERE job_id = ? ORDER BY id ASC", (job_id,)
                ).fetchall()
            ]
            return job

    @staticmethod
    def _add_progress(
        conn: sqlite3.Connection,
        job: dict[str, Any],
        *,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if steps is None:
            steps = [
                dict(step)
                for step in conn.execute(
                    "SELECT * FROM scene_steps WHERE job_id = ? ORDER BY id ASC",
                    (job["id"],),
                ).fetchall()
            ]
        running = next((step for step in reversed(steps) if step["status"] == "running"), None)
        latest = steps[-1] if steps else None
        job["progress"] = {
            "completed_steps": sum(step["status"] == "completed" for step in steps),
            "recorded_steps": len(steps),
            "current_step": (running or latest or {}).get("step_name"),
        }
        return job

    @staticmethod
    def _add_step_duration(step: dict[str, Any]) -> dict[str, Any]:
        started_at = step.get("started_at")
        finished_at = step.get("finished_at")
        if not started_at:
            step["duration_seconds"] = None
            return step
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            if finished_at
            else datetime.now(timezone.utc)
        )
        step["duration_seconds"] = round(max(0.0, (end - start).total_seconds()), 3)
        return step

    def save_art_audit(
        self,
        audit_id: str,
        reference_path: Path,
        current_path: Path,
        heatmap_path: Path | None,
        report: dict[str, Any],
        status: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO art_audits
                    (id, reference_path, current_path, heatmap_path, report_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    str(reference_path),
                    str(current_path),
                    str(heatmap_path) if heatmap_path else None,
                    json.dumps(report),
                    status,
                    utc_now(),
                ),
            )
        self.event(audit_id, "system", "art_audit_created", "Artwork visual audit completed", report)
