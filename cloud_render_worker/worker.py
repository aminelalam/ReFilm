from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery, storage

from app.quality import compare_videos
from app.colorization import colorize_video_file
from app.profiles import COLOR_ENHANCEMENT_FILTER, PROFILES, normalize_color_mode, normalize_profile
from app.video_intelligence import safe_analyze_and_record_shots


PROJECT_ID = __import__("os").getenv("GOOGLE_CLOUD_PROJECT")
BQ_DATASET_ID = __import__("os").getenv("BQ_DATASET_ID", "refilm_audit")
BQ_LOCATION = __import__("os").getenv("BQ_LOCATION", "europe-southwest1")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def table_id(name: str) -> str:
    return f"{PROJECT_ID}.{BQ_DATASET_ID}.{name}"


def run(command: list[str]) -> None:
    print("[CMD]", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    bucket, _, blob = uri[5:].partition("/")
    if not bucket or not blob:
        raise ValueError(f"Incomplete GCS URI: {uri}")
    return bucket, blob


def download(uri: str, path: Path) -> None:
    bucket, blob = parse_gcs_uri(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.Client(project=PROJECT_ID).bucket(bucket).blob(blob).download_to_filename(str(path))


def upload(path: Path, uri: str, content_type: str) -> None:
    bucket, blob = parse_gcs_uri(uri)
    storage.Client(project=PROJECT_ID).bucket(bucket).blob(blob).upload_from_filename(
        str(path), content_type=content_type
    )


def query(sql: str, parameters: list[bigquery.ScalarQueryParameter]) -> None:
    client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
    config = bigquery.QueryJobConfig(query_parameters=parameters)
    client.query(sql, job_config=config, location=BQ_LOCATION).result()


def record_file(job_id: str, category: str, uri: str, path: Path, content_type: str) -> None:
    query(
        f"""
        MERGE `{table_id('job_files')}` T
        USING (SELECT @job_id job_id, @category category, @filename filename, @gcs_uri gcs_uri,
                      @content_type content_type, @size_bytes size_bytes, @created_at created_at) S
        ON T.gcs_uri = S.gcs_uri
        WHEN MATCHED THEN UPDATE SET size_bytes=S.size_bytes, created_at=S.created_at
        WHEN NOT MATCHED THEN INSERT (job_id, category, filename, gcs_uri, content_type, size_bytes, created_at)
        VALUES (S.job_id, S.category, S.filename, S.gcs_uri, S.content_type, S.size_bytes, S.created_at)
        """,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("category", "STRING", category),
            bigquery.ScalarQueryParameter("filename", "STRING", path.name),
            bigquery.ScalarQueryParameter("gcs_uri", "STRING", uri),
            bigquery.ScalarQueryParameter("content_type", "STRING", content_type),
            bigquery.ScalarQueryParameter("size_bytes", "INT64", path.stat().st_size),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", utc_now()),
        ],
    )


def update_job(
    job_id: str,
    status: str,
    *,
    final_uri: str | None = None,
    comparison_uri: str | None = None,
    error: str | None = None,
) -> None:
    query(
        f"""
        UPDATE `{table_id('jobs')}`
        SET status=@status, updated_at=@updated_at,
            final_uri=COALESCE(@final_uri, final_uri),
            comparison_uri=COALESCE(@comparison_uri, comparison_uri),
            error=@error
        WHERE job_id=@job_id
        """,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", utc_now()),
            bigquery.ScalarQueryParameter("final_uri", "STRING", final_uri),
            bigquery.ScalarQueryParameter("comparison_uri", "STRING", comparison_uri),
            bigquery.ScalarQueryParameter("error", "STRING", error),
        ],
    )


