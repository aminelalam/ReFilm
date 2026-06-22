from __future__ import annotations

import shutil
from pathlib import Path

from app.config import BASE_DIR
from app.media import run_command
from app.profiles import COLOR_ENHANCEMENT_FILTER


REALESRGAN_DIR = BASE_DIR / "tools" / "realesrgan-ncnn-vulkan"
REALESRGAN_EXE = REALESRGAN_DIR / "realesrgan-ncnn-vulkan.exe"


def realesrgan_available() -> bool:
    return REALESRGAN_EXE.exists()


def upscale_video_with_realesrgan(
    input_path: Path,
    output_path: Path,
    *,
    colorize: bool,
    scale: int = 2,
    tile: int = 400,
) -> None:
    """Executa Real-ESRGAN NCNN Vulkan i conserva l'àudio original."""
    if not realesrgan_available():
        raise RuntimeError(f"Real-ESRGAN executable was not found: {REALESRGAN_EXE}")

    work_dir = output_path.with_name(f"{output_path.stem}_realesrgan_work")
    frames_dir = work_dir / "frames"
    upscaled_dir = work_dir / "upscaled_frames"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir.mkdir(parents=True)
    upscaled_dir.mkdir(parents=True)

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
        run_command(
            [
                str(REALESRGAN_EXE),
                "-i",
                str(frames_dir.resolve()),
                "-o",
                str(upscaled_dir.resolve()),
                "-n",
                "realesrgan-x4plus",
                "-s",
                str(scale),
                "-t",
                str(tile),
                "-f",
                "png",
            ],
            cwd=REALESRGAN_DIR,
        )
        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            _video_fps(input_path),
            "-i",
            str(upscaled_dir / "frame_%08d.png"),
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
        ]
        if colorize:
            command += ["-vf", COLOR_ENHANCEMENT_FILTER]
        command += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
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
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
