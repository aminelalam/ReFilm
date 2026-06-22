# ReFilm

ReFilm is a FastAPI application for restoring old photos and videos, reviewing the result in a local web interface, and keeping an auditable record of every restoration job.

It is designed to work locally by default. Google Cloud integrations are optional and only run when the corresponding environment variables are enabled.

## What ReFilm Does

- Restores videos and images through an asynchronous job workflow.
- Preserves the original file and stores local audit data in SQLite.
- Applies classic restoration steps with FFmpeg and OpenCV: denoise, upscale, sharpening, color enhancement, and comparison output.
- Supports color modes: no color changes, classic enhancement, and natural AI colorization.
- Keeps original audio when processing videos.
- Generates before/after comparison files for visual review.
- Reports quality indicators such as resolution, sharpness, contrast, brightness, noise, saturation, and temporal change.
- Exposes local job reports through `GET /api/jobs/{job_id}/report`.
- Includes benchmark utilities for synthetic and historical restoration tests.
- Includes optional Google Cloud Storage, BigQuery, Video Intelligence, Cloud Run Jobs, and Real-ESRGAN worker support.

## Tech Stack

- Python 3.11+
- FastAPI
- FFmpeg and FFprobe
- OpenCV
- NumPy, Pillow, scikit-image
- PySceneDetect
- Optional Google Cloud services

## Requirements

Install these before running the app:

- Python 3.11 or newer
- FFmpeg and FFprobe available on your `PATH`
- Windows, macOS, or Linux

## Quick Start

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

### macOS or Linux

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open the app at:

```text
http://127.0.0.1:8000
```

Local mode does not require Google Cloud credentials.

## Configuration

Copy `.env.example` to `.env` for local overrides.

| Variable | Purpose |
| --- | --- |
| `REFILM_CLOUD_ENABLED` | Enables Cloud Storage, BigQuery, and cloud fallback paths. Defaults to `false`. |
| `REFILM_CLOUD_RUN_JOBS_ENABLED` | Dispatches rendering to Cloud Run Jobs when cloud mode is enabled. |
| `REFILM_CLOUD_RUN_JOB_LOCATION` | Region for Cloud Run Jobs. |
| `REFILM_CLOUD_RUN_RENDER_JOB` | Cloud Run Job name for standard rendering. |
| `REFILM_CLOUD_RUN_AI_JOB` | Cloud Run Job name for AI super-resolution. |
| `REFILM_QUALITY_SAMPLE_FRAMES` | Number of frames sampled for quality metrics. |
| `REFILM_COLORIZATION_AUTO_DOWNLOAD` | Allows colorization model files to download into `data/models/colorization`. |
| `REFILM_COLORIZATION_MODEL_DIR` | Local path for colorization model files. |
| `REFILM_MAX_UPLOAD_BYTES` | Maximum accepted upload size. |
| `REFILM_MAX_VIDEO_DURATION_SECONDS` | Maximum accepted video duration. |
| `REFILM_MAX_VIDEO_DIMENSION` | Maximum accepted video width or height. |
| `GOOGLE_CLOUD_PROJECT` | Google Cloud project ID. |
| `GCS_BUCKET_NAME` | Cloud Storage bucket for originals and outputs. |
| `BQ_DATASET_ID` | BigQuery dataset for audit tables. |
| `BQ_LOCATION` | BigQuery dataset location. |

## Main API Endpoints

- `POST /api/videos`
- `POST /api/images`
- `GET /api/color-modes`
- `GET /api/profiles`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/report`
- `GET /api/jobs/{job_id}/preview/comparison`
- `POST /api/dataset/degrade`
- `POST /api/dataset/images/degrade`
- `POST /api/dataset/images/evaluate`
- `POST /api/dataset/videos/evaluate`
- `POST /api/art-audits`

## Project Layout

```text
app/                         FastAPI app, pipelines, storage, audit, and UI
app/static/                  Browser interface
tests/                       Automated tests
manual_tests/                Benchmark and cloud validation scripts
docs/                        Pipeline and deployment notes
infra/                       Terraform infrastructure
cloud_render_worker/         Cloud render worker
vertex_realesrgan_worker/    Real-ESRGAN GPU worker
datasets/                    Benchmark manifest and dataset notes
```

## Tests

```bash
python -m pytest -q --basetemp .test_tmp
node --check app/static/app.js
```

## Large Files and Models

Generated media, uploaded originals, restored outputs, SQLite databases, logs, downloaded model weights, and bundled binaries are intentionally not tracked in Git.

The local runtime writes working data under `data/`. AI colorization models are downloaded to `data/models/colorization` when `REFILM_COLORIZATION_AUTO_DOWNLOAD=true`. Real-ESRGAN binaries or model weights should be installed separately, distributed through releases, or managed through an external artifact system.

## Documentation

- `docs/PIPELINE.md`: restoration pipeline and storage flow.
- `docs/CLOUD_DEPLOYMENT.md`: cloud deployment notes.
- `docs/CLOUD_FUNCTIONS_I_WORKERS.txt`: cloud worker summary.
- `docs/HISTORICAL_BENCHMARK.md`: benchmark notes and provenance.

## License

No license has been specified yet. Until a license is added, all rights are reserved by the project owner.
