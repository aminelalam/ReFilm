from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from google.cloud import bigquery
except ModuleNotFoundError:  # Opcional en instal·lacions només locals.
    bigquery = None  # type: ignore[assignment]

from app.audit import AuditStore
from app.cloud_storage import BUCKET_NAME, PROJECT_ID
from app.config import AUDIT_DIR, DATA_DIR, FINAL_DIR


# Dataset i ubicació creats a Google Cloud Console.
DEFAULT_DATASET_ID = "refilm_audit"
DEFAULT_BQ_LOCATION = "europe-southwest1"

DATASET_ID = os.getenv("BQ_DATASET_ID", DEFAULT_DATASET_ID)
BQ_LOCATION = os.getenv("BQ_LOCATION", DEFAULT_BQ_LOCATION)

_client: bigquery.Client | None = None


def get_client() -> bigquery.Client:
    """
    Crea o reutilitza el client de BigQuery.

    Usa les credencials configurades amb:
        gcloud auth application-default login
    """
    global _client

    if bigquery is None:
        raise RuntimeError("Google BigQuery support is not installed.")

    if _client is None:
        _client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)

    return _client


def table_id(table_name: str) -> str:
    """
    Retorna l'identificador complet d'una taula de BigQuery.
    Exemple:
        encoded-ensign-496217-u4.refilm_audit.jobs
    """
    return f"{PROJECT_ID}.{DATASET_ID}.{table_name}"


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    """
    Converteix dates SQLite/ISO a datetime per a BigQuery.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return None


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def local_path_to_gcs_uri(local_path: str | Path | None) -> str | None:
    """
    Converteix una ruta local dins de data/ a la ruta equivalent de GCS.

    Exemple:
        data/final/job123/restored.mp4
        -> gs://refilm-sm-alfonso-2026/final/job123/restored.mp4
    """
    if not local_path:
        return None

    path = Path(local_path)

    try:
        relative_path = path.resolve().relative_to(DATA_DIR.resolve()).as_posix()
    except ValueError:
        return None

    return f"gs://{BUCKET_NAME}/{relative_path}"


def default_comparison_uri(job_id: str, media_type: str = "video") -> str | None:
    filename = "comparison.jpg" if media_type == "image" else "comparison.mp4"
    path = FINAL_DIR / job_id / filename
    return local_path_to_gcs_uri(path) if path.exists() else None


def default_audit_uri(job_id: str) -> str | None:
    path = AUDIT_DIR / job_id / "audit.json"
    return local_path_to_gcs_uri(path) if path.exists() else None


def run_query(query: str, parameters: list[bigquery.ScalarQueryParameter]) -> None:
    client = get_client()
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    client.query(query, job_config=job_config, location=BQ_LOCATION).result()


def upsert_job_row(
    *,
    job_id: str,
    filename: str | None,
    status: str | None,
    colorize: bool | None,
    processing_profile: str | None,
    media_type: str | None,
    color_mode: str | None,
    color_style: str | None,
    model_name: str | None,
    model_version: str | None,
    created_at: datetime | None,
    updated_at: datetime | None,
    original_uri: str | None = None,
    final_uri: str | None = None,
    comparison_uri: str | None = None,
    audit_uri: str | None = None,
    error: str | None = None,
    details_json: str | None = None,
) -> None:
    """
    Insereix o actualitza una fila a refilm_audit.jobs.
    BigQuery no imposa claus primàries; per això fem MERGE amb job_id.
    """
    query = f"""
    MERGE `{table_id('jobs')}` T
    USING (
      SELECT
        @job_id AS job_id,
        @filename AS filename,
        @status AS status,
        @colorize AS colorize,
        @processing_profile AS processing_profile,
        @media_type AS media_type,
        @color_mode AS color_mode,
        @color_style AS color_style,
        @model_name AS model_name,
        @model_version AS model_version,
        @created_at AS created_at,
        @updated_at AS updated_at,
        @original_uri AS original_uri,
        @final_uri AS final_uri,
        @comparison_uri AS comparison_uri,
        @audit_uri AS audit_uri,
        @error AS error,
        @details_json AS details_json
    ) S
    ON T.job_id = S.job_id
    WHEN MATCHED THEN UPDATE SET
      filename = S.filename,
      status = S.status,
      colorize = S.colorize,
      processing_profile = S.processing_profile,
      media_type = S.media_type,
      color_mode = S.color_mode,
      color_style = S.color_style,
      model_name = S.model_name,
      model_version = S.model_version,
      created_at = S.created_at,
      updated_at = S.updated_at,
      original_uri = S.original_uri,
      final_uri = S.final_uri,
      comparison_uri = S.comparison_uri,
      audit_uri = S.audit_uri,
      error = S.error,
      details_json = S.details_json
    WHEN NOT MATCHED THEN INSERT (
      job_id,
      filename,
      status,
      colorize,
      processing_profile,
      media_type,
      color_mode,
      color_style,
      model_name,
      model_version,
      created_at,
      updated_at,
      original_uri,
      final_uri,
      comparison_uri,
      audit_uri,
      error,
      details_json
    ) VALUES (
      S.job_id,
      S.filename,
      S.status,
      S.colorize,
      S.processing_profile,
      S.media_type,
      S.color_mode,
      S.color_style,
      S.model_name,
      S.model_version,
      S.created_at,
      S.updated_at,
      S.original_uri,
      S.final_uri,
      S.comparison_uri,
      S.audit_uri,
      S.error,
      S.details_json
    )
    """

    run_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("filename", "STRING", filename),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("colorize", "BOOL", colorize),
            bigquery.ScalarQueryParameter("processing_profile", "STRING", processing_profile),
            bigquery.ScalarQueryParameter("media_type", "STRING", media_type),
            bigquery.ScalarQueryParameter("color_mode", "STRING", color_mode),
            bigquery.ScalarQueryParameter("color_style", "STRING", color_style),
            bigquery.ScalarQueryParameter("model_name", "STRING", model_name),
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", created_at),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", updated_at),
            bigquery.ScalarQueryParameter("original_uri", "STRING", original_uri),
            bigquery.ScalarQueryParameter("final_uri", "STRING", final_uri),
            bigquery.ScalarQueryParameter("comparison_uri", "STRING", comparison_uri),
            bigquery.ScalarQueryParameter("audit_uri", "STRING", audit_uri),
            bigquery.ScalarQueryParameter("error", "STRING", error),
            bigquery.ScalarQueryParameter("details_json", "STRING", details_json),
        ],
    )


def upsert_job_from_audit_job(job: dict[str, Any]) -> None:
    """
    Converteix un job de SQLite/AuditStore al format de BigQuery.
    """
    job_id = str(job["id"])

    upsert_job_row(
        job_id=job_id,
        filename=job.get("filename"),
        status=job.get("status"),
        colorize=bool(job.get("colorize")),
        processing_profile=job.get("processing_profile"),
        media_type=job.get("media_type"),
        color_mode=job.get("color_mode"),
        color_style=job.get("color_style"),
        model_name=job.get("model_name"),
        model_version=job.get("model_version"),
        created_at=parse_timestamp(job.get("created_at")),
        updated_at=parse_timestamp(job.get("updated_at")),
        original_uri=local_path_to_gcs_uri(job.get("original_path")),
        final_uri=local_path_to_gcs_uri(job.get("final_path")),
        comparison_uri=default_comparison_uri(job_id, str(job.get("media_type") or "video")),
        audit_uri=default_audit_uri(job_id),
        error=job.get("error"),
        details_json=json_dumps(
            {
                "metrics_count": len(job.get("metrics", [])),
                "events_count": len(job.get("events", [])),
                "steps_count": len(job.get("steps", [])),
            }
        ),
    )


def upsert_job_file_row(
    *,
    job_id: str,
    category: str | None,
    filename: str | None,
    gcs_uri: str,
    content_type: str | None,
    size_bytes: int | None,
    created_at: datetime | None = None,
) -> None:
    """
    Insereix o actualitza una fila a refilm_audit.job_files.
    Usem gcs_uri com a identificador natural del fitxer.
    """
    query = f"""
    MERGE `{table_id('job_files')}` T
    USING (
      SELECT
        @job_id AS job_id,
        @category AS category,
        @filename AS filename,
        @gcs_uri AS gcs_uri,
        @content_type AS content_type,
        @size_bytes AS size_bytes,
        @created_at AS created_at
    ) S
    ON T.gcs_uri = S.gcs_uri
    WHEN MATCHED THEN UPDATE SET
      job_id = S.job_id,
      category = S.category,
      filename = S.filename,
      content_type = S.content_type,
      size_bytes = S.size_bytes,
      created_at = S.created_at
    WHEN NOT MATCHED THEN INSERT (
      job_id,
      category,
      filename,
      gcs_uri,
      content_type,
      size_bytes,
      created_at
    ) VALUES (
      S.job_id,
      S.category,
      S.filename,
      S.gcs_uri,
      S.content_type,
      S.size_bytes,
      S.created_at
    )
    """

    run_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
            bigquery.ScalarQueryParameter("filename", "STRING", filename),
            bigquery.ScalarQueryParameter("gcs_uri", "STRING", gcs_uri),
            bigquery.ScalarQueryParameter("content_type", "STRING", content_type),
            bigquery.ScalarQueryParameter("size_bytes", "INT64", size_bytes),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", created_at or utc_now_dt()),
        ],
    )


def record_uploaded_file_from_path(job_id: str, local_path: str | Path, gcs_uri: str) -> None:
    """
    Registra a BigQuery un fitxer acabat de pujar a Cloud Storage.
    """
    path = Path(local_path)
    content_type, _ = mimetypes.guess_type(str(path))

    try:
        relative_parts = path.resolve().relative_to(DATA_DIR.resolve()).parts
        category = relative_parts[0] if relative_parts else None
    except ValueError:
        category = None

    upsert_job_file_row(
        job_id=job_id,
        category=category,
        filename=path.name,
        gcs_uri=gcs_uri,
        content_type=content_type,
        size_bytes=path.stat().st_size if path.exists() else None,
        created_at=utc_now_dt(),
    )


def upsert_processing_step_row(
    *,
    job_id: str,
    scene_id: str | None,
    step_name: str,
    status: str | None,
    input_uri: str | None,
    output_uri: str | None,
    started_at: datetime | None,
    finished_at: datetime | None,
    details_json: str | None,
) -> None:
    """
    Insereix o actualitza una etapa a refilm_audit.processing_steps.
    """
    query = f"""
    MERGE `{table_id('processing_steps')}` T
    USING (
      SELECT
        @job_id AS job_id,
        @scene_id AS scene_id,
        @step_name AS step_name,
        @status AS status,
        @input_uri AS input_uri,
        @output_uri AS output_uri,
        @started_at AS started_at,
        @finished_at AS finished_at,
        @details_json AS details_json
    ) S
    ON T.job_id = S.job_id
       AND T.scene_id = S.scene_id
       AND T.step_name = S.step_name
       AND T.started_at = S.started_at
    WHEN MATCHED THEN UPDATE SET
      status = S.status,
      input_uri = S.input_uri,
      output_uri = S.output_uri,
      finished_at = S.finished_at,
      details_json = S.details_json
    WHEN NOT MATCHED THEN INSERT (
      job_id,
      scene_id,
      step_name,
      status,
      input_uri,
      output_uri,
      started_at,
      finished_at,
      details_json
    ) VALUES (
      S.job_id,
      S.scene_id,
      S.step_name,
      S.status,
      S.input_uri,
      S.output_uri,
      S.started_at,
      S.finished_at,
      S.details_json
    )
    """

    run_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("scene_id", "STRING", scene_id),
            bigquery.ScalarQueryParameter("step_name", "STRING", step_name),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("details_json", "STRING", details_json),
        ],
    )


def upsert_processing_step_from_audit_step(step: dict[str, Any]) -> None:
    upsert_processing_step_row(
        job_id=step["job_id"],
        scene_id=step.get("scene_id"),
        step_name=step["step_name"],
        status=step.get("status"),
        input_uri=local_path_to_gcs_uri(step.get("input_path")),
        output_uri=local_path_to_gcs_uri(step.get("output_path")),
        started_at=parse_timestamp(step.get("started_at")),
        finished_at=parse_timestamp(step.get("finished_at")),
        details_json=step.get("details_json"),
    )


def upsert_metric_from_audit_metric(metric: dict[str, Any]) -> None:
    query = f"""
    MERGE `{table_id('metrics')}` T
    USING (
      SELECT
        @job_id AS job_id,
        @metric_name AS metric_name,
        @metric_value AS metric_value,
        @details_json AS details_json,
        @created_at AS created_at
    ) S
    ON T.job_id = S.job_id
       AND T.metric_name = S.metric_name
       AND T.created_at = S.created_at
    WHEN MATCHED THEN UPDATE SET
      metric_value = S.metric_value,
      details_json = S.details_json
    WHEN NOT MATCHED THEN INSERT (
      job_id,
      metric_name,
      metric_value,
      details_json,
      created_at
    ) VALUES (
      S.job_id,
      S.metric_name,
      S.metric_value,
      S.details_json,
      S.created_at
    )
    """
    run_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", metric["job_id"]),
            bigquery.ScalarQueryParameter("metric_name", "STRING", metric["metric_name"]),
            bigquery.ScalarQueryParameter("metric_value", "FLOAT64", metric.get("metric_value")),
            bigquery.ScalarQueryParameter("details_json", "STRING", metric.get("details_json")),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", parse_timestamp(metric.get("created_at"))),
        ],
    )


def sync_job_metadata_to_bigquery(job_id: str) -> dict[str, int]:
    """
    Llegeix l'estat local SQLite d'un job i el sincronitza amb BigQuery.
    """
    store = AuditStore()
    job = store.get_job(job_id)

    if job is None:
        raise ValueError(f"No existe el job local: {job_id}")

    upsert_job_from_audit_job(job)

    steps = job.get("steps", [])
    for step in steps:
        upsert_processing_step_from_audit_step(step)

    metrics = job.get("metrics", [])
    for metric in metrics:
        upsert_metric_from_audit_metric(metric)

    return {"jobs": 1, "processing_steps": len(steps), "metrics": len(metrics)}


def safe_record_uploaded_file_from_path(job_id: str, local_path: str | Path, gcs_uri: str) -> None:
    """
    Versió segura: si BigQuery falla, no trenca el flux principal.
    """
    try:
        record_uploaded_file_from_path(job_id, local_path, gcs_uri)
        print(f"[BigQuery] Archivo registrado: {gcs_uri}")
    except Exception as exc:
        print(f"[BigQuery] Error registrando archivo {gcs_uri}: {exc}")


def safe_sync_job_metadata_to_bigquery(job_id: str) -> dict[str, int]:
    """
    Versió segura: si BigQuery falla, no trenca el procés local ni GCS.
    """
    try:
        result = sync_job_metadata_to_bigquery(job_id)
        print(
            f"[BigQuery] Job {job_id}: "
            f"{result['jobs']} job, {result['processing_steps']} pasos y {result['metrics']} metricas sincronizados."
        )
        return result
    except Exception as exc:
        print(f"[BigQuery] Error sincronizando metadata del job {job_id}: {exc}")
        return {"jobs": 0, "processing_steps": 0, "metrics": 0}

def _row_to_api_job(row: bigquery.table.Row) -> dict[str, Any]:
    """
    Converteix una fila de BigQuery al format que espera el frontend.
    El frontend espera job.id; per això dupliquem job_id com a id.
    """
    data = dict(row.items())

    return {
        "id": data.get("job_id"),
        "job_id": data.get("job_id"),
        "filename": data.get("filename"),
        "status": data.get("status"),
        "colorize": data.get("colorize"),
        "processing_profile": data.get("processing_profile"),
        "media_type": data.get("media_type") or "video",
        "color_mode": data.get("color_mode"),
        "color_style": data.get("color_style"),
        "model_name": data.get("model_name"),
        "model_version": data.get("model_version"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "original_uri": data.get("original_uri"),
        "final_uri": data.get("final_uri"),
        "comparison_uri": data.get("comparison_uri"),
        "audit_uri": data.get("audit_uri"),
        "error": data.get("error"),
        "details_json": data.get("details_json"),
    }


def list_jobs_from_bigquery(limit: int = 50) -> list[dict[str, Any]]:
    """
    Llista treballs des de BigQuery.
    A Cloud Run substitueix SQLite per a /api/jobs.
    """
    query = f"""
    SELECT
      job_id,
      filename,
      status,
      colorize,
      processing_profile,
      media_type,
      color_mode,
      color_style,
      model_name,
      model_version,
      created_at,
      updated_at,
      original_uri,
      final_uri,
      comparison_uri,
      audit_uri,
      error,
      details_json
    FROM `{table_id('jobs')}`
    ORDER BY updated_at DESC
    LIMIT @limit
    """

    client = get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )

    rows = client.query(query, job_config=job_config, location=BQ_LOCATION).result()

    return [_row_to_api_job(row) for row in rows]


def get_job_from_bigquery(job_id: str) -> dict[str, Any] | None:
    """
    Obté un treball concret de BigQuery amb els seus passos.
    """
    query = f"""
    SELECT
      job_id,
      filename,
      status,
      colorize,
      processing_profile,
      media_type,
      color_mode,
      color_style,
      model_name,
      model_version,
      created_at,
      updated_at,
      original_uri,
      final_uri,
      comparison_uri,
      audit_uri,
      error,
      details_json
    FROM `{table_id('jobs')}`
    WHERE job_id = @job_id
    LIMIT 1
    """

    client = get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        ]
    )

    rows = list(client.query(query, job_config=job_config, location=BQ_LOCATION).result())

    if not rows:
        return None

    job = _row_to_api_job(rows[0])
    job["steps"] = list_processing_steps_from_bigquery(job_id)
    job["files"] = list_job_files_from_bigquery(job_id)
    job["metrics"] = list_metrics_from_bigquery(job_id)
    quality_rows = [metric for metric in job["metrics"] if metric["metric_name"] == "quality_summary"]
    job["quality"] = json.loads(quality_rows[-1]["details_json"]) if quality_rows else None

    return job


def list_job_files_from_bigquery(job_id: str) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      job_id,
      category,
      filename,
      gcs_uri,
      content_type,
      size_bytes,
      created_at
    FROM `{table_id('job_files')}`
    WHERE job_id = @job_id
    ORDER BY created_at ASC
    """

    client = get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        ]
    )

    rows = client.query(query, job_config=job_config, location=BQ_LOCATION).result()

    return [dict(row.items()) for row in rows]


