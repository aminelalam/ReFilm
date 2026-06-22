from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from google.cloud import bigquery, storage


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "encoded-ensign-496217-u4")
BQ_DATASET_ID = os.getenv("BQ_DATASET_ID", "refilm_audit")
BQ_LOCATION = os.getenv("BQ_LOCATION", "europe-southwest1")
DEFAULT_BUCKET = os.getenv("GCS_BUCKET_NAME", "refilm-sm-alfonso-2026")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_command(command: list[str]) -> None:
    print()
    print("[CMD]", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"No es una URI válida de Cloud Storage: {gcs_uri}")

    without_scheme = gcs_uri.replace("gs://", "", 1)
    bucket_name, _, blob_name = without_scheme.partition("/")

    if not bucket_name or not blob_name:
        raise ValueError(f"URI GCS incompleta: {gcs_uri}")

    return bucket_name, unquote(blob_name)


def download_from_gcs(gcs_uri: str, local_path: Path) -> None:
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        raise FileNotFoundError(f"No existe en Cloud Storage: {gcs_uri}")

    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path))

    print(f"[GCS] Descargado: {gcs_uri}")
    print(f"[GCS] Local: {local_path}")


def upload_to_gcs(local_path: Path, gcs_uri: str) -> None:
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    content_type, _ = mimetypes.guess_type(str(local_path))

    blob.upload_from_filename(
        str(local_path),
        content_type=content_type or "application/octet-stream",
    )

    print(f"[GCS] Subido: {local_path}")
    print(f"[GCS] URI: {gcs_uri}")


def bq_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)


def table_id(table_name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET_ID}.{table_name}"


def run_bq_query(query: str, parameters: list[bigquery.ScalarQueryParameter]) -> None:
    client = bq_client()
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    client.query(query, job_config=job_config, location=BQ_LOCATION).result()


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
        raise RuntimeError("No he encontrado ningún job con status='restored' y original_uri.")

    return rows[0]["job_id"], rows[0]["original_uri"]


def get_video_fps(input_path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "0",
        "-of",
        "csv=p=0",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        str(input_path),
    ]

    result = subprocess.run(command, check=True, capture_output=True, text=True)
    value = result.stdout.strip()

    if "/" in value:
        num, den = value.split("/")
        den_float = float(den)

        if den_float == 0:
            return 24.0

        return float(num) / den_float

    try:
        return float(value)
    except ValueError:
        return 24.0


def extract_frames(input_path: Path, frames_dir: Path, max_frames: int) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
    ]

    if max_frames > 0:
        command += ["-frames:v", str(max_frames)]

    command += [
        "-q:v",
        "2",
        str(frames_dir / "frame_%08d.png"),
    ]

    run_command(command)


def run_realesrgan(
    *,
    exe_path: Path,
    input_frames_dir: Path,
    output_frames_dir: Path,
    model_name: str,
    scale: int,
    tile: int,
    gpu_id: int,
) -> None:
    if not exe_path.exists():
        raise FileNotFoundError(f"No encuentro Real-ESRGAN en: {exe_path}")

    output_frames_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(exe_path),
        "-i",
        str(input_frames_dir),
        "-o",
        str(output_frames_dir),
        "-n",
        model_name,
        "-s",
        str(scale),
        "-t",
        str(tile),
        "-g",
        str(gpu_id),
        "-f",
        "png",
    ]

    run_command(command)


def encode_video(
    *,
    upscaled_frames_dir: Path,
    original_video_path: Path,
    output_video_path: Path,
    fps: float,
) -> None:
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(upscaled_frames_dir / "frame_%08d.png"),
        "-i",
        str(original_video_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(output_video_path),
    ]

    run_command(command)


