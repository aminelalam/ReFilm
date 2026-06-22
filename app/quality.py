from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


ANALYSIS_WIDTH = 640


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 4) if values else None


def _ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline in {None, 0}:
        return None
    return round(candidate / baseline, 4)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return round(candidate - baseline, 4)


def _sample_indices(total_frames: int, sample_frames: int) -> list[int]:
    if total_frames <= 0:
        return []
    count = min(max(1, sample_frames), total_frames)
    return sorted(set(int(round(value)) for value in np.linspace(0, total_frames - 1, count)))


def analyze_video(path: Path, *, sample_frames: int) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video for quality analysis: {path}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    sharpness: list[float] = []
    contrast: list[float] = []
    brightness: list[float] = []
    noise: list[float] = []
    saturation: list[float] = []
    temporal_change: list[float] = []
    previous_small: np.ndarray | None = None

    try:
        for index in _sample_indices(total_frames, sample_frames):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue

            analysis_height = max(1, round(frame.shape[0] * ANALYSIS_WIDTH / frame.shape[1]))
            normalized = cv2.resize(frame, (ANALYSIS_WIDTH, analysis_height), interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(normalized, cv2.COLOR_BGR2HSV)
            residual = gray.astype(np.float32) - cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)

            sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            contrast.append(float(np.std(gray)))
            brightness.append(float(np.mean(gray)))
            noise.append(float(np.std(residual)))
            saturation.append(float(np.mean(hsv[:, :, 1])))

            small = cv2.resize(gray, (160, 120), interpolation=cv2.INTER_AREA).astype(np.float32)
            if previous_small is not None:
                temporal_change.append(float(np.mean(np.abs(small - previous_small))))
            previous_small = small
    finally:
        capture.release()

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 4),
        "duration_seconds": round(total_frames / fps, 4) if fps else None,
        "sampled_frames": len(sharpness),
        "sharpness": _mean(sharpness),
        "contrast": _mean(contrast),
        "brightness": _mean(brightness),
        "noise_estimate": _mean(noise),
        "saturation": _mean(saturation),
        "temporal_change_estimate": _mean(temporal_change),
    }


def compare_videos(original_path: Path, restored_path: Path, *, sample_frames: int) -> dict[str, Any]:
    original = analyze_video(original_path, sample_frames=sample_frames)
    restored = analyze_video(restored_path, sample_frames=sample_frames)
    return {
        "original": original,
        "restored": restored,
        "change": {
            "resolution_scale": _ratio(restored["width"], original["width"]),
            "sharpness_gain": _ratio(restored["sharpness"], original["sharpness"]),
            "contrast_gain": _ratio(restored["contrast"], original["contrast"]),
            "brightness_delta": _delta(restored["brightness"], original["brightness"]),
            "noise_delta": _delta(restored["noise_estimate"], original["noise_estimate"]),
            "saturation_delta": _delta(restored["saturation"], original["saturation"]),
            "temporal_change_delta": _delta(
                restored["temporal_change_estimate"],
                original["temporal_change_estimate"],
            ),
        },
        "notes": {
            "sharpness": "Variance of Laplacian after size normalization. Higher usually means sharper but can include artifacts.",
            "noise_estimate": "Approximate high-frequency residual; lower is usually cleaner.",
            "temporal_change_estimate": "Approximate change between sampled frames, not a reference metric.",
        },
    }