def list_processing_steps_from_bigquery(job_id: str) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      job_id,
      scene_id,
      step_name,
      status,
      input_uri,
      output_uri,
      started_at,
      finished_at,
      details_json
    FROM `{table_id('processing_steps')}`
    WHERE job_id = @job_id
    ORDER BY started_at ASC
    """

    client = get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        ]
    )

    rows = client.query(query, job_config=job_config, location=BQ_LOCATION).result()

    return [dict(row.items()) for row in rows]


def list_metrics_from_bigquery(job_id: str) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      job_id,
      metric_name,
      metric_value,
      details_json,
      created_at
    FROM `{table_id('metrics')}`
    WHERE job_id = @job_id
    ORDER BY created_at ASC
    """
    client = get_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
    )
    rows = client.query(query, job_config=job_config, location=BQ_LOCATION).result()
    return [dict(row.items()) for row in rows]


def get_download_uri_for_job(job_id: str, kind: str) -> str | None:
    """
    Retorna la URI gs:// correcta per descarregar fitxers del job.
    Primer mira jobs i després job_files.
    """
    job = get_job_from_bigquery(job_id)

    if job is None:
        return None

    direct_columns = {
        "original": "original_uri",
        "final": "final_uri",
        "comparison": "comparison_uri",
        "audit": "audit_uri",
    }

    column = direct_columns.get(kind)
    if column:
        uri = job.get(column)
        if uri:
            return uri

    media_type = job.get("media_type") or "video"
    if media_type == "image":
        filename_by_kind = {
            "final": Path(job.get("final_uri") or "").name or "colorized.png",
            "restored": "restored.png",
            "colorized": "colorized.png",
            "comparison": "comparison.jpg",
            "audit": "audit.json",
        }
    else:
        filename_by_kind = {
            "final": "restored.mp4",
            "comparison": "comparison.mp4",
            "audit": "audit.json",
        }

    category_by_kind = {
        "original": "originals",
        "final": "final",
        "restored": "final",
        "colorized": "final",
        "comparison": "final",
        "audit": "audit",
    }

    files = list_job_files_from_bigquery(job_id)

    if kind == "original":
        for item in files:
            if item.get("category") == "originals":
                return item.get("gcs_uri")

    expected_filename = filename_by_kind.get(kind)
    expected_category = category_by_kind.get(kind)

    for item in files:
        if item.get("category") == expected_category and item.get("filename") == expected_filename:
            return item.get("gcs_uri")

    return None