def upsert_vertex_model_run(
    *,
    run_id: str,
    job_id: str,
    model_name: str,
    status: str,
    input_uri: str,
    output_uri: str | None,
    created_at: datetime,
    started_at: datetime | None,
    finished_at: datetime | None,
    details: dict,
    error: str | None,
) -> None:
    query = f"""
    MERGE `{table_id("vertex_model_runs")}` T
    USING (
      SELECT
        @run_id AS run_id,
        @job_id AS job_id,
        @model_name AS model_name,
        @model_version AS model_version,
        @task_type AS task_type,
        @status AS status,
        @input_uri AS input_uri,
        @output_uri AS output_uri,
        @vertex_custom_job_name AS vertex_custom_job_name,
        @machine_type AS machine_type,
        @accelerator_type AS accelerator_type,
        @accelerator_count AS accelerator_count,
        @created_at AS created_at,
        @started_at AS started_at,
        @finished_at AS finished_at,
        @details_json AS details_json,
        @error AS error
    ) S
    ON T.run_id = S.run_id
    WHEN MATCHED THEN UPDATE SET
      job_id = S.job_id,
      model_name = S.model_name,
      model_version = S.model_version,
      task_type = S.task_type,
      status = S.status,
      input_uri = S.input_uri,
      output_uri = S.output_uri,
      vertex_custom_job_name = S.vertex_custom_job_name,
      machine_type = S.machine_type,
      accelerator_type = S.accelerator_type,
      accelerator_count = S.accelerator_count,
      created_at = S.created_at,
      started_at = S.started_at,
      finished_at = S.finished_at,
      details_json = S.details_json,
      error = S.error
    WHEN NOT MATCHED THEN INSERT (
      run_id,
      job_id,
      model_name,
      model_version,
      task_type,
      status,
      input_uri,
      output_uri,
      vertex_custom_job_name,
      machine_type,
      accelerator_type,
      accelerator_count,
      created_at,
      started_at,
      finished_at,
      details_json,
      error
    ) VALUES (
      S.run_id,
      S.job_id,
      S.model_name,
      S.model_version,
      S.task_type,
      S.status,
      S.input_uri,
      S.output_uri,
      S.vertex_custom_job_name,
      S.machine_type,
      S.accelerator_type,
      S.accelerator_count,
      S.created_at,
      S.started_at,
      S.finished_at,
      S.details_json,
      S.error
    )
    """

    run_bq_query(
        query,
        [
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("model_name", "STRING", model_name),
            bigquery.ScalarQueryParameter("model_version", "STRING", "x4plus-localgpu"),
            bigquery.ScalarQueryParameter("task_type", "STRING", "video_super_resolution"),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("vertex_custom_job_name", "STRING", "local_gpu_fallback"),
            bigquery.ScalarQueryParameter("machine_type", "STRING", "local_windows_pc"),
            bigquery.ScalarQueryParameter("accelerator_type", "STRING", "local_nvidia_gpu_vulkan"),
            bigquery.ScalarQueryParameter("accelerator_count", "INT64", 1),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", created_at),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(details, ensure_ascii=False)),
            bigquery.ScalarQueryParameter("error", "STRING", error),
        ],
    )


