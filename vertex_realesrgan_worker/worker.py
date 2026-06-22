from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import cv2
import requests
import torch
from basicsr.archs.rrdbnet_arch import RRDBNet
from google.cloud import bigquery, storage
from realesrgan import RealESRGANer
from tqdm import tqdm
from app.colorization import colorize_video_file
from app.profiles import normalize_color_mode
from app.video_intelligence import safe_analyze_and_record_shots


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "encoded-ensign-496217-u4")
BQ_DATASET_ID = os.getenv("BQ_DATASET_ID", "refilm_audit")
BQ_LOCATION = os.getenv("BQ_LOCATION", "europe-southwest1")

MODEL_URLS = {
    "RealESRGAN_x4plus": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
}
COLOR_ENHANCEMENT_FILTER = "eq=contrast=1.02:saturation=1.05:gamma=1.01"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_command(command: list[str]) -> None:
    print("[CMD]", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"URI GCS inválida: {gcs_uri}")

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

    print(f"[GCS] Descargado {gcs_uri} -> {local_path}", flush=True)


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

    print(f"[GCS] Subido {local_path} -> {gcs_uri}", flush=True)


def table_id(table_name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET_ID}.{table_name}"


def bq_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)


def run_bq_query(query: str, parameters: list[bigquery.ScalarQueryParameter]) -> None:
    client = bq_client()
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    client.query(query, job_config=job_config, location=BQ_LOCATION).result()