def record_quality(job_id: str, quality: dict) -> None:
    query(
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


def record_step(job_id: str, input_uri: str, output_uri: str, status: str, started_at: datetime, details: dict) -> None:
    query(
        f"""
        INSERT INTO `{table_id('processing_steps')}`
          (job_id, scene_id, step_name, status, input_uri, output_uri, started_at, finished_at, details_json)
        VALUES (@job_id, 'video', 'cloud_run_gpu_render', @status, @input_uri, @output_uri,
                @started_at, @finished_at, @details_json)
        """,
        [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("input_uri", "STRING", input_uri),
            bigquery.ScalarQueryParameter("output_uri", "STRING", output_uri),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("finished_at", "TIMESTAMP", utc_now()),
            bigquery.ScalarQueryParameter("details_json", "STRING", json.dumps(details)),
        ],
    )


def render(input_path: Path, output_path: Path, profile: str, color_mode: str, color_style: str) -> str:
    settings = PROFILES[profile]
    render_output = output_path.with_name("restored_luma.mp4") if color_mode == "ai_natural" else output_path
    scale_flags = str(settings.get("scale_flags", "lanczos"))
    filters = [
        str(settings["denoise"]),
        f"scale='if(lt(iw,960),iw*2,iw)':'if(lt(iw,960),ih*2,ih)':flags={scale_flags}",
    ]
    if settings.get("deband"):
        filters.append(str(settings["deband"]))
    filters.append(str(settings["sharpen"]))
    if settings.get("tone"):
        filters.append(str(settings["tone"]))
    if color_mode == "enhance":
        filters.append(COLOR_ENHANCEMENT_FILTER)
    filters.append("format=yuv420p")

    common = [
        "ffmpeg", "-y", "-i", str(input_path), "-vf", ",".join(filters),
        "-map", "0:v:0", "-map", "0:a?", "-c:a", "aac", "-b:a", "128k",
    ]
    try:
        run(common + ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", str(render_output)])
        encoder = "h264_nvenc"
    except subprocess.CalledProcessError:
        run(
            common
            + [
                "-c:v", "libx264", "-preset", str(settings["preset"]),
                "-crf", str(settings["crf"]), str(render_output),
            ]
        )
        encoder = "libx264_fallback"
    if color_mode == "ai_natural":
        colorize_video_file(render_output, output_path, color_style=color_style, preset="veryfast", crf=20)
        render_output.unlink(missing_ok=True)
        return f"{encoder}+opencv_dnn_colorization"
    return encoder


def make_comparison(original: Path, restored: Path, output: Path) -> None:
    run(
        [
            "ffmpeg", "-y", "-i", str(original), "-i", str(restored),
            "-filter_complex",
            "[0:v]scale=640:-2,setsar=1[left];[1:v]scale=640:-2,setsar=1[right];[left][right]hstack=inputs=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", str(output),
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--input-uri", required=True)
    parser.add_argument("--output-uri", required=True)
    parser.add_argument("--comparison-uri", required=True)
    parser.add_argument("--profile", choices=["fast", "quality", "premium"], default="quality")
    parser.add_argument("--colorize", choices=["true", "false"], default="false")
    parser.add_argument("--color-mode", default=None)
    parser.add_argument("--color-style", default="historical_natural")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = normalize_profile(args.profile)
    color_mode = normalize_color_mode(args.color_mode, legacy_colorize=args.colorize == "true")
    started_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="refilm-") as tmp:
        work = Path(tmp)
        source = work / "original.mp4"
        restored = work / "restored.mp4"
        comparison = work / "comparison.mp4"
        try:
            update_job(args.job_id, "processing")
            download(args.input_uri, source)
            with ThreadPoolExecutor(max_workers=1) as executor:
                shots_future = executor.submit(
                    safe_analyze_and_record_shots,
                    args.job_id,
                    args.input_uri,
                    900,
                )
                encoder = render(source, restored, profile, color_mode, args.color_style)
                make_comparison(source, restored, comparison)
                quality = compare_videos(source, restored, sample_frames=12)
                shots_detected = len(shots_future.result())
            quality["worker"] = {
                "type": "cloud_run_gpu",
                "encoder": encoder,
                "profile": profile,
                "color_mode": color_mode,
                "color_style": args.color_style,
            }
            upload(restored, args.output_uri, "video/mp4")
            upload(comparison, args.comparison_uri, "video/mp4")
            record_file(args.job_id, "final", args.output_uri, restored, "video/mp4")
            record_file(args.job_id, "final", args.comparison_uri, comparison, "video/mp4")
            record_quality(args.job_id, quality)
            record_step(
                args.job_id,
                args.input_uri,
                args.output_uri,
                "completed",
                started_at,
                {
                    "profile": profile,
                    "encoder": encoder,
                    "colorize": color_mode != "none",
                    "color_mode": color_mode,
                    "color_style": args.color_style,
                    "shots_detected": shots_detected,
                },
            )
            update_job(
                args.job_id,
                "restored",
                final_uri=args.output_uri,
                comparison_uri=args.comparison_uri,
            )
        except Exception as exc:
            record_step(
                args.job_id,
                args.input_uri,
                args.output_uri,
                "error",
                started_at,
                {"profile": profile, "error": str(exc)},
            )
            update_job(args.job_id, "error", error=str(exc))
            raise


if __name__ == "__main__":
    main()
