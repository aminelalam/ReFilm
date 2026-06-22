from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

try:
    from google.cloud import bigquery
    from google.cloud import videointelligence
except ModuleNotFoundError:  # Opcional en instal·lacions només locals.
    bigquery = None  # type: ignore[assignment]
    videointelligence = None  # type: ignore[assignment]

from app.bigquery_audit import (
    BQ_LOCATION,
    get_client as get_bigquery_client,
    table_id,
    upsert_processing_step_row,
)


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _duration_to_seconds(duration: Any) -> float:
    """
    Converteix un Duration protobuf a segons.
    """
    seconds = float(getattr(duration, "seconds", 0) or 0)
    nanos = float(getattr(duration, "nanos", 0) or 0)

    # Algunes versions exposen microseconds.
    microseconds = float(getattr(duration, "microseconds", 0) or 0)

    return seconds + nanos / 1_000_000_000 + microseconds / 1_000_000


def analyze_shot_changes_from_gcs(
    source_uri: str,
    timeout_seconds: int = 600,
) -> list[dict[str, Any]]:
    """
    Analitza un vídeo de Cloud Storage amb Video Intelligence.

    source_uri ha de tenir format:
        gs://bucket/ruta/video.mp4

    Retorna una llista de shots/escenes:
        [
          {
            "shot_id": "shot_000",
            "start_time_seconds": 0.0,
            "end_time_seconds": 4.2,
            "duration_seconds": 4.2,
            "source_uri": "gs://..."
          }
        ]
    """
    if videointelligence is None:
        raise RuntimeError("Google Video Intelligence support is not installed.")

    if not source_uri.startswith("gs://"):
        raise ValueError(f"Video Intelligence necesita una URI gs:// válida: {source_uri}")

    client = videointelligence.VideoIntelligenceServiceClient()

    features = [
        videointelligence.Feature.SHOT_CHANGE_DETECTION,
    ]

    operation = client.annotate_video(
        request={
            "input_uri": source_uri,
            "features": features,
        }
    )

    print(f"[Video Intelligence] Analizando cambios de plano: {source_uri}")

    result = operation.result(timeout=timeout_seconds)

    if not result.annotation_results:
        return []

    annotation_result = result.annotation_results[0]

    shots: list[dict[str, Any]] = []

    for index, shot in enumerate(annotation_result.shot_annotations):
        start_seconds = _duration_to_seconds(shot.start_time_offset)
        end_seconds = _duration_to_seconds(shot.end_time_offset)
        duration_seconds = max(0.0, end_seconds - start_seconds)

        shots.append(
            {
                "shot_id": f"shot_{index:03d}",
                "start_time_seconds": start_seconds,
                "end_time_seconds": end_seconds,
                "duration_seconds": duration_seconds,
                "source_uri": source_uri,
            }
        )

    print(f"[Video Intelligence] Shots detectados: {len(shots)}")

    return shots


def upsert_video_shot_row(
    *,
    job_id: str,
    shot_id: str,
    start_time_seconds: float | None,
    end_time_seconds: float | None,
    duration_seconds: float | None,
    source_uri: str | None,
    created_at: datetime | None = None,
) -> None:
    """
    Insereix o actualitza una fila a refilm_audit.video_shots.
    Usem job_id + shot_id com a identificador lògic.
    """
    query = f"""
    MERGE `{table_id('video_shots')}` T
    USING (
      SELECT
        @job_id AS job_id,
        @shot_id AS shot_id,
        @start_time_seconds AS start_time_seconds,
        @end_time_seconds AS end_time_seconds,
        @duration_seconds AS duration_seconds,
        @source_uri AS source_uri,
        @created_at AS created_at
    ) S
    ON T.job_id = S.job_id
       AND T.shot_id = S.shot_id
    WHEN MATCHED THEN UPDATE SET
      start_time_seconds = S.start_time_seconds,
      end_time_seconds = S.end_time_seconds,
      duration_seconds = S.duration_seconds,
      source_uri = S.source_uri,
      created_at = S.created_at
    WHEN NOT MATCHED THEN INSERT (
      job_id,
      shot_id,
      start_time_seconds,
      end_time_seconds,
      duration_seconds,
      source_uri,
      created_at
    ) VALUES (
      S.job_id,
      S.shot_id,
      S.start_time_seconds,
      S.end_time_seconds,
      S.duration_seconds,
      S.source_uri,
      S.created_at
    )
    """

    client = get_bigquery_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("shot_id", "STRING", shot_id),
            bigquery.ScalarQueryParameter("start_time_seconds", "FLOAT64", start_time_seconds),
            bigquery.ScalarQueryParameter("end_time_seconds", "FLOAT64", end_time_seconds),
            bigquery.ScalarQueryParameter("duration_seconds", "FLOAT64", duration_seconds),
            bigquery.ScalarQueryParameter("source_uri", "STRING", source_uri),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", created_at or utc_now_dt()),
        ]
    )

    client.query(query, job_config=job_config, location=BQ_LOCATION).result()


