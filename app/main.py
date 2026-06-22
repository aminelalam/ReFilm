from __future__ import annotations

import shutil
import uuid
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.art_audit import ArtworkAuditor
from app.audit import AuditStore
from app.bigquery_audit import (
    get_download_uri_for_job,
    get_job_from_bigquery,
    list_jobs_from_bigquery,
    sync_job_metadata_to_bigquery,
)
from app.cloud_storage import build_gcs_uri, create_signed_download_url, download_gcs_uri_as_bytes
from app.cloud_sync import safe_sync_job_to_gcs, sync_job_to_gcs
from app.config import (
    BASE_DIR,
    CLOUD_ENABLED,
    CLOUD_RUN_JOBS_ENABLED,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ensure_data_dirs,
)
from app.dataset import compare_reference_images, compare_reference_videos, degrade_clip, degrade_image
from app.image_pipeline import ImagePipeline
from app.pipeline import VideoPipeline
from app.profiles import DEFAULT_COLOR_STYLE, normalize_color_mode, normalize_profile, public_color_modes, public_profiles
from app.services.audit_service import AuditService
from app.services.cloud_dispatcher import dispatch_cloud_restoration
from app.services.reports import build_job_report
from app.storage import LocalBucket
from app.video_intelligence import safe_analyze_and_record_shots
from app.validation import validate_uploaded_image, validate_uploaded_video

ensure_data_dirs()
logger = logging.getLogger(__name__)

VIDEO_FILE_KINDS = {"original", "final", "comparison", "audit", "restored", "colorized"}
IMAGE_FILE_KINDS = {"original", "restored", "colorized", "final", "comparison", "audit"}
IMAGE_PREVIEW_KINDS = {"original", "restored", "colorized", "final", "comparison"}
VIDEO_PREVIEW_KINDS = {"original", "final", "comparison"}

app = FastAPI(
    title="ReFilm MVP",
    description="MVP for historical video preservation/restoration and museum visual auditing.",
    version="0.1.0",
)


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

audit_store = AuditService(AuditStore())
bucket = LocalBucket()
pipeline = VideoPipeline(audit_store, bucket)
image_pipeline = ImagePipeline(audit_store, bucket)
art_auditor = ArtworkAuditor(audit_store, bucket)


def _run_video_job_with_cloud_sync(
    job_id: str,
    original_path: Path,
    colorize: bool,
    profile: str,
    color_mode: str,
    color_style: str,
) -> None:
    if not CLOUD_ENABLED:
        try:
            pipeline.run(
                job_id,
                original_path,
                colorize=colorize,
                profile=profile,
                color_mode=color_mode,
                color_style=color_style,
            )
        except Exception:
            logger.exception("Video pipeline failed for job %s", job_id)
        return

    # Flux cloud: persistim l'original abans d'analitzar-lo.
    safe_sync_job_to_gcs(job_id)

    # Video Intelligence treballa sobre la còpia de Cloud Storage.
    original_gcs_uri = build_gcs_uri(f"originals/{job_id}/{original_path.name}")

    safe_analyze_and_record_shots(
        job_id=job_id,
        source_uri=original_gcs_uri,
        timeout_seconds=900,
    )

    # El render continua localment i després es replica.
    try:
        pipeline.run(
            job_id,
            original_path,
            colorize=colorize,
            profile=profile,
            color_mode=color_mode,
            color_style=color_style,
        )
    except Exception:
        logger.exception("Video pipeline failed for job %s", job_id)
    finally:
        # Les sortides locals es repliquen a GCS i BigQuery.
        safe_sync_job_to_gcs(job_id)


def _run_image_job_with_cloud_sync(job_id: str, original_path: Path, color_mode: str, color_style: str) -> None:
    try:
        image_pipeline.run(job_id, original_path, color_mode=color_mode, color_style=color_style)
    except Exception:
        logger.exception("Image pipeline failed for job %s", job_id)
    finally:
        safe_sync_job_to_gcs(job_id)


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "index.html")


