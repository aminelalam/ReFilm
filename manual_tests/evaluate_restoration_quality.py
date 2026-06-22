from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import cv2
import numpy as np
from google.cloud import bigquery, storage


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "encoded-ensign-496217-u4")
BQ_DATASET_ID = os.getenv("BQ_DATASET_ID", "refilm_audit")
BQ_LOCATION = os.getenv("BQ_LOCATION", "europe-southwest1")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def table_id(table_name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET_ID}.{table_name}"


def bq_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)


def run_bq_query(query: str, parameters: list[bigquery.ScalarQueryParameter] | None = None):
    client = bq_client()
    job_config = bigquery.QueryJobConfig(query_parameters=parameters or [])
    return client.query(query, job_config=job_config, location=BQ_LOCATION).result()


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


def safe_float(value) -> float | None:
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(value) or math.isinf(value):
        return None

    return value


def ratio(candidate: float | None, baseline: float | None) -> float | None:
    candidate = safe_float(candidate)
    baseline = safe_float(baseline)

    if candidate is None or baseline is None or baseline == 0:
        return None

    return candidate / baseline


def delta(candidate: float | None, baseline: float | None) -> float | None:
    candidate = safe_float(candidate)
    baseline = safe_float(baseline)

    if candidate is None or baseline is None:
        return None

    return candidate - baseline


def get_video_metadata(video_path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]

    result = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)

    video_stream = None

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if video_stream is None:
        raise RuntimeError(f"No se ha encontrado stream de vídeo en: {video_path}")

    width = int(video_stream.get("width", 0) or 0)
    height = int(video_stream.get("height", 0) or 0)

    fps_value = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "0/1"

    try:
        if "/" in fps_value:
            num, den = fps_value.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0.0
        else:
            fps = float(fps_value)
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    duration = None

    if video_stream.get("duration"):
        duration = safe_float(video_stream.get("duration"))
    elif data.get("format", {}).get("duration"):
        duration = safe_float(data.get("format", {}).get("duration"))

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_seconds": duration,
    }


def estimate_frame_noise(gray: np.ndarray) -> float:
    """
    Estimació simple de soroll i alta freqüència.
    No és perfecta: també puja amb vores i detall fi.
    Sirve como indicador aproximado para comparar versiones.
    """
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    residual = gray.astype(np.float32) - blurred.astype(np.float32)
    return float(np.std(residual))


def sample_frame_indices(total_frames: int, sample_frames: int) -> list[int]:
    if total_frames <= 0:
        return []

    if sample_frames <= 0 or sample_frames >= total_frames:
        return list(range(total_frames))

    values = np.linspace(0, total_frames - 1, sample_frames)
    return sorted(set(int(round(v)) for v in values))


