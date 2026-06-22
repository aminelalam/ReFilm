from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = PROJECT_ROOT / "datasets"
GENERATED_DIR = DATASET_DIR / "generated"
sys.path.insert(0, str(PROJECT_ROOT))

from app.dataset import degrade_clip, degrade_image


def relative(path: Path) -> str:
    return path.relative_to(DATASET_DIR).as_posix()


def generate_image(index: int, output: Path) -> None:
    rng = np.random.default_rng(index)
    height, width = 240, 320
    gradient = np.linspace(25, 220, width, dtype=np.uint8)
    image = np.repeat(gradient[np.newaxis, :, np.newaxis], height, axis=0)
    image = np.repeat(image, 3, axis=2)
    image = np.clip(image + rng.integers(-18, 19, image.shape, dtype=np.int16), 0, 255).astype(np.uint8)
    cv2.rectangle(image, (20 + index, 30), (150, 180), (180, 90, 45), 3)
    cv2.circle(image, (220, 120), 35 + index % 20, (35, 180, 210), 4)
    cv2.putText(image, f"ReFilm {index:02d}", (35, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (245, 245, 245), 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise RuntimeError(f"Could not write generated image: {output}")


def generate_video(index: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=160x120:rate=8:duration=2,hue=h={index * 12}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def generate(image_count: int, video_count: int, manifest_path: Path) -> dict:
    images_dir = GENERATED_DIR / "images"
    videos_dir = GENERATED_DIR / "videos"
    image_cases = []
    video_cases = []

    for index in range(image_count):
        clean = images_dir / f"clean_{index:02d}.png"
        damaged = images_dir / f"damaged_{index:02d}.jpg"
        generate_image(index, clean)
        degrade_image(clean, damaged, seed=index)
        image_cases.append(
            {
                "id": f"synthetic-image-{index:02d}-damaged-baseline",
                "reference": relative(clean),
                "candidate": relative(damaged),
                "profile": "synthetic_damage_baseline",
            }
        )

    for index in range(video_count):
        clean = videos_dir / f"clean_{index:02d}.mp4"
        damaged = videos_dir / f"damaged_{index:02d}.mp4"
        generate_video(index, clean)
        degrade_clip(clean, damaged, seed=index)
        video_cases.append(
            {
                "id": f"synthetic-video-{index:02d}-damaged-baseline",
                "reference": relative(clean),
                "candidate": relative(damaged),
                "profile": "synthetic_damage_baseline",
            }
        )

    manifest = {
        "version": 1,
        "description": "Generated deterministic ReFilm baseline benchmark.",
        "image_cases": image_cases,
        "video_cases": video_cases,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=20)
    parser.add_argument("--videos", type=int, default=3)
    parser.add_argument("--manifest", type=Path, default=DATASET_DIR / "generated_manifest.json")
    args = parser.parse_args()
    manifest = generate(max(0, args.images), max(0, args.videos), args.manifest.resolve())
    print(
        json.dumps(
            {
                "manifest": str(args.manifest.resolve()),
                "image_cases": len(manifest["image_cases"]),
                "video_cases": len(manifest["video_cases"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
