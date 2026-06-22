from __future__ import annotations

import math
import random
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from app.media import ffmpeg_filter


DEGRADATION_FILTERS = [
    "noise=alls=24:allf=t+u,eq=contrast=1.05:brightness=-0.02",
    "gblur=sigma=1.2,noise=alls=18:allf=t",
    "eq=brightness=-0.05:saturation=0.75,unsharp=3:3:0.2",
    "vignette=PI/4,noise=alls=20:allf=t",
]
VMAF_SCORE_PATTERN = re.compile(r"VMAF score:\s*([0-9.]+)")


def degrade_clip(clean_path: Path, damaged_path: Path, seed: int | None = None) -> dict:
    """Crea un parell de vídeo degradat per al dataset."""

    rng = random.Random(seed)
    filter_expr = rng.choice(DEGRADATION_FILTERS)
    ffmpeg_filter(clean_path, damaged_path, filter_expr)
    return {"filter": filter_expr, "seed": seed}


def degrade_image(clean_path: Path, damaged_path: Path, seed: int | None = None) -> dict:
    """Crea una imatge degradada reproduïble i les mètriques de referència."""
    image = cv2.imread(str(clean_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not read the clean image.")

    rng = np.random.default_rng(seed)
    height, width = image.shape[:2]
    small = cv2.resize(image, (max(1, width // 2), max(1, height // 2)), interpolation=cv2.INTER_AREA)
    damaged = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    damaged = cv2.GaussianBlur(damaged, (5, 5), sigmaX=1.1)
    noise = rng.normal(0, 10, damaged.shape).astype(np.float32)
    damaged = np.clip(damaged.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    damaged_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(damaged_path), damaged):
        raise RuntimeError(f"Could not write damaged image: {damaged_path}")

    clean_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    damaged_gray = cv2.cvtColor(damaged, cv2.COLOR_BGR2GRAY)
    return {
        "seed": seed,
        "degradations": ["downscale_x2", "gaussian_blur_sigma_1.1", "gaussian_noise_sigma_10"],
        "width": width,
        "height": height,
        "ssim": round(float(structural_similarity(clean_gray, damaged_gray, data_range=255)), 4),
        "psnr": round(float(peak_signal_noise_ratio(image, damaged, data_range=255)), 4),
    }


def compare_reference_images(clean_path: Path, candidate_path: Path) -> dict:
    """Compara una imatge restaurada amb la seva referència neta."""
    clean = cv2.imread(str(clean_path), cv2.IMREAD_COLOR)
    candidate = cv2.imread(str(candidate_path), cv2.IMREAD_COLOR)
    if clean is None or candidate is None:
        raise ValueError("Could not read both reference images.")

    clean_height, clean_width = clean.shape[:2]
    candidate_height, candidate_width = candidate.shape[:2]
    if (candidate_width, candidate_height) != (clean_width, clean_height):
        candidate = cv2.resize(candidate, (clean_width, clean_height), interpolation=cv2.INTER_AREA)

    clean_gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
    candidate_gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
    psnr = float("inf") if np.array_equal(clean, candidate) else float(
        peak_signal_noise_ratio(clean, candidate, data_range=255)
    )
    perfect_match = math.isinf(psnr)
    return {
        "reference_width": clean_width,
        "reference_height": clean_height,
        "candidate_width": candidate_width,
        "candidate_height": candidate_height,
        "candidate_resized_for_metrics": (candidate_width, candidate_height) != (clean_width, clean_height),
        "ssim": round(float(structural_similarity(clean_gray, candidate_gray, data_range=255)), 4),
        "psnr": None if perfect_match else round(psnr, 4),
        "perfect_match": perfect_match,
    }


def compare_reference_videos(
    clean_path: Path,
    candidate_path: Path,
    sample_frames: int = 48,
    *,
    include_vmaf: bool = False,
) -> dict:
    """Calcula PSNR i SSIM amb frames mostrejats dels dos vídeos."""
    clean_capture = cv2.VideoCapture(str(clean_path))
    candidate_capture = cv2.VideoCapture(str(candidate_path))
    if not clean_capture.isOpened() or not candidate_capture.isOpened():
        raise ValueError("Could not read both reference videos.")

    try:
        clean_count = max(1, int(clean_capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        candidate_count = max(1, int(candidate_capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        clean_width = int(clean_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        clean_height = int(clean_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        candidate_width = int(candidate_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        candidate_height = int(candidate_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count = max(1, min(sample_frames, clean_count, candidate_count))
        positions = np.linspace(0.0, 1.0, count)
        squared_error = 0.0
        compared_values = 0
        ssim_values: list[float] = []

        for position in positions:
            clean_capture.set(cv2.CAP_PROP_POS_FRAMES, round(position * (clean_count - 1)))
            candidate_capture.set(cv2.CAP_PROP_POS_FRAMES, round(position * (candidate_count - 1)))
            clean_ok, clean = clean_capture.read()
            candidate_ok, candidate = candidate_capture.read()
            if not clean_ok or not candidate_ok:
                continue
            if candidate.shape[:2] != clean.shape[:2]:
                candidate = cv2.resize(candidate, (clean_width, clean_height), interpolation=cv2.INTER_AREA)

            difference = clean.astype(np.float32) - candidate.astype(np.float32)
            squared_error += float(np.sum(difference * difference))
            compared_values += int(difference.size)
            ssim_values.append(
                float(
                    structural_similarity(
                        cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY),
                        cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY),
                        data_range=255,
                    )
                )
            )

        if not ssim_values or not compared_values:
            raise ValueError("No comparable frames were found in the reference videos.")

        mse = squared_error / compared_values
        psnr = float("inf") if mse == 0 else 10 * math.log10((255**2) / mse)
        perfect_match = math.isinf(psnr)
        return {
            "reference_width": clean_width,
            "reference_height": clean_height,
            "candidate_width": candidate_width,
            "candidate_height": candidate_height,
            "candidate_resized_for_metrics": (candidate_width, candidate_height) != (clean_width, clean_height),
            "sampled_frames": len(ssim_values),
            "ssim": round(float(np.mean(ssim_values)), 4),
            "psnr": None if perfect_match else round(psnr, 4),
            "perfect_match": perfect_match,
            "vmaf": (
                calculate_vmaf(clean_path, candidate_path, clean_width, clean_height)
                if include_vmaf
                else {"available": False, "reason": "not_requested"}
            ),
        }
    finally:
        clean_capture.release()
        candidate_capture.release()


def calculate_vmaf(clean_path: Path, candidate_path: Path, width: int, height: int) -> dict:
    """Retorna VMAF si la instal·lació de FFmpeg inclou libvmaf."""
    if shutil.which("ffmpeg") is None:
        return {"available": False, "reason": "ffmpeg_not_installed"}

    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "libvmaf" not in filters.stdout:
        return {"available": False, "reason": "libvmaf_filter_not_available"}

    filter_graph = (
        f"[0:v]scale={width}:{height}:flags=bicubic[distorted];"
        f"[1:v]scale={width}:{height}:flags=bicubic[reference];"
        "[distorted][reference]libvmaf"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(candidate_path),
            "-i",
            str(clean_path),
            "-lavfi",
            filter_graph,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = VMAF_SCORE_PATTERN.search(result.stderr)
    if result.returncode != 0 or match is None:
        return {"available": False, "reason": "vmaf_calculation_failed"}
    return {"available": True, "score": round(float(match.group(1)), 4)}
