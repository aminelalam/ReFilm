from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Permet importar app.bigquery_audit des de tests/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.bigquery_audit import (
    upsert_job_file_row,
    upsert_job_row,
    upsert_processing_step_row,
)


def main() -> None:
    """
    Prova manual de BigQuery.

    Aquest fitxer NO és un test automàtic de pytest.
    Serveix per comprovar que Python pot escriure a les taules:

    - refilm_audit.jobs
    - refilm_audit.job_files
    - refilm_audit.processing_steps
    """

    now = datetime.now(timezone.utc)

    job_id = "manual_bigquery_check"

    print("Probando inserción/actualización en BigQuery...")

    # Taula jobs.
    upsert_job_row(
        job_id=job_id,
        filename="manual_test_video.mp4",
        status="manual_test_ok",
        colorize=False,
        processing_profile="quality",
        media_type="video",
        color_mode="none",
        color_style="historical_natural",
        model_name=None,
        model_version=None,
        created_at=now,
        updated_at=now,
        original_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_test_video.mp4",
        final_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_restored.mp4",
        comparison_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_comparison.mp4",
        audit_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_audit.json",
        error=None,
        details_json='{"source": "check_bigquery_manual.py", "status": "ok"}',
    )

    print("Fila insertada/actualizada en tabla jobs.")

    # Taula job_files.
    upsert_job_file_row(
        job_id=job_id,
        category="manual_test",
        filename="manual_test_video.mp4",
        gcs_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_test_video.mp4",
        content_type="video/mp4",
        size_bytes=12345,
        created_at=now,
    )

    upsert_job_file_row(
        job_id=job_id,
        category="manual_test",
        filename="manual_audit.json",
        gcs_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_audit.json",
        content_type="application/json",
        size_bytes=456,
        created_at=now,
    )

    print("Filas insertadas/actualizadas en tabla job_files.")

    # Taula processing_steps.
    upsert_processing_step_row(
        job_id=job_id,
        scene_id="scene_000",
        step_name="manual_bigquery_step",
        status="completed",
        input_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_test_video.mp4",
        output_uri="gs://refilm-sm-alfonso-2026/pruebas/manual_restored.mp4",
        started_at=now,
        finished_at=now,
        details_json='{"message": "Paso manual registrado correctamente"}',
    )

    print("Fila insertada/actualizada en tabla processing_steps.")

    print()
    print("Prueba BigQuery completada correctamente.")
    print("Ahora puedes revisar estas tablas en BigQuery:")
    print("- refilm_audit.jobs")
    print("- refilm_audit.job_files")
    print("- refilm_audit.processing_steps")
    print()
    print(f"Busca el job_id: {job_id}")


if __name__ == "__main__":
    main()