def compute_video_quality_metrics(video_path: Path, sample_frames: int) -> dict:
    metadata = get_video_metadata(video_path)

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise RuntimeError(f"No se pudo abrir el vídeo: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    indices = sample_frame_indices(total_frames, sample_frames)

    sharpness_values: list[float] = []
    contrast_values: list[float] = []
    brightness_values: list[float] = []
    noise_values: list[float] = []
    flicker_values: list[float] = []

    previous_gray_small: np.ndarray | None = None

    for frame_index in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()

        if not ok or frame is None:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        contrast = float(np.std(gray))
        brightness = float(np.mean(gray))
        noise = estimate_frame_noise(gray)

        sharpness_values.append(sharpness)
        contrast_values.append(contrast)
        brightness_values.append(brightness)
        noise_values.append(noise)

        gray_small = cv2.resize(gray, (160, 120), interpolation=cv2.INTER_AREA).astype(np.float32)

        if previous_gray_small is not None:
            flicker = float(np.mean(np.abs(gray_small - previous_gray_small)))
            flicker_values.append(flicker)

        previous_gray_small = gray_small

    cap.release()

    def mean_or_none(values: list[float]) -> float | None:
        if not values:
            return None
        return float(np.mean(values))

    return {
        "width": metadata["width"],
        "height": metadata["height"],
        "fps": metadata["fps"],
        "duration_seconds": metadata["duration_seconds"],
        "sampled_frames": len(sharpness_values),
        "sharpness": mean_or_none(sharpness_values),
        "contrast": mean_or_none(contrast_values),
        "brightness": mean_or_none(brightness_values),
        "noise_estimate": mean_or_none(noise_values),
        "flicker_estimate": mean_or_none(flicker_values),
        "total_frames": total_frames,
    }


def get_latest_restored_job_id() -> str:
    query = f"""
    SELECT job_id
    FROM `{table_id("jobs")}`
    WHERE status = 'restored'
      AND original_uri IS NOT NULL
    ORDER BY updated_at DESC
    LIMIT 1
    """

    rows = list(run_bq_query(query))

    if not rows:
        raise RuntimeError("No he encontrado ningún job restaurado en BigQuery.")

    return rows[0]["job_id"]


def get_job_main_uris(job_id: str) -> dict:
    query = f"""
    SELECT
      job_id,
      original_uri,
      final_uri,
      comparison_uri,
      audit_uri
    FROM `{table_id("jobs")}`
    WHERE job_id = @job_id
    LIMIT 1
    """

    rows = list(
        run_bq_query(
            query,
            [bigquery.ScalarQueryParameter("job_id", "STRING", job_id)],
        )
    )

    if not rows:
        raise RuntimeError(f"No he encontrado el job en BigQuery: {job_id}")

    row = rows[0]

    return {
        "job_id": row["job_id"],
        "original_uri": row["original_uri"],
        "final_uri": row["final_uri"],
        "comparison_uri": row["comparison_uri"],
        "audit_uri": row["audit_uri"],
    }


def get_latest_file_uri(job_id: str, category: str, filename_contains: str | None = None) -> str | None:
    if filename_contains:
        query = f"""
        SELECT gcs_uri
        FROM `{table_id("job_files")}`
        WHERE job_id = @job_id
          AND category = @category
          AND filename LIKE @filename_like
        ORDER BY created_at DESC
        LIMIT 1
        """

        params = [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
            bigquery.ScalarQueryParameter("filename_like", "STRING", f"%{filename_contains}%"),
        ]
    else:
        query = f"""
        SELECT gcs_uri
        FROM `{table_id("job_files")}`
        WHERE job_id = @job_id
          AND category = @category
        ORDER BY created_at DESC
        LIMIT 1
        """

        params = [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
        ]

    rows = list(run_bq_query(query, params))

    if not rows:
        return None

    return rows[0]["gcs_uri"]


def get_latest_realesrgan_uri(job_id: str) -> str | None:
    uri = get_latest_file_uri(job_id, "vertex_realesrgan")

    if uri:
        return uri

    query = f"""
    SELECT output_uri
    FROM `{table_id("vertex_model_runs")}`
    WHERE job_id = @job_id
      AND status = 'completed'
      AND output_uri IS NOT NULL
    ORDER BY created_at DESC
    LIMIT 1
    """

    rows = list(
        run_bq_query(
            query,
            [bigquery.ScalarQueryParameter("job_id", "STRING", job_id)],
        )
    )

    if not rows:
        return None

    return rows[0]["output_uri"]


def resolve_video_uris(job_id: str) -> dict[str, str]:
    job = get_job_main_uris(job_id)

    original_uri = job.get("original_uri")
    current_restored_uri = job.get("final_uri")
    realesrgan_uri = get_latest_realesrgan_uri(job_id)

    if not current_restored_uri:
        current_restored_uri = get_latest_file_uri(job_id, "final", "restored")

    resolved = {}

    if original_uri:
        resolved["original"] = original_uri

    if current_restored_uri:
        resolved["current_restored"] = current_restored_uri

    if realesrgan_uri:
        resolved["realesrgan_x4plus"] = realesrgan_uri

    return resolved


def local_video_path(work_dir: Path, label: str, gcs_uri: str) -> Path:
    _, blob_name = parse_gcs_uri(gcs_uri)
    suffix = Path(blob_name).suffix or ".mp4"
    safe_label = label.replace("/", "_").replace("\\", "_")
    return work_dir / f"{safe_label}{suffix}"


def insert_metric_row(
    *,
    metric_id: str,
    job_id: str,
    comparison_type: str,
    baseline_label: str,
    candidate_label: str,
    baseline_uri: str,
    candidate_uri: str,
    baseline_metrics: dict,
    candidate_metrics: dict,
    details: dict,
) -> None:
    query = f"""
    MERGE `{table_id("restoration_metrics")}` T
    USING (
      SELECT
        @metric_id AS metric_id,
        @job_id AS job_id,
        @comparison_type AS comparison_type,
        @baseline_label AS baseline_label,
        @candidate_label AS candidate_label,
        @baseline_uri AS baseline_uri,
        @candidate_uri AS candidate_uri,
        @sampled_frames AS sampled_frames,

        @baseline_width AS baseline_width,
        @baseline_height AS baseline_height,
        @candidate_width AS candidate_width,
        @candidate_height AS candidate_height,

        @baseline_fps AS baseline_fps,
        @candidate_fps AS candidate_fps,
        @baseline_duration_seconds AS baseline_duration_seconds,
        @candidate_duration_seconds AS candidate_duration_seconds,

        @baseline_sharpness AS baseline_sharpness,
        @candidate_sharpness AS candidate_sharpness,
        @sharpness_gain AS sharpness_gain,

        @baseline_contrast AS baseline_contrast,
        @candidate_contrast AS candidate_contrast,
        @contrast_gain AS contrast_gain,

        @baseline_brightness AS baseline_brightness,
        @candidate_brightness AS candidate_brightness,
        @brightness_delta AS brightness_delta,

        @baseline_noise_estimate AS baseline_noise_estimate,
        @candidate_noise_estimate AS candidate_noise_estimate,
        @noise_delta AS noise_delta,

        @baseline_flicker_estimate AS baseline_flicker_estimate,
        @candidate_flicker_estimate AS candidate_flicker_estimate,
        @flicker_delta AS flicker_delta,

        @created_at AS created_at,
        @details_json AS details_json
    ) S
    ON T.metric_id = S.metric_id
    WHEN MATCHED THEN UPDATE SET
      job_id = S.job_id,
      comparison_type = S.comparison_type,
      baseline_label = S.baseline_label,
      candidate_label = S.candidate_label,
      baseline_uri = S.baseline_uri,
      candidate_uri = S.candidate_uri,
      sampled_frames = S.sampled_frames,

      baseline_width = S.baseline_width,
      baseline_height = S.baseline_height,
      candidate_width = S.candidate_width,
      candidate_height = S.candidate_height,

      baseline_fps = S.baseline_fps,
      candidate_fps = S.candidate_fps,
      baseline_duration_seconds = S.baseline_duration_seconds,
      candidate_duration_seconds = S.candidate_duration_seconds,

      baseline_sharpness = S.baseline_sharpness,
      candidate_sharpness = S.candidate_sharpness,
      sharpness_gain = S.sharpness_gain,

      baseline_contrast = S.baseline_contrast,
      candidate_contrast = S.candidate_contrast,
      contrast_gain = S.contrast_gain,

      baseline_brightness = S.baseline_brightness,
      candidate_brightness = S.candidate_brightness,
      brightness_delta = S.brightness_delta,

      baseline_noise_estimate = S.baseline_noise_estimate,
      candidate_noise_estimate = S.candidate_noise_estimate,
      noise_delta = S.noise_delta,

      baseline_flicker_estimate = S.baseline_flicker_estimate,
      candidate_flicker_estimate = S.candidate_flicker_estimate,
      flicker_delta = S.flicker_delta,

      created_at = S.created_at,
      details_json = S.details_json
    WHEN NOT MATCHED THEN INSERT (
      metric_id,
      job_id,
      comparison_type,
      baseline_label,
      candidate_label,
      baseline_uri,
      candidate_uri,
      sampled_frames,

      baseline_width,
      baseline_height,
      candidate_width,
      candidate_height,

      baseline_fps,
      candidate_fps,
      baseline_duration_seconds,
      candidate_duration_seconds,

      baseline_sharpness,
      candidate_sharpness,
      sharpness_gain,

      baseline_contrast,
      candidate_contrast,
      contrast_gain,

      baseline_brightness,
      candidate_brightness,
      brightness_delta,

      baseline_noise_estimate,
      candidate_noise_estimate,
      noise_delta,

      baseline_flicker_estimate,
      candidate_flicker_estimate,
      flicker_delta,

      created_at,
      details_json
    ) VALUES (
      S.metric_id,
      S.job_id,
      S.comparison_type,
      S.baseline_label,
      S.candidate_label,
      S.baseline_uri,
      S.candidate_uri,
      S.sampled_frames,

      S.baseline_width,
      S.baseline_height,
      S.candidate_width,
      S.candidate_height,

      S.baseline_fps,
      S.candidate_fps,
      S.baseline_duration_seconds,
      S.candidate_duration_seconds,

      S.baseline_sharpness,
      S.candidate_sharpness,
      S.sharpness_gain,

      S.baseline_contrast,
      S.candidate_contrast,
      S.contrast_gain,

      S.baseline_brightness,
      S.candidate_brightness,
      S.brightness_delta,

      S.baseline_noise_estimate,
      S.candidate_noise_estimate,
      S.noise_delta,

      S.baseline_flicker_estimate,
      S.candidate_flicker_estimate,
      S.flicker_delta,

      S.created_at,
      S.details_json
    )
    """

    sampled_frames = min(
        int(baseline_metrics.get("sampled_frames") or 0),
        int(candidate_metrics.get("sampled_frames") or 0),
    )

    params = [
        bigquery.ScalarQueryParameter("metric_id", "STRING", metric_id),
        bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        bigquery.ScalarQueryParameter("comparison_type", "STRING", comparison_type),
        bigquery.ScalarQueryParameter("baseline_label", "STRING", baseline_label),
        bigquery.ScalarQueryParameter("candidate_label", "STRING", candidate_label),
        bigquery.ScalarQueryParameter("baseline_uri", "STRING", baseline_uri),
        bigquery.ScalarQueryParameter("candidate_uri", "STRING", candidate_uri),
        bigquery.ScalarQueryParameter("sampled_frames", "INT64", sampled_frames),

        bigquery.ScalarQueryParameter("baseline_width", "INT64", baseline_metrics.get("width")),
        bigquery.ScalarQueryParameter("baseline_height", "INT64", baseline_metrics.get("height")),
        bigquery.ScalarQueryParameter("candidate_width", "INT64", candidate_metrics.get("width")),
        bigquery.ScalarQueryParameter("candidate_height", "INT64", candidate_metrics.get("height")),

        bigquery.ScalarQueryParameter("baseline_fps", "FLOAT64", safe_float(baseline_metrics.get("fps"))),
        bigquery.ScalarQueryParameter("candidate_fps", "FLOAT64", safe_float(candidate_metrics.get("fps"))),
        bigquery.ScalarQueryParameter("baseline_duration_seconds", "FLOAT64", safe_float(baseline_metrics.get("duration_seconds"))),
        bigquery.ScalarQueryParameter("candidate_duration_seconds", "FLOAT64", safe_float(candidate_metrics.get("duration_seconds"))),

        bigquery.ScalarQueryParameter("baseline_sharpness", "FLOAT64", safe_float(baseline_metrics.get("sharpness"))),
        bigquery.ScalarQueryParameter("candidate_sharpness", "FLOAT64", safe_float(candidate_metrics.get("sharpness"))),
        bigquery.ScalarQueryParameter("sharpness_gain", "FLOAT64", ratio(candidate_metrics.get("sharpness"), baseline_metrics.get("sharpness"))),

        bigquery.ScalarQueryParameter("baseline_contrast", "FLOAT64", safe_float(baseline_metrics.get("contrast"))),
        bigquery.ScalarQueryParameter("candidate_contrast", "FLOAT64", safe_float(candidate_metrics.get("contrast"))),
        bigquery.ScalarQueryParameter("contrast_gain", "FLOAT64", ratio(candidate_metrics.get("contrast"), baseline_metrics.get("contrast"))),

        bigquery.ScalarQueryParameter("baseline_brightness", "FLOAT64", safe_float(baseline_metrics.get("brightness"))),
        bigquery.ScalarQueryParameter("candidate_brightness", "FLOAT64", safe_float(candidate_metrics.get("brightness"))),
        bigquery.ScalarQueryParameter("brightness_delta", "FLOAT64", delta(candidate_metrics.get("brightness"), baseline_metrics.get("brightness"))),

        bigquery.ScalarQueryParameter("baseline_noise_estimate", "FLOAT64", safe_float(baseline_metrics.get("noise_estimate"))),
        bigquery.ScalarQueryParameter("candidate_noise_estimate", "FLOAT64", safe_float(candidate_metrics.get("noise_estimate"))),
        bigquery.ScalarQueryParameter("noise_delta", "FLOAT64", delta(candidate_metrics.get("noise_estimate"), baseline_metrics.get("noise_estimate"))),

        bigquery.ScalarQueryParameter("baseline_flicker_estimate", "FLOAT64", safe_float(baseline_metrics.get("flicker_estimate"))),
        bigquery.ScalarQueryParameter("candidate_flicker_estimate", "FLOAT64", safe_float(candidate_metrics.get("flicker_estimate"))),
        bigquery.ScalarQueryParameter("flicker_delta", "FLOAT64", delta(candidate_metrics.get("flicker_estimate"), baseline_metrics.get("flicker_estimate"))),

        bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", utc_now()),
        bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(details, ensure_ascii=False)),
    ]

    run_bq_query(query, params)