def register_job_file(
    *,
    job_id: str,
    category: str,
    filename: str,
    gcs_uri: str,
    size_bytes: int | None,
) -> None:
    content_type, _ = mimetypes.guess_type(filename)

    query = f"""
    MERGE `{table_id("job_files")}` T
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

    run_bq_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
            bigquery.ScalarQueryParameter("filename", "STRING", filename),
            bigquery.ScalarQueryParameter("gcs_uri", "STRING", gcs_uri),
            bigquery.ScalarQueryParameter("content_type", "STRING", content_type or "video/mp4"),
            bigquery.ScalarQueryParameter("size_bytes", "INT64", size_bytes),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", utc_now()),
        ],
    )


def register_processing_step(
    *,
    job_id: str,
    input_uri: str,
    output_uri: str | None,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    details: dict,
) -> None:
    query = f"""
    MERGE `{table_id("processing_steps")}` T
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

    run_bq_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("scene_id", "STRING", "video"),
            bigquery.ScalarQueryParameter("step_name", "STRING", "local_gpu_realesrgan_x4plus"),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(details, ensure_ascii=False)),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument("--job-id", default=None)
    parser.add_argument("--input-uri", default=None)

    parser.add_argument(
        "--exe",
        default="tools/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan.exe",
    )

    parser.add_argument("--model-name", default="realesrgan-x4plus")
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--tile", type=int, default=400)
    parser.add_argument("--gpu-id", type=int, default=0)

    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--clean-workdir", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.job_id is None or args.input_uri is None:
        print("[BigQuery] No has pasado job_id/input_uri. Buscando último job restored...")
        job_id, input_uri = get_latest_restored_job()
    else:
        job_id = args.job_id
        input_uri = args.input_uri

    run_id = f"local_realesrgan_{job_id}_{int(time.time())}"

    output_uri = (
        f"gs://{DEFAULT_BUCKET}/vertex_realesrgan/{job_id}/"
        f"restored_realesrgan_x4plus_localgpu.mp4"
    )

    work_dir = Path("data/local_realesrgan") / job_id
    input_path = work_dir / "input.mp4"
    frames_dir = work_dir / "frames"
    upscaled_frames_dir = work_dir / "upscaled_frames"
    output_path = work_dir / "restored_realesrgan_x4plus_localgpu.mp4"

    details = {
        "mode": "local_gpu_fallback",
        "model": args.model_name,
        "scale": args.scale,
        "tile": args.tile,
        "gpu_id": args.gpu_id,
        "max_frames": args.max_frames,
        "input_uri": input_uri,
        "output_uri": output_uri,
        "note": (
            "Real-ESRGAN x4plus executed locally using GPU/Vulkan "
            "because Vertex AI GPU quota was unavailable."
        ),
    }

    created_at = utc_now()
    started_at = created_at

    print()
    print("=== ReFilm Real-ESRGAN x4plus local GPU ===")
    print(f"Job ID: {job_id}")
    print(f"Input:  {input_uri}")
    print(f"Output: {output_uri}")
    print()

    upsert_vertex_model_run(
        run_id=run_id,
        job_id=job_id,
        model_name=args.model_name,
        status="running",
        input_uri=input_uri,
        output_uri=output_uri,
        created_at=created_at,
        started_at=started_at,
        finished_at=None,
        details=details,
        error=None,
    )

    try:
        if args.clean_workdir and work_dir.exists():
            shutil.rmtree(work_dir)

        work_dir.mkdir(parents=True, exist_ok=True)

        download_from_gcs(input_uri, input_path)

        fps = get_video_fps(input_path)
        details["fps"] = fps
        print(f"[Video] FPS detectado: {fps}")

        extract_frames(input_path, frames_dir, max_frames=args.max_frames)

        run_realesrgan(
            exe_path=Path(args.exe),
            input_frames_dir=frames_dir,
            output_frames_dir=upscaled_frames_dir,
            model_name=args.model_name,
            scale=args.scale,
            tile=args.tile,
            gpu_id=args.gpu_id,
        )

        encode_video(
            upscaled_frames_dir=upscaled_frames_dir,
            original_video_path=input_path,
            output_video_path=output_path,
            fps=fps,
        )

        upload_to_gcs(output_path, output_uri)

        finished_at = utc_now()

        details["output_size_bytes"] = output_path.stat().st_size if output_path.exists() else None
        details["finished_ok"] = True

        upsert_vertex_model_run(
            run_id=run_id,
            job_id=job_id,
            model_name=args.model_name,
            status="completed",
            input_uri=input_uri,
            output_uri=output_uri,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            details=details,
            error=None,
        )

        register_job_file(
            job_id=job_id,
            category="vertex_realesrgan",
            filename=output_path.name,
            gcs_uri=output_uri,
            size_bytes=output_path.stat().st_size if output_path.exists() else None,
        )

        register_processing_step(
            job_id=job_id,
            input_uri=input_uri,
            output_uri=output_uri,
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            details=details,
        )

        print()
        print("[OK] Real-ESRGAN local GPU completado correctamente.")
        print(f"[OK] Resultado local: {output_path}")
        print(f"[OK] Resultado GCS:   {output_uri}")

    except Exception as exc:
        finished_at = utc_now()
        error_text = str(exc)

        details["finished_ok"] = False
        details["error"] = error_text

        upsert_vertex_model_run(
            run_id=run_id,
            job_id=job_id,
            model_name=args.model_name,
            status="error",
            input_uri=input_uri,
            output_uri=output_uri,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            details=details,
            error=error_text,
        )

        register_processing_step(
            job_id=job_id,
            input_uri=input_uri,
            output_uri=output_uri,
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            details=details,
        )

        print()
        print(f"[ERROR] {error_text}")
        raise


if __name__ == "__main__":
    main()