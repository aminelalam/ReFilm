import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ORIGINALS_DIR = DATA_DIR / "originals"
SCENES_DIR = DATA_DIR / "scenes"
PROCESSED_DIR = DATA_DIR / "processed"
FINAL_DIR = DATA_DIR / "final"
AUDIT_DIR = DATA_DIR / "audit"
ART_DIR = DATA_DIR / "art_audit"
DATASET_DIR = DATA_DIR / "dataset"
MODELS_DIR = DATA_DIR / "models"
DB_PATH = AUDIT_DIR / "refilm.sqlite3"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# El mode local ha de funcionar sense credencials ni xarxa.
# Cloud Run s'activa explícitament amb REFILM_CLOUD_ENABLED=true.
CLOUD_ENABLED = env_flag("REFILM_CLOUD_ENABLED")
CLOUD_RUN_JOBS_ENABLED = CLOUD_ENABLED and env_flag("REFILM_CLOUD_RUN_JOBS_ENABLED")
CLOUD_RUN_JOB_LOCATION = os.getenv("REFILM_CLOUD_RUN_JOB_LOCATION", "europe-west1")
CLOUD_RUN_RENDER_JOB = os.getenv("REFILM_CLOUD_RUN_RENDER_JOB", "refilm-render-gpu")
CLOUD_RUN_AI_JOB = os.getenv("REFILM_CLOUD_RUN_AI_JOB", "refilm-realesrgan-gpu")
QUALITY_SAMPLE_FRAMES = max(1, int(os.getenv("REFILM_QUALITY_SAMPLE_FRAMES", "12")))
COLORIZATION_MODEL_DIR = Path(os.getenv("REFILM_COLORIZATION_MODEL_DIR", str(MODELS_DIR / "colorization")))
COLORIZATION_AUTO_DOWNLOAD = env_flag("REFILM_COLORIZATION_AUTO_DOWNLOAD", True)
MAX_UPLOAD_BYTES = max(1, int(os.getenv("REFILM_MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024))))
MAX_VIDEO_DURATION_SECONDS = max(1, int(os.getenv("REFILM_MAX_VIDEO_DURATION_SECONDS", "3600")))
MAX_VIDEO_DIMENSION = max(1, int(os.getenv("REFILM_MAX_VIDEO_DIMENSION", "4096")))


def ensure_data_dirs() -> None:
    for path in [
        DATA_DIR,
        ORIGINALS_DIR,
        SCENES_DIR,
        PROCESSED_DIR,
        FINAL_DIR,
        AUDIT_DIR,
        ART_DIR,
        DATASET_DIR,
        MODELS_DIR,
        COLORIZATION_MODEL_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)
