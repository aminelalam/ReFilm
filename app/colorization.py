from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np
try:
    from skimage.segmentation import slic
except ModuleNotFoundError:  # Optional fallback for minimal installs.
    slic = None  # type: ignore[assignment]

from app.config import COLORIZATION_AUTO_DOWNLOAD, COLORIZATION_MODEL_DIR
from app.media import run_command
from app.profiles import DEFAULT_COLOR_STYLE


MODEL_NAME = "zhang_colorization_opencv_dnn"
MODEL_VERSION = "colorization_release_v2"
MODEL_FILES = {
    "prototxt": {
        "path": COLORIZATION_MODEL_DIR / "colorization_deploy_v2.prototxt",
        "url": "https://storage.openvinotoolkit.org/repositories/datumaro/models/colorization/colorization_deploy_v2.prototxt",
    },
    "caffemodel": {
        "path": COLORIZATION_MODEL_DIR / "colorization_release_v2.caffemodel",
        "url": "https://storage.openvinotoolkit.org/repositories/datumaro/models/colorization/colorization_release_v2.caffemodel",
    },
    "points": {
        "path": COLORIZATION_MODEL_DIR / "pts_in_hull.npy",
        "url": "https://storage.openvinotoolkit.org/repositories/datumaro/models/colorization/pts_in_hull.npy",
    },
}

STYLE_SETTINGS = {
    "historical_natural": {
        "chroma_scale": 0.52,
        "max_chroma": 22.0,
        "saturation_soft_cap": 62.0,
        "saturation_hard_cap": 96.0,
        "contrast": 1.01,
    },
    "natural": {
        "chroma_scale": 0.64,
        "max_chroma": 28.0,
        "saturation_soft_cap": 78.0,
        "saturation_hard_cap": 118.0,
        "contrast": 1.02,
    },
    "vivid": {
        "chroma_scale": 0.82,
        "max_chroma": 36.0,
        "saturation_soft_cap": 104.0,
        "saturation_hard_cap": 150.0,
        "contrast": 1.03,
    },
    "conservative": {
        "chroma_scale": 0.38,
        "max_chroma": 16.0,
        "saturation_soft_cap": 46.0,
        "saturation_hard_cap": 76.0,
        "contrast": 1.0,
    },
}

_NET: cv2.dnn.Net | None = None


class ColorizationModelUnavailable(RuntimeError):
    """El model neuronal de colorització no està disponible."""


def model_metadata() -> dict[str, str]:
    return {"model_name": MODEL_NAME, "model_version": MODEL_VERSION}


def model_assets_available() -> bool:
    return all(item["path"].exists() for item in MODEL_FILES.values())


def ensure_model_assets() -> None:
    COLORIZATION_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    missing = [key for key, item in MODEL_FILES.items() if not item["path"].exists()]
    if not missing:
        return
    if not COLORIZATION_AUTO_DOWNLOAD:
        paths = ", ".join(str(MODEL_FILES[key]["path"]) for key in missing)
        raise ColorizationModelUnavailable(
            "Real colorization model assets are missing and auto-download is disabled. "
            f"Missing files: {paths}"
        )

    for key in missing:
        item = MODEL_FILES[key]
        destination = item["path"]
        temporary = destination.with_suffix(destination.suffix + ".download")
        try:
            urllib.request.urlretrieve(item["url"], temporary)
            temporary.replace(destination)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise ColorizationModelUnavailable(
                f"Could not download real colorization model asset '{key}' from {item['url']}: {exc}"
            ) from exc


def _load_net() -> cv2.dnn.Net:
    global _NET
    if _NET is not None:
        return _NET

    ensure_model_assets()
    prototxt = MODEL_FILES["prototxt"]["path"]
    caffemodel = MODEL_FILES["caffemodel"]["path"]
    points_path = MODEL_FILES["points"]["path"]
    net = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
    points = np.load(str(points_path)).transpose().reshape(2, 313, 1, 1).astype(np.float32)
    net.getLayer(net.getLayerId("class8_ab")).blobs = [points]
    net.getLayer(net.getLayerId("conv8_313_rh")).blobs = [np.full([1, 313], 2.606, dtype=np.float32)]
    _NET = net
    return net