def evaluate_pair(
    *,
    job_id: str,
    baseline_label: str,
    candidate_label: str,
    baseline_uri: str,
    candidate_uri: str,
    local_paths: dict[str, Path],
    metrics_by_label: dict[str, dict],
    run_suffix: str,
) -> None:
    baseline_metrics = metrics_by_label[baseline_label]
    candidate_metrics = metrics_by_label[candidate_label]

    comparison_type = f"{baseline_label}_vs_{candidate_label}"
    metric_id = f"{job_id}_{comparison_type}_{run_suffix}"

    details = {
        "metric_notes": {
            "sharpness": "Variance of Laplacian. Higher usually means sharper, but can also increase with artifacts.",
            "contrast": "Standard deviation of grayscale intensity. Higher means stronger contrast.",
            "brightness": "Mean grayscale intensity.",
            "noise_estimate": "Std of high-frequency residual. Approximate; can increase with detail as well as noise.",
            "flicker_estimate": "Mean absolute difference between consecutive sampled frames resized to 160x120. Approximate temporal instability indicator.",
            "sharpness_gain": "candidate_sharpness / baseline_sharpness",
            "contrast_gain": "candidate_contrast / baseline_contrast",
            "deltas": "candidate - baseline",
        },
        "baseline_local_path": str(local_paths[baseline_label]),
        "candidate_local_path": str(local_paths[candidate_label]),
        "baseline_metrics_raw": baseline_metrics,
        "candidate_metrics_raw": candidate_metrics,
    }

    insert_metric_row(
        metric_id=metric_id,
        job_id=job_id,
        comparison_type=comparison_type,
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        baseline_uri=baseline_uri,
        candidate_uri=candidate_uri,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        details=details,
    )

    print()
    print(f"[OK] Métrica insertada: {comparison_type}")
    print(f"     Sharpness gain: {ratio(candidate_metrics.get('sharpness'), baseline_metrics.get('sharpness'))}")
    print(f"     Contrast gain:  {ratio(candidate_metrics.get('contrast'), baseline_metrics.get('contrast'))}")
    print(f"     Noise delta:    {delta(candidate_metrics.get('noise_estimate'), baseline_metrics.get('noise_estimate'))}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument("--job-id", default=None)
    parser.add_argument("--sample-frames", type=int, default=30)
    parser.add_argument("--clean-workdir", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    job_id = args.job_id or get_latest_restored_job_id()

    print()
    print("=== ReFilm restoration quality evaluation ===")
    print(f"Job ID: {job_id}")
    print(f"Sample frames per video: {args.sample_frames}")

    video_uris = resolve_video_uris(job_id)

    print()
    print("[URIs detectadas]")
    for label, uri in video_uris.items():
        print(f"- {label}: {uri}")

    required = ["original", "current_restored", "realesrgan_x4plus"]
    missing = [label for label in required if label not in video_uris]

    if missing:
        print()
        print("[AVISO] Faltan algunas versiones para comparar:")
        for label in missing:
            print(f"- {label}")

    if len(video_uris) < 2:
        raise RuntimeError("Necesito al menos dos vídeos para calcular comparaciones.")

    work_dir = Path("data/restoration_metrics") / job_id

    if args.clean_workdir and work_dir.exists():
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True, exist_ok=True)

    local_paths: dict[str, Path] = {}

    for label, uri in video_uris.items():
        local_path = local_video_path(work_dir, label, uri)
        download_from_gcs(uri, local_path)
        local_paths[label] = local_path

    metrics_by_label: dict[str, dict] = {}

    print()
    print("[Calculando métricas por vídeo]")

    for label, path in local_paths.items():
        print(f"- {label}: {path}")
        metrics = compute_video_quality_metrics(path, sample_frames=args.sample_frames)
        metrics_by_label[label] = metrics

        print(
            f"  resolución={metrics['width']}x{metrics['height']} "
            f"fps={metrics['fps']} "
            f"sharpness={metrics['sharpness']} "
            f"contrast={metrics['contrast']} "
            f"noise={metrics['noise_estimate']}"
        )

    run_suffix = str(int(time.time()))

    comparisons = [
        ("original", "current_restored"),
        ("original", "realesrgan_x4plus"),
        ("current_restored", "realesrgan_x4plus"),
    ]

    for baseline_label, candidate_label in comparisons:
        if baseline_label not in video_uris or candidate_label not in video_uris:
            continue

        evaluate_pair(
            job_id=job_id,
            baseline_label=baseline_label,
            candidate_label=candidate_label,
            baseline_uri=video_uris[baseline_label],
            candidate_uri=video_uris[candidate_label],
            local_paths=local_paths,
            metrics_by_label=metrics_by_label,
            run_suffix=run_suffix,
        )

    print()
    print("[OK] Evaluación completada.")
    print("Revisa BigQuery:")
    print(f"SELECT * FROM `{table_id('restoration_metrics')}` ORDER BY created_at DESC;")


if __name__ == "__main__":
    main()
