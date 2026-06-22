from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def ffmpeg_available() -> bool:
    return has_binary("ffmpeg")


def ffprobe_available() -> bool:
    return has_binary("ffprobe")


def ffmpeg_filter(
    input_path: Path,
    output_path: Path,
    filters: str,
    *,
    preserve_audio: bool = False,
    preset: str = "veryfast",
    crf: int = 21,
) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filters,
        "-map",
        "0:v:0",
    ]
    if preserve_audio:
        command += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    else:
        command += ["-an"]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    run_command(command)


def ffmpeg_copy(input_path: Path, output_path: Path) -> None:
    run_command(["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(output_path)])


def ffmpeg_extract_segment(input_path: Path, output_path: Path, start: float, end: float) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(end - start),
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def ffmpeg_concat(inputs: list[Path], output_path: Path) -> None:
    list_file = output_path.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in inputs),
        encoding="utf-8",
    )
    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(output_path),
            ]
        )
    finally:
        list_file.unlink(missing_ok=True)


def ffprobe_metadata(input_path: Path) -> dict:
    if not ffprobe_available():
        return {"available": False, "message": "ffprobe not installed"}
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(input_path),
        ]
    )
    return json.loads(result.stdout)


def make_side_by_side(original: Path, restored: Path, output_path: Path) -> None:
    original_preview = output_path.with_name("original_preview.mp4")
    restored_preview = output_path.with_name("restored_preview.mp4")
    try:
        ffmpeg_filter(original, original_preview, "scale=640:-2,setsar=1")
        ffmpeg_filter(restored, restored_preview, "scale=640:-2,setsar=1")
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(original_preview),
                "-i",
                str(restored_preview),
                "-filter_complex",
                "hstack=inputs=2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-an",
                str(output_path),
            ]
        )
    finally:
        original_preview.unlink(missing_ok=True)
        restored_preview.unlink(missing_ok=True)