def restore_image_array(image: np.ndarray) -> np.ndarray:
    """Restauració prudent abans de coloritzar."""
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    denoised = cv2.fastNlMeansDenoisingColored(image, None, 4, 4, 7, 21)
    denoised = cv2.bilateralFilter(denoised, 5, 18, 9)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.25, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(l_channel)
    enhanced = cv2.cvtColor(cv2.merge([enhanced_l, a_channel, b_channel]), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.15)
    sharpened = cv2.addWeighted(enhanced, 1.12, blurred, -0.12, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def enhance_existing_color(image: np.ndarray) -> np.ndarray:
    """Millora clàssica per a material que ja té color."""
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    l_channel = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(l_channel)
    enhanced = cv2.cvtColor(cv2.merge([l_channel, a_channel, b_channel]), cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.08, 0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.01, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _style_settings(color_style: str) -> dict[str, float]:
    return STYLE_SETTINGS.get(color_style, STYLE_SETTINGS[DEFAULT_COLOR_STYLE])


def _smooth_predicted_ab(predicted_ab: np.ndarray) -> np.ndarray:
    channels = []
    for index in range(2):
        channels.append(cv2.bilateralFilter(predicted_ab[:, :, index].astype(np.float32), 7, 16, 9))
    return np.dstack(channels).astype(np.float32)


def _luminance_chroma_mask(luminance: np.ndarray) -> np.ndarray:
    low = np.clip((luminance - 10.0) / 18.0, 0.0, 1.0)
    high = np.clip((96.0 - luminance) / 18.0, 0.0, 1.0)
    mask = cv2.GaussianBlur(low * high, (0, 0), 1.2)
    return mask[:, :, np.newaxis].astype(np.float32)


def _limit_chroma(predicted_ab: np.ndarray, max_chroma: float) -> np.ndarray:
    magnitude = np.linalg.norm(predicted_ab, axis=2, keepdims=True)
    scale = np.minimum(1.0, max_chroma / np.maximum(magnitude, 1e-4))
    return predicted_ab * scale


def _segment_count(width: int, height: int) -> int:
    return int(np.clip((width * height) / 1100, 80, 900))


def _lock_chroma_by_regions(source_bgr: np.ndarray, predicted_ab: np.ndarray) -> np.ndarray:
    """Manté el color coherent dins de regions visuals."""
    def smooth_ab(chroma: np.ndarray, diameter: int, sigma_color: float, sigma_space: float) -> np.ndarray:
        return np.dstack(
            [
                cv2.bilateralFilter(chroma[:, :, channel].astype(np.float32), diameter, sigma_color, sigma_space)
                for channel in range(2)
            ]
        ).astype(np.float32)

    if slic is None:
        return smooth_ab(predicted_ab, 9, 22, 15)

    height, width = source_bgr.shape[:2]
    source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    labels = slic(
        source_rgb,
        n_segments=_segment_count(width, height),
        compactness=14.0,
        sigma=1.0,
        start_label=0,
        channel_axis=-1,
    )
    gray = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Laplacian(gray, cv2.CV_32F)
    locked = predicted_ab.copy().astype(np.float32)
    for label in np.unique(labels):
        mask = labels == label
        if int(mask.sum()) < 8:
            continue
        region_ab = predicted_ab[mask]
        median_ab = np.median(region_ab, axis=0)
        mean_ab = region_ab.mean(axis=0)
        target_ab = 0.65 * median_ab + 0.35 * mean_ab
        edge_strength = float(np.mean(np.abs(edges[mask])))
        blend = 0.88 if edge_strength < 5.0 else 0.72
        locked[mask] = (1.0 - blend) * region_ab + blend * target_ab
    return smooth_ab(locked, 5, 10, 7)


def _compress_saturation(image: np.ndarray, *, soft_cap: float, hard_cap: float) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    saturation = hsv[:, :, 1]
    over_soft = saturation > soft_cap
    saturation[over_soft] = soft_cap + (saturation[over_soft] - soft_cap) * 0.35
    hsv[:, :, 1] = np.clip(saturation, 0, hard_cap)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _finish_colorized_bgr(image: np.ndarray, luminance: np.ndarray, settings: dict[str, float]) -> np.ndarray:
    image = _compress_saturation(
        image,
        soft_cap=float(settings["saturation_soft_cap"]),
        hard_cap=float(settings["saturation_hard_cap"]),
    )
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] = np.clip(luminance * 255.0 / 100.0, 0, 255)
    image = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(image, (0, 0), 0.6)
    return cv2.addWeighted(image, float(settings["contrast"]), blurred, 1.0 - float(settings["contrast"]), 0)


def colorize_image_array(
    image: np.ndarray,
    *,
    color_style: str = DEFAULT_COLOR_STYLE,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Coloritza amb un model Caffe carregat amb OpenCV DNN."""
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    net = _load_net()
    settings = _style_settings(color_style)
    chroma_scale = float(settings["chroma_scale"])

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    height, width = rgb.shape[:2]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    luminance = lab[:, :, 0]

    resized = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_AREA)
    resized_lab = cv2.cvtColor(resized, cv2.COLOR_RGB2LAB)
    resized_luminance = resized_lab[:, :, 0] - 50

    net.setInput(cv2.dnn.blobFromImage(resized_luminance))
    predicted_ab = net.forward()[0, :, :, :].transpose((1, 2, 0))
    predicted_ab = cv2.resize(predicted_ab, (width, height), interpolation=cv2.INTER_CUBIC)
    predicted_ab = _smooth_predicted_ab(predicted_ab)
    predicted_ab *= chroma_scale
    predicted_ab *= _luminance_chroma_mask(luminance)
    predicted_ab = _limit_chroma(predicted_ab, float(settings["max_chroma"]))
    predicted_ab = _lock_chroma_by_regions(image, predicted_ab)
    predicted_ab = _limit_chroma(predicted_ab, float(settings["max_chroma"]))

    colorized_lab = np.concatenate((luminance[:, :, np.newaxis], predicted_ab), axis=2)
    colorized_rgb = cv2.cvtColor(colorized_lab, cv2.COLOR_LAB2RGB)
    colorized_bgr = cv2.cvtColor(np.clip(colorized_rgb * 255, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    colorized_bgr = _finish_colorized_bgr(colorized_bgr, luminance, settings)

    details = {
        **model_metadata(),
        "color_style": color_style,
        "chroma_scale": chroma_scale,
        "max_chroma": settings["max_chroma"],
        "saturation_soft_cap": settings["saturation_soft_cap"],
        "saturation_hard_cap": settings["saturation_hard_cap"],
        "engine": "opencv_dnn_caffe",
        "postprocess": "luminance_gated_region_locked_chroma_saturation_compression",
    }
    return colorized_bgr, details


def colorize_image_file(
    input_path: Path,
    output_path: Path,
    *,
    color_style: str = DEFAULT_COLOR_STYLE,
) -> dict[str, Any]:
    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image for colorization: {input_path}")
    colorized, details = colorize_image_array(image, color_style=color_style)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), colorized):
        raise RuntimeError(f"Could not write colorized image: {output_path}")
    return details | {"width": int(colorized.shape[1]), "height": int(colorized.shape[0])}


def colorize_video_file(
    input_path: Path,
    output_path: Path,
    *,
    color_style: str = DEFAULT_COLOR_STYLE,
    fps: str | None = None,
    preset: str = "veryfast",
    crf: int = 20,
    temporal_smoothing: float = 0.28,
) -> dict[str, Any]:
    """Coloritza els frames i estabilitza la crominància abans de remultiplexar l'àudio."""
    work_dir = output_path.with_name(f"{output_path.stem}_color_work")
    frames_dir = work_dir / "frames"
    colorized_dir = work_dir / "colorized_frames"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir.mkdir(parents=True)
    colorized_dir.mkdir(parents=True)
    running_mean: np.ndarray | None = None
    running_std: np.ndarray | None = None
    frame_count = 0
    details: dict[str, Any] = {}

    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vsync",
                "0",
                str(frames_dir / "frame_%08d.png"),
            ]
        )
        for frame_path in sorted(frames_dir.glob("frame_*.png")):
            frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError(f"Could not read extracted frame: {frame_path}")
            colorized, details = colorize_image_array(frame, color_style=color_style)
            lab = cv2.cvtColor(colorized, cv2.COLOR_BGR2LAB).astype(np.float32)
            current_ab = lab[:, :, 1:3]
            current_mean = current_ab.reshape(-1, 2).mean(axis=0)
            current_std = current_ab.reshape(-1, 2).std(axis=0) + 1e-3
            if running_mean is None or running_std is None:
                running_mean = current_mean
                running_std = current_std
            else:
                running_mean = (1.0 - temporal_smoothing) * running_mean + temporal_smoothing * current_mean
                running_std = (1.0 - temporal_smoothing) * running_std + temporal_smoothing * current_std
                adjusted_ab = (current_ab - current_mean) * (running_std / current_std) + running_mean
                lab[:, :, 1:3] = np.clip(adjusted_ab, 0, 255)
                colorized = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
            if not cv2.imwrite(str(colorized_dir / frame_path.name), colorized):
                raise RuntimeError(f"Could not write colorized frame: {frame_path.name}")
            frame_count += 1

        if frame_count == 0:
            raise RuntimeError("No video frames were extracted for colorization.")

        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            fps or _video_fps(input_path),
            "-i",
            str(colorized_dir / "frame_%08d.png"),
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output_path),
        ]
        run_command(command)
        return details | {
            "frames_processed": frame_count,
            "temporal_smoothing": temporal_smoothing,
            "temporal_stabilization": "global_chroma_mean_std",
            "audio_preserved": True,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def make_image_comparison(original: Path, restored: Path, output_path: Path) -> None:
    left = cv2.imread(str(original), cv2.IMREAD_COLOR)
    right = cv2.imread(str(restored), cv2.IMREAD_COLOR)
    if left is None or right is None:
        raise ValueError("Could not read images for comparison.")
    target_height = min(left.shape[0], right.shape[0], 900)
    left_width = max(1, round(left.shape[1] * target_height / left.shape[0]))
    right_width = max(1, round(right.shape[1] * target_height / right.shape[0]))
    left = cv2.resize(left, (left_width, target_height), interpolation=cv2.INTER_AREA)
    right = cv2.resize(right, (right_width, target_height), interpolation=cv2.INTER_AREA)
    gutter = np.full((target_height, 12, 3), 245, dtype=np.uint8)
    comparison = np.hstack([left, gutter, right])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), comparison):
        raise RuntimeError(f"Could not write image comparison: {output_path}")


