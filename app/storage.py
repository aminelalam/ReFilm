from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from app.config import (
    ART_DIR,
    AUDIT_DIR,
    DATASET_DIR,
    FINAL_DIR,
    ORIGINALS_DIR,
    PROCESSED_DIR,
    SCENES_DIR,
    ensure_data_dirs,
)


class LocalBucket:
    """Adaptador local amb la mateixa estructura que el bucket cloud."""

    def __init__(self) -> None:
        ensure_data_dirs()

    def new_job_id(self) -> str:
        return uuid.uuid4().hex

    def save_original(self, job_id: str, filename: str, fileobj: BinaryIO) -> Path:
        job_dir = ORIGINALS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        destination = job_dir / Path(filename).name
        with destination.open("wb") as out:
            shutil.copyfileobj(fileobj, out)
        destination.chmod(0o444)
        return destination

    def delete_original_job(self, job_id: str) -> None:
        job_dir = ORIGINALS_DIR / job_id
        if job_dir.parent == ORIGINALS_DIR and job_dir.exists():
            for path in job_dir.rglob("*"):
                if path.is_file():
                    path.chmod(0o666)
            shutil.rmtree(job_dir)

    def scene_path(self, job_id: str, scene_id: str) -> Path:
        path = SCENES_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{scene_id}.mp4"

    def processed_path(self, job_id: str, scene_id: str, step_name: str) -> Path:
        path = PROCESSED_DIR / job_id / scene_id
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{step_name}.mp4"

    def final_path(self, job_id: str) -> Path:
        path = FINAL_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "restored.mp4"

    def comparison_path(self, job_id: str) -> Path:
        path = FINAL_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "comparison.mp4"

    def image_restored_path(self, job_id: str) -> Path:
        path = FINAL_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "restored.png"

    def image_colorized_path(self, job_id: str) -> Path:
        path = FINAL_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "colorized.png"

    def image_comparison_path(self, job_id: str) -> Path:
        path = FINAL_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "comparison.jpg"

    def audit_json_path(self, job_id: str) -> Path:
        path = AUDIT_DIR / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path / "audit.json"

    def art_path(self, audit_id: str, filename: str) -> Path:
        path = ART_DIR / audit_id
        path.mkdir(parents=True, exist_ok=True)
        return path / Path(filename).name

    def dataset_path(self, pair_id: str, filename: str) -> Path:
        path = DATASET_DIR / pair_id
        path.mkdir(parents=True, exist_ok=True)
        return path / Path(filename).name
