from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = PROJECT_ROOT / "datasets"
INPUT_MANIFEST = DATASET_DIR / "historical_manifest.json"
OUTPUT_MANIFEST = DATASET_DIR / "historical_quality_manifest.json"
sys.path.insert(0, str(PROJECT_ROOT))

from app.media import ffmpeg_filter, ffprobe_metadata
from app.profiles import PROFILES


def source_width(path: Path) -> int:
    for stream in ffprobe_metadata(path).get("streams", []):
        if stream.get("width"):
            return int(stream["width"])
    raise ValueError(f"Could not determine source width: {path}")


def restore_quality(source: Path, output: Path) -> dict:
    settings = PROFILES["quality"]
    scale = 2 if source_width(source) < 960 else 1
    filters = [
        str(settings["denoise"]),
        f"scale=iw*{scale}:ih*{scale}:flags=lanczos",
        str(settings["sharpen"]),
        "format=yuv420p",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_filter(
        source,
        output,
        ",".join(filters),
        preserve_audio=True,
        preset=str(settings["preset"]),
        crf=int(settings["crf"]),
    )
    return {"profile": "quality", "scale": scale, "ffmpeg_filter": ",".join(filters)}


def main() -> None:
    manifest = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
    cases = []
    for case in manifest["video_cases"]:
        damaged = DATASET_DIR / case["candidate"]
        output = DATASET_DIR / "historical" / "videos" / "restored" / f"quality_{Path(case['candidate']).name}"
        details = restore_quality(damaged, output)
        cases.append(
            {
                "id": case["id"].replace("damaged-baseline", "quality-restored"),
                "reference": case["reference"],
                "candidate": output.relative_to(DATASET_DIR).as_posix(),
                "profile": "quality",
                "source_page": case["source_page"],
                "license_url": case["license_url"],
                "restoration": details,
            }
        )
    output_manifest = {
        "version": 1,
        "description": "Quality-profile restoration results for historical ReFilm video clips.",
        "image_cases": [],
        "video_cases": cases,
    }
    OUTPUT_MANIFEST.write_text(json.dumps(output_manifest, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(OUTPUT_MANIFEST), "video_cases": len(cases)}, indent=2))


if __name__ == "__main__":
    main()