def upsert_vertex_run(
    *,
    run_id: str,
    job_id: str,
    model_name: str,
    model_version: str | None,
    task_type: str,
    status: str,
    input_uri: str,
    output_uri: str | None,
    vertex_custom_job_name: str | None,
    machine_type: str | None,
    accelerator_type: str | None,
    accelerator_count: int | None,
    created_at: datetime | None,
    started_at: datetime | None,
    finished_at: datetime | None,
    details_json: str | None,
    error: str | None,
) -> None:
    query = f"""
    MERGE `{table_id('vertex_model_runs')}` T
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
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
            bigquery.ScalarQueryParameter("task_type", "STRING", task_type),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("vertex_custom_job_name", "STRING", vertex_custom_job_name),
            bigquery.ScalarQueryParameter("machine_type", "STRING", machine_type),
            bigquery.ScalarQueryParameter("accelerator_type", "STRING", accelerator_type),
            bigquery.ScalarQueryParameter("accelerator_count", "INT64", accelerator_count),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", created_at),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("details_json", "STRING", details_json),
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

    content_type, _ = mimetypes.guess_type(filename)

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

    run_bq_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("scene_id", "STRING", "video"),
            bigquery.ScalarQueryParameter("step_name", "vertex_ai_realesrgan_x4plus"),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", finished_at),
            bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(details, ensure_ascii=False)),
        ],
    )


def update_job_status(
    *,
    job_id: str,
    status: str,
    final_uri: str | None = None,
    comparison_uri: str | None = None,
    error: str | None = None,
) -> None:
    query = f"""
    UPDATE `{table_id('jobs')}`
    SET status = @status,
        updated_at = @updated_at,
        final_uri = COALESCE(@final_uri, final_uri),
        comparison_uri = COALESCE(@comparison_uri, comparison_uri),
        error = @error
    WHERE job_id = @job_id
    """
    run_bq_query(
        query,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", utc_now()),
            bigquery.ScalarQueryParameter("final_uri", "STRING", final_uri),
            bigquery.ScalarQueryParameter("comparison_uri", "STRING", comparison_uri),
            bigquery.ScalarQueryParameter("error", "STRING", error),
        ],
    )


def analyze_video(path: Path, sample_frames: int = 12) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"No se pudo abrir el video para metricas: {path}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    indices = sorted(set(int(round(v)) for v in __import__("numpy").linspace(0, max(0, total - 1), min(sample_frames, total))))
    values = {"sharpness": [], "contrast": [], "brightness": [], "noise_estimate": [], "saturation": []}
    try:
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            target_height = max(1, round(frame.shape[0] * 640 / frame.shape[1]))
            frame = cv2.resize(frame, (640, target_height), interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            residual = gray.astype("float32") - cv2.GaussianBlur(gray, (3, 3), 0).astype("float32")
            values["sharpness"].append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            values["contrast"].append(float(gray.std()))
            values["brightness"].append(float(gray.mean()))
            values["noise_estimate"].append(float(residual.std()))
            values["saturation"].append(float(hsv[:, :, 1].mean()))
    finally:
        capture.release()

    np = __import__("numpy")
    return {
        "width": width,
        "height": height,
        "fps": round(fps, 4),
        "duration_seconds": round(total / fps, 4) if fps else None,
        "sampled_frames": len(values["sharpness"]),
        **{name: round(float(np.mean(items)), 4) if items else None for name, items in values.items()},
    }


def compare_videos(original: Path, restored: Path) -> dict:
    baseline = analyze_video(original)
    candidate = analyze_video(restored)

    def ratio(name: str) -> float | None:
        before, after = baseline.get(name), candidate.get(name)
        return round(after / before, 4) if before not in {None, 0} and after is not None else None

    def delta(name: str) -> float | None:
        before, after = baseline.get(name), candidate.get(name)
        return round(after - before, 4) if before is not None and after is not None else None

    return {
        "original": baseline,
        "restored": candidate,
        "change": {
            "resolution_scale": round(candidate["width"] / baseline["width"], 4) if baseline["width"] else None,
            "sharpness_gain": ratio("sharpness"),
            "contrast_gain": ratio("contrast"),
            "brightness_delta": delta("brightness"),
            "noise_delta": delta("noise_estimate"),
            "saturation_delta": delta("saturation"),
            "temporal_change_delta": None,
        },
        "worker": {"type": "cloud_run_gpu", "model": "RealESRGAN_x4plus"},
    }


def record_quality_summary(job_id: str, quality: dict) -> None:
    run_bq_query(
        f"""
        INSERT INTO `{table_id('metrics')}` (job_id, metric_name, metric_value, details_json, created_at)
        VALUES (@job_id, 'quality_summary', NULL, @details_json, @created_at)
        """,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(quality)),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", utc_now()),
        ],
    )


def download_model_weights(model_name: str, model_path: Path) -> None:
    if model_path.exists():
        print(f"[Model] Pesos ya existen: {model_path}", flush=True)
        return

    url = MODEL_URLS.get(model_name)
    if not url:
        raise ValueError(f"Modelo no soportado: {model_name}")

    model_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[Model] Descargando pesos {model_name} desde {url}", flush=True)

    response = requests.get(url, timeout=600)
    response.raise_for_status()

    model_path.write_bytes(response.content)

    print(f"[Model] Pesos guardados en {model_path}", flush=True)


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


def build_upsampler(model_name: str, model_path: Path, scale: int, tile: int) -> RealESRGANer:
    if model_name != "RealESRGAN_x4plus":
        raise ValueError("Este worker está preparado para RealESRGAN_x4plus.")

    model = RRDBNet(
        num_in_ch=3,
        num_out_ch=3,
        num_feat=64,
        num_block=23,
        num_grow_ch=32,
        scale=4,
    )

    use_half = torch.cuda.is_available()
    gpu_id = 0 if torch.cuda.is_available() else None

    print(f"[GPU] torch.cuda.is_available() = {torch.cuda.is_available()}", flush=True)

    if torch.cuda.is_available():
        print(f"[GPU] Nombre GPU: {torch.cuda.get_device_name(0)}", flush=True)

    return RealESRGANer(
        scale=scale,
        model_path=str(model_path),
        model=model,
        tile=tile,
        tile_pad=10,
        pre_pad=0,
        half=use_half,
        gpu_id=gpu_id,
    )


def upscale_frames(
    *,
    input_frames_dir: Path,
    output_frames_dir: Path,
    model_name: str,
    model_path: Path,
    scale: int,
    outscale: float,
    tile: int,
) -> int:
    output_frames_dir.mkdir(parents=True, exist_ok=True)

    upsampler = build_upsampler(
        model_name=model_name,
        model_path=model_path,
        scale=scale,
        tile=tile,
    )

    frame_paths = sorted(input_frames_dir.glob("frame_*.png"))

    if not frame_paths:
        raise RuntimeError("No se han extraído frames del vídeo.")

    for frame_path in tqdm(frame_paths, desc="Upscaling frames"):
        image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)

        if image is None:
            raise RuntimeError(f"No se pudo leer frame: {frame_path}")

        output, _ = upsampler.enhance(image, outscale=outscale)

        output_path = output_frames_dir / frame_path.name
        cv2.imwrite(str(output_path), output)

    return len(frame_paths)


def encode_video(
    *,
    upscaled_frames_dir: Path,
    original_video_path: Path,
    output_video_path: Path,
    fps: float,
    color_mode: str = "none",
    color_style: str = "historical_natural",
) -> str:
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    render_output_path = (
        output_video_path.with_name("restored_realesrgan_luma.mp4")
        if color_mode == "ai_natural"
        else output_video_path
    )

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
    ]
    if color_mode == "enhance":
        command += ["-vf", COLOR_ENHANCEMENT_FILTER]
    common = command + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    try:
        run_command(common + ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", str(render_output_path)])
        encoder = "h264_nvenc"
    except subprocess.CalledProcessError:
        run_command(common + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(render_output_path)])
        encoder = "libx264_fallback"
    if color_mode == "ai_natural":
        colorize_video_file(render_output_path, output_video_path, color_style=color_style, fps=str(fps), crf=20)
        render_output_path.unlink(missing_ok=True)
        return f"{encoder}+opencv_dnn_colorization"
    return encoder


def create_comparison(original_path: Path, restored_path: Path, output_path: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(original_path),
            "-i",
            str(restored_path),
            "-filter_complex",
            "[0:v]scale=640:-2,setsar=1[left];[1:v]scale=640:-2,setsar=1[right];[left][right]hstack=inputs=2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-an",
            str(output_path),
        ]
    )


def process_video(args: argparse.Namespace) -> None:
    started_at = utc_now()
    created_at = started_at

    run_id = args.run_id or f"realesrgan_{args.job_id}_{int(time.time())}"

    vertex_custom_job_name = os.getenv("CLOUD_ML_JOB_ID") or os.getenv("AIP_JOB_NAME")

    details = {
        "model": args.model_name,
        "scale": args.scale,
        "outscale": args.outscale,
        "tile": args.tile,
        "max_frames": args.max_frames,
        "note": "RealESRGAN_x4plus executed as a cloud GPU batch job",
    }

    upsert_vertex_run(
        run_id=run_id,
        job_id=args.job_id,
        model_name=args.model_name,
        model_version="x4plus-pretrained",
        task_type="video_super_resolution",
        status="running",
        input_uri=args.input_uri,
        output_uri=args.output_uri,
        vertex_custom_job_name=vertex_custom_job_name,
        machine_type=args.machine_type,
        accelerator_type=args.accelerator_type,
        accelerator_count=args.accelerator_count,
        created_at=created_at,
        started_at=started_at,
        finished_at=None,
        details_json=json.dumps(details, ensure_ascii=False),
        error=None,
    )

    work_dir = Path("/tmp/refilm_realesrgan")
    input_path = work_dir / "input.mp4"
    output_path = work_dir / "restored_realesrgan_x4plus.mp4"
    comparison_path = work_dir / "comparison.mp4"
    frames_dir = work_dir / "frames"
    upscaled_frames_dir = work_dir / "upscaled_frames"
    model_path = work_dir / "models" / f"{args.model_name}.pth"

    for directory in [frames_dir, upscaled_frames_dir]:
        shutil.rmtree(directory, ignore_errors=True)
    for path in [input_path, output_path, comparison_path]:
        path.unlink(missing_ok=True)

    try:
        update_job_status(job_id=args.job_id, status="processing")
        download_from_gcs(args.input_uri, input_path)
        download_model_weights(args.model_name, model_path)

        fps = get_video_fps(input_path)
        print(f"[Video] FPS detectado: {fps}", flush=True)

        with ThreadPoolExecutor(max_workers=1) as executor:
            shots_future = executor.submit(
                safe_analyze_and_record_shots,
                args.job_id,
                args.input_uri,
                900,
            )
            extract_frames(input_path, frames_dir, max_frames=args.max_frames)

            frame_count = upscale_frames(
                input_frames_dir=frames_dir,
                output_frames_dir=upscaled_frames_dir,
                model_name=args.model_name,
                model_path=model_path,
                scale=args.scale,
                outscale=args.outscale,
                tile=args.tile,
            )

            encoder = encode_video(
                upscaled_frames_dir=upscaled_frames_dir,
                original_video_path=input_path,
                output_video_path=output_path,
                fps=fps,
                color_mode=normalize_color_mode(args.color_mode, legacy_colorize=args.colorize == "true"),
                color_style=args.color_style,
            )
            create_comparison(input_path, output_path, comparison_path)
            shots_detected = len(shots_future.result())

        upload_to_gcs(output_path, args.output_uri)
        upload_to_gcs(comparison_path, args.comparison_uri)
        quality = compare_videos(input_path, output_path)
        quality["worker"]["color_mode"] = normalize_color_mode(args.color_mode, legacy_colorize=args.colorize == "true")
        quality["worker"]["color_style"] = args.color_style
        record_quality_summary(args.job_id, quality)

        finished_at = utc_now()

        details["frames_processed"] = frame_count
        details["output_size_bytes"] = output_path.stat().st_size if output_path.exists() else None
        details["fps"] = fps
        details["cuda_available"] = torch.cuda.is_available()
        details["encoder"] = encoder
        details["color_mode"] = normalize_color_mode(args.color_mode, legacy_colorize=args.colorize == "true")
        details["color_style"] = args.color_style
        details["shots_detected"] = shots_detected

        upsert_vertex_run(
            run_id=run_id,
            job_id=args.job_id,
            model_name=args.model_name,
            model_version="x4plus-pretrained",
            task_type="video_super_resolution",
            status="completed",
            input_uri=args.input_uri,
            output_uri=args.output_uri,
            vertex_custom_job_name=vertex_custom_job_name,
            machine_type=args.machine_type,
            accelerator_type=args.accelerator_type,
            accelerator_count=args.accelerator_count,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            details_json=json.dumps(details, ensure_ascii=False),
            error=None,
        )

        register_job_file(
            job_id=args.job_id,
            category="final",
            filename=Path(parse_gcs_uri(args.output_uri)[1]).name,
            gcs_uri=args.output_uri,
            size_bytes=output_path.stat().st_size if output_path.exists() else None,
        )
        register_job_file(
            job_id=args.job_id,
            category="final",
            filename=Path(parse_gcs_uri(args.comparison_uri)[1]).name,
            gcs_uri=args.comparison_uri,
            size_bytes=comparison_path.stat().st_size if comparison_path.exists() else None,
        )

        register_processing_step(
            job_id=args.job_id,
            input_uri=args.input_uri,
            output_uri=args.output_uri,
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            details=details,
        )
        update_job_status(
            job_id=args.job_id,
            status="restored",
            final_uri=args.output_uri,
            comparison_uri=args.comparison_uri,
        )

        print("[DONE] Real-ESRGAN Vertex AI job completed successfully.", flush=True)

    except Exception as exc:
        finished_at = utc_now()
        error_text = str(exc)

        print(f"[ERROR] {error_text}", file=sys.stderr, flush=True)

        upsert_vertex_run(
            run_id=run_id,
            job_id=args.job_id,
            model_name=args.model_name,
            model_version="x4plus-pretrained",
            task_type="video_super_resolution",
            status="error",
            input_uri=args.input_uri,
            output_uri=args.output_uri,
            vertex_custom_job_name=vertex_custom_job_name,
            machine_type=args.machine_type,
            accelerator_type=args.accelerator_type,
            accelerator_count=args.accelerator_count,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            details_json=json.dumps(details, ensure_ascii=False),
            error=error_text,
        )

        register_processing_step(
            job_id=args.job_id,
            input_uri=args.input_uri,
            output_uri=args.output_uri,
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            details={**details, "error": error_text},
        )
        update_job_status(job_id=args.job_id, status="error", error=error_text)

        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument("--job-id", required=True)
    parser.add_argument("--input-uri", required=True)
    parser.add_argument("--output-uri", required=True)
    parser.add_argument("--comparison-uri", required=True)
    parser.add_argument("--profile", default="ai_realesrgan")
    parser.add_argument("--colorize", default="false")
    parser.add_argument("--color-mode", default=None)
    parser.add_argument("--color-style", default="historical_natural")

    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-name", default="RealESRGAN_x4plus")
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--outscale", type=float, default=2.0)
    parser.add_argument("--tile", type=int, default=400)

    # Control de cost: limita frames només en proves acotades.
    parser.add_argument("--max-frames", type=int, default=0)

    # Només per registrar-ho a BigQuery.
    parser.add_argument("--machine-type", default="n1-standard-4")
    parser.add_argument("--accelerator-type", default="NVIDIA_TESLA_T4")
    parser.add_argument("--accelerator-count", type=int, default=1)

    return parser.parse_args()


if __name__ == "__main__":
    process_video(parse_args())
