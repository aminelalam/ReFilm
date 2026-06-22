from __future__ import annotations

import json
import mimetypes
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

try:
    from google.cloud import storage
except ModuleNotFoundError:  # Opcional en instal·lacions només locals.
    storage = None  # type: ignore[assignment]


DEFAULT_PROJECT_ID = "encoded-ensign-496217-u4"
DEFAULT_BUCKET_NAME = "refilm-sm-alfonso-2026"

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT_ID)
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", DEFAULT_BUCKET_NAME)


_client: storage.Client | None = None


def get_client() -> storage.Client:
    global _client

    if storage is None:
        raise RuntimeError("Google Cloud Storage support is not installed.")

    if _client is None:
        _client = storage.Client(project=PROJECT_ID)

    return _client


def get_bucket() -> storage.Bucket:
    if not BUCKET_NAME:
        raise RuntimeError("No se ha configurado el nombre del bucket.")

    client = get_client()
    return client.bucket(BUCKET_NAME)


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """
    Converteix:
        gs://bucket/ruta/archivo.mp4
    en:
        ("bucket", "ruta/archivo.mp4")
    """
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"No es una URI válida de Cloud Storage: {gcs_uri}")

    without_scheme = gcs_uri.replace("gs://", "", 1)
    bucket_name, _, blob_name = without_scheme.partition("/")

    if not bucket_name or not blob_name:
        raise ValueError(f"URI de Cloud Storage incompleta: {gcs_uri}")

    return bucket_name, unquote(blob_name)


def upload_file_to_gcs(local_path: str | Path, destination_path: str) -> str:
    local_path = Path(local_path)

    if not local_path.exists():
        raise FileNotFoundError(f"No existe el archivo local: {local_path}")

    bucket = get_bucket()
    blob = bucket.blob(destination_path)

    content_type, _ = mimetypes.guess_type(str(local_path))

    blob.upload_from_filename(
        str(local_path),
        content_type=content_type,
    )

    return f"gs://{BUCKET_NAME}/{destination_path}"


def upload_bytes_to_gcs(
    data: bytes,
    destination_path: str,
    content_type: str = "application/octet-stream",
) -> str:
    bucket = get_bucket()
    blob = bucket.blob(destination_path)

    blob.upload_from_string(
        data,
        content_type=content_type,
    )

    return f"gs://{BUCKET_NAME}/{destination_path}"


def upload_text_to_gcs(
    text: str,
    destination_path: str,
    content_type: str = "text/plain",
) -> str:
    bucket = get_bucket()
    blob = bucket.blob(destination_path)

    blob.upload_from_string(
        text,
        content_type=content_type,
    )

    return f"gs://{BUCKET_NAME}/{destination_path}"


def upload_json_to_gcs(data: dict[str, Any], destination_path: str) -> str:
    json_text = json.dumps(data, ensure_ascii=False, indent=2)

    return upload_text_to_gcs(
        json_text,
        destination_path,
        content_type="application/json",
    )


def download_file_from_gcs(source_path: str, local_path: str | Path) -> None:
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    bucket = get_bucket()
    blob = bucket.blob(source_path)

    blob.download_to_filename(str(local_path))


def download_gcs_uri_as_bytes(gcs_uri: str) -> tuple[bytes, str, str]:
    """
    Descarrega un objecte gs:// i retorna:
    - contingut en bytes
    - nom de fitxer
    - content type
    """
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)

    client = get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        raise FileNotFoundError(f"No existe el objeto en Cloud Storage: {gcs_uri}")

    blob.reload()

    data = blob.download_as_bytes()
    filename = Path(blob_name).name
    content_type = blob.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    return data, filename, content_type


def create_signed_download_url(gcs_uri: str, *, inline: bool = False) -> str:
    """Crea una URL temporal per evitar passar vídeos grans per FastAPI."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    blob = get_client().bucket(bucket_name).blob(blob_name)
    disposition = "inline" if inline else "attachment"
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=15),
        method="GET",
        response_disposition=f'{disposition}; filename="{Path(blob_name).name}"',
    )


def gcs_file_exists(source_path: str) -> bool:
    bucket = get_bucket()
    blob = bucket.blob(source_path)

    return blob.exists()


def gcs_uri_exists(gcs_uri: str) -> bool:
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)

    client = get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    return blob.exists()


def build_gcs_uri(destination_path: str) -> str:
    return f"gs://{BUCKET_NAME}/{destination_path}"
