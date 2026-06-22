from __future__ import annotations

from pathlib import Path

try:
    from scenedetect import ContentDetector, detect
except ModuleNotFoundError:  # Optional dependency for local scene splitting.
    ContentDetector = None  # type: ignore[assignment]
    detect = None  # type: ignore[assignment]


def detect_scene_ranges(path: Path) -> tuple[list[tuple[float, float]], str]:
    if detect is None or ContentDetector is None:
        return [], "single_scene_fallback_no_pyscenedetect"

    scenes = detect(str(path), ContentDetector(threshold=30.0, min_scene_len=24))
    ranges = [
        (round(start.get_seconds(), 4), round(end.get_seconds(), 4))
        for start, end in scenes
        if end.get_seconds() > start.get_seconds()
    ]
    return ranges, "pyscenedetect_content_detector"