def record_video_shots_to_bigquery(
    job_id: str,
    source_uri: str,
    shots: list[dict[str, Any]],
) -> int:
    """
    Desa a BigQuery els shots detectats per Video Intelligence.
    """
    created_at = utc_now_dt()

    for shot in shots:
        upsert_video_shot_row(
            job_id=job_id,
            shot_id=shot["shot_id"],
            start_time_seconds=shot.get("start_time_seconds"),
            end_time_seconds=shot.get("end_time_seconds"),
            duration_seconds=shot.get("duration_seconds"),
            source_uri=source_uri,
            created_at=created_at,
        )

    return len(shots)


def analyze_and_record_shots(
    job_id: str,
    source_uri: str,
    timeout_seconds: int = 600,
) -> list[dict[str, Any]]:
    """
    Executa Video Intelligence i registra el resultat a BigQuery.
    """
    started_at = utc_now_dt()

    shots = analyze_shot_changes_from_gcs(
        source_uri=source_uri,
        timeout_seconds=timeout_seconds,
    )

    inserted_count = record_video_shots_to_bigquery(
        job_id=job_id,
        source_uri=source_uri,
        shots=shots,
    )

    finished_at = utc_now_dt()

    upsert_processing_step_row(
        job_id=job_id,
        scene_id="video",
        step_name="video_intelligence_shot_detection",
        status="completed",
        input_uri=source_uri,
        output_uri=None,
        started_at=started_at,
        finished_at=finished_at,
        details_json=json.dumps(
            {
                "api": "Google Cloud Video Intelligence",
                "feature": "SHOT_CHANGE_DETECTION",
                "shots_detected": inserted_count,
            },
            ensure_ascii=False,
        ),
    )

    print(
        f"[Video Intelligence] Job {job_id}: "
        f"{inserted_count} shots registrados en BigQuery."
    )

    return shots


def safe_analyze_and_record_shots(
    job_id: str,
    source_uri: str,
    timeout_seconds: int = 600,
) -> list[dict[str, Any]]:
    """
    Versió segura: si Video Intelligence falla, no trenca el pipeline principal.
    """
    try:
        return analyze_and_record_shots(
            job_id=job_id,
            source_uri=source_uri,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        print(f"[Video Intelligence] Error analizando job {job_id}: {exc}")

        try:
            upsert_processing_step_row(
                job_id=job_id,
                scene_id="video",
                step_name="video_intelligence_shot_detection",
                status="error",
                input_uri=source_uri,
                output_uri=None,
                started_at=utc_now_dt(),
                finished_at=utc_now_dt(),
                details_json=json.dumps(
                    {
                        "api": "Google Cloud Video Intelligence",
                        "feature": "SHOT_CHANGE_DETECTION",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as inner_exc:
            print(f"[Video Intelligence] Error registrando fallo en BigQuery: {inner_exc}")

        return []


def list_video_shots_from_bigquery(job_id: str) -> list[dict[str, Any]]:
    """
    Llista els shots detectats per a un job.
    """
    query = f"""
    SELECT
      job_id,
      shot_id,
      start_time_seconds,
      end_time_seconds,
      duration_seconds,
      source_uri,
      created_at
    FROM `{table_id('video_shots')}`
    WHERE job_id = @job_id
    ORDER BY start_time_seconds ASC
    """

    client = get_bigquery_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        ]
    )

    rows = client.query(query, job_config=job_config, location=BQ_LOCATION).result()

    return [dict(row.items()) for row in rows]
