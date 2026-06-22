from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = PROJECT_ROOT / "datasets"
HISTORICAL_DIR = DATASET_DIR / "historical"
MANIFEST_PATH = DATASET_DIR / "historical_manifest.json"
USER_AGENT = "ReFilmBenchmark/1.0 (historical restoration research)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_CATEGORY = "Category:Historical images of the United States"
PRELINGER_ITEMS = [
    ("AboutBan1935", 20),
    ("Doctorin1946", 30),
    ("HealthYo1953", 40),
    ("FromtheG1954", 50),
    ("Sleepfor1950", 60),
]
sys.path.insert(0, str(PROJECT_ROOT))

from app.dataset import degrade_clip, degrade_image


def request_json(url: str) -> dict[str, Any]:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=120) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=300) as response:
                destination.write_bytes(response.read())
            return
        except HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            delay = int(exc.headers.get("Retry-After") or 2 ** (attempt + 1))
            print(f"[RATE LIMIT] Waiting {delay}s before retrying {url}", flush=True)
            time.sleep(delay)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.relative_to(DATASET_DIR).as_posix()


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return name[:100] or "asset"


def ext_value(info: dict[str, Any], key: str) -> str | None:
    return info.get("extmetadata", {}).get(key, {}).get("value")


def commons_candidates(limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    continuation: dict[str, str] = {}
    while len(results) < limit:
        params = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": COMMONS_CATEGORY,
            "gcmtype": "file",
            "gcmlimit": "100",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": "1280",
            "format": "json",
            "formatversion": "2",
            **continuation,
        }
        payload = request_json(f"{COMMONS_API}?{urlencode(params)}")
        for page in payload.get("query", {}).get("pages", []):
            info = page.get("imageinfo", [{}])[0]
            title = page["title"]
            source_url = info.get("thumburl") or info.get("url")
            license_name = ext_value(info, "LicenseShortName")
            copyrighted = ext_value(info, "Copyrighted")
            suffix = Path(source_url or "").suffix.lower()
            if (
                source_url
                and license_name == "Public domain"
                and copyrighted == "False"
                and suffix in {".jpg", ".jpeg"}
            ):
                results.append(
                    {
                        "title": title,
                        "download_url": source_url,
                        "source_page": f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}",
                        "license": license_name,
                        "license_url": ext_value(info, "LicenseUrl"),
                        "artist": ext_value(info, "Artist"),
                    }
                )
                if len(results) == limit:
                    break
        continuation = payload.get("continue", {})
        if not continuation:
            break
    if len(results) < limit:
        raise RuntimeError(f"Only found {len(results)} suitable Commons images, expected {limit}.")
    return results


def download_commons_images(limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = []
    cases = []
    images_dir = HISTORICAL_DIR / "images"
    for index, asset in enumerate(commons_candidates(limit)):
        clean = images_dir / f"clean_{index:02d}_{safe_name(asset['title'][5:])}.jpg"
        damaged = images_dir / f"damaged_{index:02d}.jpg"
        download(asset["download_url"], clean)
        time.sleep(0.75)
        details = degrade_image(clean, damaged, seed=1000 + index)
        source = {
            **asset,
            "local_path": relative(clean),
            "sha256": sha256(clean),
            "size_bytes": clean.stat().st_size,
            "degradation": details,
        }
        sources.append(source)
        cases.append(
            {
                "id": f"historical-image-{index:02d}-damaged-baseline",
                "reference": relative(clean),
                "candidate": relative(damaged),
                "profile": "synthetic_damage_baseline",
                "source_page": asset["source_page"],
                "license": asset["license"],
            }
        )
    return sources, cases


def choose_mp4(files: list[dict[str, Any]]) -> dict[str, Any]:
    low_res = [item for item in files if item.get("name", "").lower().endswith("_512kb.mp4")]
    if low_res:
        return min(low_res, key=lambda item: int(item.get("size") or 0))
    mp4s = [item for item in files if item.get("name", "").lower().endswith(".mp4")]
    if not mp4s:
        raise RuntimeError("Internet Archive item has no MP4 download.")
    return min(mp4s, key=lambda item: int(item.get("size") or 0))


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def download_prelinger_clips(limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = []
    cases = []
    source_dir = HISTORICAL_DIR / "videos" / "sources"
    clips_dir = HISTORICAL_DIR / "videos" / "clips"
    source_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    for index, (identifier, offset) in enumerate(PRELINGER_ITEMS[:limit]):
        metadata = request_json(f"https://archive.org/metadata/{identifier}")
        item = metadata.get("metadata", {})
        license_url = item.get("licenseurl")
        if "publicdomain" not in str(license_url):
            raise RuntimeError(f"{identifier} does not expose a public-domain license URL.")
        file_info = choose_mp4(metadata.get("files", []))
        filename = file_info["name"]
        source = source_dir / safe_name(filename)
        clean = clips_dir / f"clean_{index:02d}_{identifier}.mp4"
        damaged = clips_dir / f"damaged_{index:02d}_{identifier}.mp4"
        download(f"https://archive.org/download/{identifier}/{quote(filename)}", source)
        run_ffmpeg(["-ss", str(offset), "-i", str(source), "-t", "8", "-c", "copy", str(clean)])
        details = degrade_clip(clean, damaged, seed=2000 + index)
        source_details = {
            "identifier": identifier,
            "title": item.get("title"),
            "date": item.get("date"),
            "source_page": f"https://archive.org/details/{identifier}",
            "download_url": f"https://archive.org/download/{identifier}/{quote(filename)}",
            "license_url": license_url,
            "local_source_path": relative(source),
            "local_clip_path": relative(clean),
            "source_sha256": sha256(source),
            "clip_sha256": sha256(clean),
            "source_size_bytes": source.stat().st_size,
            "clip_offset_seconds": offset,
            "clip_duration_seconds": 8,
            "degradation": details,
        }
        sources.append(source_details)
        cases.append(
            {
                "id": f"historical-video-{index:02d}-{identifier}-damaged-baseline",
                "reference": relative(clean),
                "candidate": relative(damaged),
                "profile": "synthetic_damage_baseline",
                "source_page": source_details["source_page"],
                "license_url": license_url,
            }
        )
    return sources, cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=int, default=20)
    parser.add_argument("--videos", type=int, default=5)
    args = parser.parse_args()
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    image_sources, image_cases = download_commons_images(max(1, args.images))
    video_sources, video_cases = download_prelinger_clips(max(1, min(args.videos, len(PRELINGER_ITEMS))))
    manifest = {
        "version": 1,
        "description": "Historical public-domain ReFilm benchmark with reproducible synthetic degradation.",
        "image_cases": image_cases,
        "video_cases": video_cases,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    provenance = {
        "commons_category": COMMONS_CATEGORY,
        "image_sources": image_sources,
        "video_sources": video_sources,
    }
    (HISTORICAL_DIR / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "manifest": str(MANIFEST_PATH),
                "images": len(image_cases),
                "video_clips": len(video_cases),
                "historical_bytes": sum(path.stat().st_size for path in HISTORICAL_DIR.rglob("*") if path.is_file()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
