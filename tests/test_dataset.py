from pathlib import Path
import shutil

import cv2
import numpy as np

from app import dataset
from app.dataset import compare_reference_videos


def test_compare_reference_videos_reports_identical_clip(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mp4"
    candidate = tmp_path / "candidate.mp4"
    writer = cv2.VideoWriter(
        str(reference),
        cv2.VideoWriter_fourcc(*"mp4v"),
        4.0,
        (48, 32),
    )
    assert writer.isOpened()
    for value in [32, 96, 160, 224]:
        writer.write(np.full((32, 48, 3), value, dtype=np.uint8))
    writer.release()
    shutil.copyfile(reference, candidate)

    details = compare_reference_videos(reference, candidate)

    assert details["sampled_frames"] == 4
    assert details["ssim"] == 1.0
    assert details["psnr"] is None
    assert details["perfect_match"] is True
    assert details["vmaf"] == {"available": False, "reason": "not_requested"}


def test_compare_reference_videos_runs_vmaf_only_when_requested(tmp_path: Path, monkeypatch) -> None:
    reference = tmp_path / "reference.mp4"
    candidate = tmp_path / "candidate.mp4"
    writer = cv2.VideoWriter(str(reference), cv2.VideoWriter_fourcc(*"mp4v"), 2.0, (16, 16))
    assert writer.isOpened()
    writer.write(np.full((16, 16, 3), 127, dtype=np.uint8))
    writer.release()
    shutil.copyfile(reference, candidate)
    monkeypatch.setattr(
        dataset,
        "calculate_vmaf",
        lambda *args: {"available": True, "score": 100.0},
    )

    details = compare_reference_videos(reference, candidate, include_vmaf=True)

    assert details["vmaf"] == {"available": True, "score": 100.0}
