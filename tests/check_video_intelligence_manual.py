from __future__ import annotations

import sys
from pathlib import Path

# Permet importar app.* des de tests/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.bigquery_audit import list_jobs_from_bigquery
from app.video_intelligence import (
    list_video_shots_from_bigquery,
    safe_analyze_and_record_shots,
)


def main() -> None:
    """
    Prova manual de Google Cloud Video Intelligence API.

    Aquesta prova busca un job real, analitza shots i registra el resultat.
    """

    print("Buscando último job en BigQuery...")

    jobs = list_jobs_from_bigquery(limit=10)

    # Evitem el job manual de prova.
    real_jobs = [
        job for job in jobs
        if job.get("job_id") != "manual_bigquery_check"
    ]

    if not real_jobs:
        raise RuntimeError("No hay jobs reales en BigQuery para analizar.")

    job = real_jobs[0]

    job_id = job["job_id"]
    source_uri = job.get("original_uri")

    if not source_uri:
        raise RuntimeError(f"El job {job_id} no tiene original_uri.")

    print(f"Job seleccionado: {job_id}")
    print(f"Vídeo a analizar: {source_uri}")
    print()
    print("Llamando a Google Cloud Video Intelligence API...")
    print("Esto puede tardar unos minutos si el vídeo no es muy corto.")
    print()

    shots = safe_analyze_and_record_shots(
        job_id=job_id,
        source_uri=source_uri,
        timeout_seconds=900,
    )

    print()
    print(f"Shots detectados por la API: {len(shots)}")

    for shot in shots:
        print(
            f"- {shot['shot_id']}: "
            f"{shot['start_time_seconds']:.2f}s → "
            f"{shot['end_time_seconds']:.2f}s "
            f"({shot['duration_seconds']:.2f}s)"
        )

    print()
    print("Comprobando filas guardadas en BigQuery.video_shots...")

    stored_shots = list_video_shots_from_bigquery(job_id)

    print(f"Filas encontradas en BigQuery.video_shots: {len(stored_shots)}")

    print()
    print("Prueba Video Intelligence completada.")
    print("Puedes comprobarlo en BigQuery con:")
    print()
    print(
        "SELECT *\n"
        "FROM `encoded-ensign-496217-u4.refilm_audit.video_shots`\n"
        f"WHERE job_id = '{job_id}'\n"
        "ORDER BY start_time_seconds;"
    )


if __name__ == "__main__":
    main()