@app.post("/api/videos")
def upload_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    colorize: bool = Form(False),
    color_mode: str | None = Form(None),
    color_style: str = Form(DEFAULT_COLOR_STYLE),
    profile: str = Form("quality"),
) -> dict:
    _ensure_extension(video.filename, VIDEO_EXTENSIONS, "video")
    try:
        profile = normalize_profile(profile)
        resolved_color_mode = normalize_color_mode(color_mode, legacy_colorize=colorize)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = bucket.new_job_id()
    original_path = bucket.save_original(job_id, video.filename or "video.mp4", video.file)
    try:
        validate_uploaded_video(original_path)
    except ValueError as exc:
        bucket.delete_original_job(job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_store.create_job(
        job_id,
        video.filename or original_path.name,
        original_path,
        resolved_color_mode != "none",
        processing_profile=profile,
        media_type="video",
        color_mode=resolved_color_mode,
        color_style=color_style,
    )

    if CLOUD_RUN_JOBS_ENABLED:
        try:
            _dispatch_cloud_restoration(job_id, original_path, resolved_color_mode != "none", profile, resolved_color_mode, color_style)
            return {
                "job_id": job_id,
                "status": "queued",
                "colorize": resolved_color_mode != "none",
                "color_mode": resolved_color_mode,
                "profile": profile,
            }
        except Exception as exc:
            audit_store.event(
                job_id,
                "cloud_run",
                "cloud_run_job_dispatch_failed",
                str(exc),
                {"profile": profile, "fallback": "inline"},
            )

    background_tasks.add_task(
        _run_video_job_with_cloud_sync,
        job_id,
        original_path,
        resolved_color_mode != "none",
        profile,
        resolved_color_mode,
        color_style,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "colorize": resolved_color_mode != "none",
        "color_mode": resolved_color_mode,
        "profile": profile,
    }


def _dispatch_cloud_restoration(
    job_id: str,
    original_path: Path,
    colorize: bool,
    profile: str,
    color_mode: str | None = None,
    color_style: str = DEFAULT_COLOR_STYLE,
) -> None:
    dispatch_cloud_restoration(
        job_id=job_id,
        original_path=original_path,
        colorize=colorize,
        profile=profile,
        color_mode=color_mode,
        color_style=color_style,
        audit_store=audit_store,
        sync_job_to_gcs=sync_job_to_gcs,
        sync_job_metadata_to_bigquery=sync_job_metadata_to_bigquery,
        safe_sync_job_to_gcs=safe_sync_job_to_gcs,
    )


@app.get("/api/profiles")
def list_profiles() -> list[dict[str, str]]:
    return public_profiles()


@app.get("/api/color-modes")
def list_color_modes() -> list[dict[str, str]]:
    return public_color_modes()


@app.post("/api/images")
def upload_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    color_mode: str = Form("ai_natural"),
    color_style: str = Form(DEFAULT_COLOR_STYLE),
) -> dict:
    _ensure_extension(image.filename, IMAGE_EXTENSIONS, "image")
    try:
        resolved_color_mode = normalize_color_mode(color_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = bucket.new_job_id()
    original_path = bucket.save_original(job_id, image.filename or "image.jpg", image.file)
    try:
        validate_uploaded_image(original_path)
    except ValueError as exc:
        bucket.delete_original_job(job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_store.create_job(
        job_id,
        image.filename or original_path.name,
        original_path,
        resolved_color_mode != "none",
        processing_profile="image_quality",
        media_type="image",
        color_mode=resolved_color_mode,
        color_style=color_style,
    )
    background_tasks.add_task(_run_image_job_with_cloud_sync, job_id, original_path, resolved_color_mode, color_style)
    return {
        "job_id": job_id,
        "status": "pending",
        "media_type": "image",
        "color_mode": resolved_color_mode,
        "color_style": color_style,
    }


@app.get("/api/images/{job_id}")
def get_image_job(job_id: str) -> dict:
    job = get_job(job_id)
    if job.get("media_type") != "image":
        raise HTTPException(status_code=404, detail="Image job not found")
    return job


@app.get("/api/images/{job_id}/download/{kind}")
def download_image_file(job_id: str, kind: str) -> Response:
    if kind not in IMAGE_FILE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid image download kind")
    if CLOUD_ENABLED:
        try:
            gcs_uri = get_download_uri_for_job(job_id, kind)
            if gcs_uri:
                return _download_gcs_uri_response(gcs_uri)
        except Exception:
            logger.warning("Cloud image download failed for %s/%s; using local fallback", job_id, kind, exc_info=True)
    return _download_image_file_local(job_id, kind)


@app.get("/api/images/{job_id}/preview/{kind}")
def preview_image_file(job_id: str, kind: str) -> Response:
    if kind not in IMAGE_PREVIEW_KINDS:
        raise HTTPException(status_code=400, detail="Invalid image preview kind")
    if CLOUD_ENABLED:
        try:
            gcs_uri = get_download_uri_for_job(job_id, kind)
            if gcs_uri:
                return _download_gcs_uri_response(gcs_uri, inline=True)
        except Exception:
            logger.warning("Cloud image preview failed for %s/%s; using local fallback", job_id, kind, exc_info=True)
    return _download_image_file_local(job_id, kind, inline=True)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    if not CLOUD_ENABLED:
        return audit_store.list_jobs()

    try:
        return list_jobs_from_bigquery()
    except Exception:
        logger.warning("BigQuery job list failed; using SQLite fallback", exc_info=True)
        return audit_store.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if not CLOUD_ENABLED:
        job = audit_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    try:
        job = get_job_from_bigquery(job_id)
        if job is not None:
            return job
    except Exception:
        logger.warning("BigQuery job lookup failed for %s; using SQLite fallback", job_id, exc_info=True)

    job = audit_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/download/{kind}")
def download_job_file(job_id: str, kind: str) -> Response:
    if kind not in VIDEO_FILE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid download kind")
    if not CLOUD_ENABLED:
        return _download_job_file_local(job_id, kind)

    try:
        gcs_uri = get_download_uri_for_job(job_id, kind)

        if gcs_uri:
            return _download_gcs_uri_response(gcs_uri)
    except Exception:
        logger.warning("Cloud video download failed for %s/%s; using local fallback", job_id, kind, exc_info=True)

    return _download_job_file_local(job_id, kind)


@app.get("/api/jobs/{job_id}/preview/{kind}")
def preview_job_video(job_id: str, kind: str) -> Response:
    if kind not in VIDEO_PREVIEW_KINDS:
        raise HTTPException(status_code=400, detail="Invalid preview kind")

    if CLOUD_ENABLED:
        try:
            gcs_uri = get_download_uri_for_job(job_id, kind)
            if gcs_uri:
                return _download_gcs_uri_response(gcs_uri, inline=True)
        except Exception:
            logger.warning("Cloud video preview failed for %s/%s; using local fallback", job_id, kind, exc_info=True)

    return _download_job_file_local(job_id, kind, inline=True)


@app.get("/api/jobs/{job_id}/report")
def get_job_report(job_id: str) -> dict:
    job = get_job(job_id)
    return build_job_report(job_id, job)


@app.post("/api/dataset/degrade")
def create_damaged_pair(
    clean_video: UploadFile = File(...),
    seed: int | None = Form(None),
) -> dict:
    _ensure_extension(clean_video.filename, VIDEO_EXTENSIONS, "video")

    pair_id = uuid.uuid4().hex
    clean_path = bucket.dataset_path(pair_id, f"clean_{clean_video.filename or 'clip.mp4'}")
    damaged_path = clean_path.with_name("damaged.mp4")

    with clean_path.open("wb") as out:
        shutil.copyfileobj(clean_video.file, out)

    try:
        details = degrade_clip(clean_path, damaged_path, seed)
    except Exception as exc:
        safe_sync_job_to_gcs(pair_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    audit_store.event(pair_id, "dataset", "synthetic_damage_created", "Synthetic damaged pair created", details)

    uploaded_uris = safe_sync_job_to_gcs(pair_id)

    return {
        "pair_id": pair_id,
        "clean_path": str(clean_path),
        "damaged_path": str(damaged_path),
        "gcs_uris": uploaded_uris,
        "details": details,
    }


@app.post("/api/dataset/images/degrade")
def create_damaged_image_pair(
    clean_image: UploadFile = File(...),
    seed: int | None = Form(None),
) -> dict:
    _ensure_extension(clean_image.filename, IMAGE_EXTENSIONS, "image")

    pair_id = uuid.uuid4().hex
    clean_path = bucket.dataset_path(pair_id, f"clean_{clean_image.filename or 'image.jpg'}")
    damaged_path = bucket.dataset_path(pair_id, "damaged.jpg")
    with clean_path.open("wb") as out:
        shutil.copyfileobj(clean_image.file, out)

    try:
        details = degrade_image(clean_path, damaged_path, seed)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    audit_store.event(pair_id, "dataset", "synthetic_image_damage_created", "Synthetic image pair created", details)
    uploaded_uris = safe_sync_job_to_gcs(pair_id)
    return {
        "pair_id": pair_id,
        "clean_path": str(clean_path),
        "damaged_path": str(damaged_path),
        "gcs_uris": uploaded_uris,
        "details": details,
    }


@app.post("/api/dataset/images/evaluate")
def evaluate_restored_image(
    clean_image: UploadFile = File(...),
    restored_image: UploadFile = File(...),
) -> dict:
    _ensure_extension(clean_image.filename, IMAGE_EXTENSIONS, "clean image")
    _ensure_extension(restored_image.filename, IMAGE_EXTENSIONS, "restored image")

    evaluation_id = uuid.uuid4().hex
    clean_path = bucket.dataset_path(evaluation_id, f"reference_{clean_image.filename or 'clean.jpg'}")
    restored_path = bucket.dataset_path(
        evaluation_id,
        f"candidate_{restored_image.filename or 'restored.jpg'}",
    )
    with clean_path.open("wb") as out:
        shutil.copyfileobj(clean_image.file, out)
    with restored_path.open("wb") as out:
        shutil.copyfileobj(restored_image.file, out)

    try:
        details = compare_reference_images(clean_path, restored_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_store.event(
        evaluation_id,
        "dataset",
        "restored_image_evaluated",
        "Restored image compared with clean reference",
        details,
    )
    uploaded_uris = safe_sync_job_to_gcs(evaluation_id)
    return {"evaluation_id": evaluation_id, "gcs_uris": uploaded_uris, "details": details}


@app.post("/api/dataset/videos/evaluate")
def evaluate_restored_video(
    clean_video: UploadFile = File(...),
    restored_video: UploadFile = File(...),
    include_vmaf: bool = Form(False),
) -> dict:
    _ensure_extension(clean_video.filename, VIDEO_EXTENSIONS, "clean video")
    _ensure_extension(restored_video.filename, VIDEO_EXTENSIONS, "restored video")

    evaluation_id = uuid.uuid4().hex
    clean_path = bucket.dataset_path(evaluation_id, f"reference_{clean_video.filename or 'clean.mp4'}")
    restored_path = bucket.dataset_path(
        evaluation_id,
        f"candidate_{restored_video.filename or 'restored.mp4'}",
    )
    with clean_path.open("wb") as out:
        shutil.copyfileobj(clean_video.file, out)
    with restored_path.open("wb") as out:
        shutil.copyfileobj(restored_video.file, out)

    try:
        validate_uploaded_video(clean_path)
        validate_uploaded_video(restored_path)
        details = compare_reference_videos(clean_path, restored_path, include_vmaf=include_vmaf)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_store.event(
        evaluation_id,
        "dataset",
        "restored_video_evaluated",
        "Restored video compared with clean reference",
        details,
    )
    uploaded_uris = safe_sync_job_to_gcs(evaluation_id)
    return {"evaluation_id": evaluation_id, "gcs_uris": uploaded_uris, "details": details}


@app.post("/api/art-audits")
def create_art_audit(
    reference: UploadFile = File(...),
    current: UploadFile = File(...),
) -> dict:
    _ensure_extension(reference.filename, IMAGE_EXTENSIONS, "reference image")
    _ensure_extension(current.filename, IMAGE_EXTENSIONS, "current image")

    upload_id = uuid.uuid4().hex
    reference_path = bucket.art_path(upload_id, f"reference_{reference.filename or 'reference.jpg'}")
    current_path = bucket.art_path(upload_id, f"current_{current.filename or 'current.jpg'}")

    with reference_path.open("wb") as out:
        shutil.copyfileobj(reference.file, out)
    with current_path.open("wb") as out:
        shutil.copyfileobj(current.file, out)

    try:
        report = art_auditor.run(reference_path, current_path)
    except Exception as exc:
        safe_sync_job_to_gcs(upload_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    uploaded_uris = safe_sync_job_to_gcs(upload_id)
    report["gcs_uris"] = uploaded_uris

    return report


@app.get("/api/art-audits/{audit_id}/heatmap")
def download_heatmap(audit_id: str) -> Response:
    local_path = bucket.art_path(audit_id, "difference_heatmap.jpg")

    if local_path.exists():
        return FileResponse(local_path, filename=local_path.name)

    gcs_uri = build_gcs_uri(f"art_audit/{audit_id}/difference_heatmap.jpg")

    try:
        return _download_gcs_uri_response(gcs_uri)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Heatmap not found: {exc}") from exc


def _download_gcs_uri_response(gcs_uri: str, *, inline: bool = False) -> Response:
    try:
        return RedirectResponse(create_signed_download_url(gcs_uri, inline=inline), status_code=307)
    except Exception:
        logger.warning("Signed URL unavailable for %s; proxying download", gcs_uri, exc_info=True)

    data, filename, content_type = download_gcs_uri_as_bytes(gcs_uri)

    disposition = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{filename}"'}

    return Response(
        content=data,
        media_type=content_type,
        headers=headers,
    )


def _download_job_file_local(job_id: str, kind: str, *, inline: bool = False) -> FileResponse:
    job = audit_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("media_type") == "image":
        return _download_image_file_local(job_id, kind, inline=inline)

    candidates = {
        "original": Path(job["original_path"]),
        "final": Path(job["final_path"]) if job.get("final_path") else None,
        "comparison": bucket.comparison_path(job_id),
        "audit": bucket.audit_json_path(job_id),
    }

    path = candidates.get(kind)

    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=f"{kind} file is not available yet")

    if inline:
        return FileResponse(path)
    return FileResponse(path, filename=path.name)


def _download_image_file_local(job_id: str, kind: str, *, inline: bool = False) -> FileResponse:
    job = audit_store.get_job(job_id)
    if job is None or job.get("media_type") != "image":
        raise HTTPException(status_code=404, detail="Image job not found")

    final_path = Path(job["final_path"]) if job.get("final_path") else None
    candidates = {
        "original": Path(job["original_path"]),
        "restored": bucket.image_restored_path(job_id),
        "colorized": bucket.image_colorized_path(job_id),
        "final": final_path,
        "comparison": bucket.image_comparison_path(job_id),
        "audit": bucket.audit_json_path(job_id),
    }
    path = candidates.get(kind)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail=f"{kind} file is not available yet")
    if inline:
        return FileResponse(path)
    return FileResponse(path, filename=path.name)


def _ensure_extension(filename: str | None, allowed: set[str], label: str) -> None:
    suffix = Path(filename or "").suffix.lower()

    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label} extension. Allowed: {', '.join(sorted(allowed))}",
        )