def analyze_image(path: Path) -> dict[str, Any]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image for analysis: {path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    residual = gray.astype(np.float32) - cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    return {
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4),
        "contrast": round(float(np.std(gray)), 4),
        "brightness": round(float(np.mean(gray)), 4),
        "noise_estimate": round(float(np.std(residual)), 4),
        "saturation": round(float(np.mean(hsv[:, :, 1])), 4),
        "chrominance": round(float(np.mean(np.abs(lab[:, :, 1:3].astype(np.float32) - 128))), 4),
    }


def compare_images(original_path: Path, restored_path: Path) -> dict[str, Any]:
    original = analyze_image(original_path)
    restored = analyze_image(restored_path)

    def ratio(metric: str) -> float | None:
        before = original.get(metric)
        after = restored.get(metric)
        return round(after / before, 4) if before not in {None, 0} and after is not None else None

    def delta(metric: str) -> float | None:
        before = original.get(metric)
        after = restored.get(metric)
        return round(after - before, 4) if before is not None and after is not None else None

    return {
        "original": original,
        "restored": restored,
        "change": {
            "sharpness_gain": ratio("sharpness"),
            "contrast_gain": ratio("contrast"),
            "brightness_delta": delta("brightness"),
            "noise_delta": delta("noise_estimate"),
            "saturation_delta": delta("saturation"),
            "chrominance_delta": delta("chrominance"),
        },
    }


def _video_fps(input_path: Path) -> str:
    result = run_command(
        [
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
    )
    return result.stdout.strip() or "24"
