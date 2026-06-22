import json
from pathlib import Path

from manual_tests.run_benchmark import run


def test_benchmark_runner_accepts_empty_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"version": 1, "image_cases": [], "video_cases": []}),
        encoding="utf-8",
    )

    report = run(manifest)

    assert report["manifest_version"] == 1
    assert report["case_count"] == 0
    assert report["results"] == []
