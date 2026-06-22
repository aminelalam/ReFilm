from __future__ import annotations

from pathlib import Path

import cv2

from app.config import MAX_UPLOAD_BYTES, MAX_VIDEO_DIMENSION, MAX_VIDEO_DURATION_SECONDS
from app.media import ffprobe_metadata


def validate_uploaded_video(path: Path) -> dict:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("Uploaded video is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded video exceeds the {MAX_UPLOAD_BYTES} byte limit.")

    try:
        metadata = ffprobe_metadata(path)
    except Exception as exc:
        raise ValueError("Uploaded file is not a readable video.") from exc

    streams = metadata.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("width") and stream.get("height")), None)
    if video_stream is None:
        raise ValueError("Uploaded file does not contain a video stream.")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    if max(width, height) > MAX_VIDEO_DIMENSION:
        raise ValueError(f"Video dimensions exceed the {MAX_VIDEO_DIMENSION}px limit.")

    duration = float(metadata.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Uploaded video duration could not be determined.")
    if duration > MAX_VIDEO_DURATION_SECONDS:
        raise ValueError(f"Video duration exceeds the {MAX_VIDEO_DURATION_SECONDS}s limit.")

    return metadata


def validate_uploaded_image(path: Path) -> dict:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("Uploaded image is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded image exceeds the {MAX_UPLOAD_BYTES} byte limit.")

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Uploaded file is not a readable image.")

    height, width = image.shape[:2]
    if max(width, height) > MAX_VIDEO_DIMENSION:
        raise ValueError(f"Image dimensions exceed the {MAX_VIDEO_DIMENSION}px limit.")

    return {"width": width, "height": height, "size_bytes": size}
