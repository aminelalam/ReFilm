from pathlib import Path

import cv2
import numpy as np

import app.image_pipeline as image_pipeline_module
from app.audit import AuditStore
from app.image_pipeline import ImagePipeline
from app.storage import LocalBucket


def test_image_pipeline_ai_colorization_writes_non_gray_output(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = AuditStore(db_path)
    bucket = LocalBucket()
    job_id = bucket.new_job_id()
    original = bucket.dataset_path(job_id, "original.png")
    gray = np.full((32, 48, 3), 127, dtype=np.uint8)
    assert cv2.imwrite(str(original), gray)

    def fake_colorize(image: np.ndarray, *, color_style: str) -> tuple[np.ndarray, dict]:
        colored = image.copy()
        colored[:, :, 1] = 80
        colored[:, :, 2] = 180
        return colored, {"model_name": "test_colorizer", "model_version": "test"}

    monkeypatch.setattr(image_pipeline_module, "colorize_image_array", fake_colorize)
    store.create_job(
        job_id,
        "original.png",
        original,
        colorize=True,
        media_type="image",
        color_mode="ai_natural",
    )

    ImagePipeline(store, bucket).run(job_id, original, color_mode="ai_natural")

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "restored"
    output = cv2.imread(str(bucket.image_colorized_path(job_id)), cv2.IMREAD_COLOR)
    assert output is not None
    assert not np.array_equal(output[:, :, 0], output[:, :, 2])
