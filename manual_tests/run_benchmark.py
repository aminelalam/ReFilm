from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "datasets" / "manifest.json"
sys.path.insert(0, str(PROJECT_ROOT))

from app.dataset import compare_reference_images, compare_reference_videos


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_case(dataset_dir: Path, case: dict[str, Any], kind: str) -> dict[str, Any]:
    reference = dataset_dir / case["reference"]
    candidate = dataset_dir / case["candidate"]
    if not reference.is_file() or not candidate.is_file():
        raise FileNotFoundError(f"Missing benchmark files for {case['id']}")
    metrics = (
        compare_reference_images(reference, candidate)
        if kind == "image"
        else compare_reference_videos(reference, candidate, include_vmaf=True)
    )
    return {
        **case,
        "kind": kind,
        "reference_sha256": sha256(reference),
        "candidate_sha256": sha256(candidate),
        "metrics": metrics,
    }


def run(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_dir = manifest_path.parent
    results = [
        evaluate_case(dataset_dir, case, kind)
        for key, kind in [("image_cases", "image"), ("video_cases", "video")]
        for case in manifest.get(key, [])
    ]
    return {
        "manifest": str(manifest_path),
        "manifest_version": manifest["version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.manifest.resolve())
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
