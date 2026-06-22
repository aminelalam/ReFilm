from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from google.cloud import bigquery


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "encoded-ensign-496217-u4")
BQ_DATASET_ID = os.getenv("BQ_DATASET_ID", "refilm_audit")
BQ_LOCATION = os.getenv("BQ_LOCATION", "europe-southwest1")


def table_id(table_name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET_ID}.{table_name}"


def bq_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)


def get_latest_restored_job() -> tuple[str, str]:
    query = f"""
    SELECT job_id, original_uri
    FROM `{table_id("jobs")}`
    WHERE status = 'restored'
      AND original_uri IS NOT NULL
    ORDER BY updated_at DESC
    LIMIT 1
    """

    rows = list(bq_client().query(query, location=BQ_LOCATION).result())

    if not rows:
        raise RuntimeError("No he encontrado ningún job restaurado con original_uri en BigQuery.")

    return rows[0]["job_id"], rows[0]["original_uri"]


def run_command(command: list[str]) -> None:
    print()
    print("=" * 90)
    print("[RUN]", " ".join(command))
    print("=" * 90)
    print()

    subprocess.run(command, check=True)


def validate_existing_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo necesario: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline avanzado local: Real-ESRGAN x4plus + métricas en BigQuery."
    )

    parser.add_argument("--job-id", default=None)
    parser.add_argument("--input-uri", default=None)

    # 0 = vídeo complet.
    parser.add_argument("--max-frames", type=int, default=0)

    # Frames que es mostregen per calcular mètriques.
    parser.add_argument("--sample-frames", type=int, default=30)

    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--tile", type=int, default=400)

    parser.add_argument("--clean-workdir", action="store_true")

    parser.add_argument("--skip-realesrgan", action="store_true")
    parser.add_argument("--skip-evaluation", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    realesrgan_script = Path("manual_tests/run_realesrgan_local_gpu.py")
    evaluation_script = Path("manual_tests/evaluate_restoration_quality.py")

    validate_existing_file(realesrgan_script)
    validate_existing_file(evaluation_script)

    if args.job_id is None or args.input_uri is None:
        print("[BigQuery] No has pasado job_id/input_uri. Buscando último job restaurado...")
        job_id, input_uri = get_latest_restored_job()
    else:
        job_id = args.job_id
        input_uri = args.input_uri

    print()
    print("=== ReFilm Advanced Restoration Pipeline ===")
    print(f"Project ID:     {PROJECT_ID}")
    print(f"Dataset:        {BQ_DATASET_ID}")
    print(f"Job ID:         {job_id}")
    print(f"Input URI:      {input_uri}")
    print(f"Max frames:     {args.max_frames}  (0 = vídeo completo)")
    print(f"Sample frames:  {args.sample_frames}")
    print(f"GPU ID:         {args.gpu_id}")
    print(f"Tile:           {args.tile}")
    print()

    if not args.skip_realesrgan:
        command = [
            sys.executable,
            str(realesrgan_script),
            "--job-id",
            job_id,
            "--input-uri",
            input_uri,
            "--max-frames",
            str(args.max_frames),
            "--gpu-id",
            str(args.gpu_id),
            "--tile",
            str(args.tile),
        ]

        if args.clean_workdir:
            command.append("--clean-workdir")

        run_command(command)
    else:
        print("[SKIP] Saltando Real-ESRGAN.")

    if not args.skip_evaluation:
        command = [
            sys.executable,
            str(evaluation_script),
            "--job-id",
            job_id,
            "--sample-frames",
            str(args.sample_frames),
        ]

        if args.clean_workdir:
            command.append("--clean-workdir")

        run_command(command)
    else:
        print("[SKIP] Saltando evaluación.")

    print()
    print("=" * 90)
    print("[OK] Pipeline avanzado completado.")
    print("=" * 90)
    print()
    print("Revisa BigQuery con:")
    print()
    print(f"SELECT * FROM `{table_id('vertex_model_runs')}` ORDER BY created_at DESC;")
    print()
    print(f"SELECT * FROM `{table_id('job_files')}` WHERE category = 'vertex_realesrgan' ORDER BY created_at DESC;")
    print()
    print(f"SELECT * FROM `{table_id('restoration_metrics')}` ORDER BY created_at DESC;")
    print()


if __name__ == "__main__":
    main()
