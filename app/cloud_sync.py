from __future__ import annotations

from pathlib import Path

from app.bigquery_audit import (
    safe_record_uploaded_file_from_path,
    safe_sync_job_metadata_to_bigquery,
)
from app.cloud_storage import upload_file_to_gcs
from app.config import CLOUD_ENABLED


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"


def _upload_folder_if_exists(job_id: str, local_folder: Path, gcs_prefix: str) -> list[str]:
    """
    Puja una carpeta local a Cloud Storage i registra els fitxers.
    """
    uploaded_uris: list[str] = []

    if not local_folder.exists():
        return uploaded_uris

    for file_path in local_folder.rglob("*"):
        if not file_path.is_file():
            continue

        relative_path = file_path.relative_to(local_folder).as_posix()
        destination_path = f"{gcs_prefix}/{relative_path}"

        uri = upload_file_to_gcs(file_path, destination_path)
        uploaded_uris.append(uri)

        # Registre de fitxers per a la traçabilitat.
        safe_record_uploaded_file_from_path(job_id, file_path, uri)

    return uploaded_uris


def sync_job_to_gcs(job_id: str) -> list[str]:
    """
    Sincronitza els fitxers d'un treball amb GCS i BigQuery.
    """
    uploaded_uris: list[str] = []

    folders_to_sync = [
        ("originals", DATA_DIR / "originals" / job_id),
        ("scenes", DATA_DIR / "scenes" / job_id),
        ("processed", DATA_DIR / "processed" / job_id),
        ("final", DATA_DIR / "final" / job_id),
        ("audit", DATA_DIR / "audit" / job_id),
        ("art_audit", DATA_DIR / "art_audit" / job_id),
        ("dataset", DATA_DIR / "dataset" / job_id),
    ]

    for cloud_folder, local_folder in folders_to_sync:
        uploaded_uris.extend(
            _upload_folder_if_exists(
                job_id=job_id,
                local_folder=local_folder,
                gcs_prefix=f"{cloud_folder}/{job_id}",
            )
        )

    # Compatibilitat amb auditories guardades fora de la carpeta del job.
    audit_file = DATA_DIR / "audit" / f"{job_id}.json"
    if audit_file.exists():
        uri = upload_file_to_gcs(
            audit_file,
            f"audit/{job_id}/audit.json",
        )
        uploaded_uris.append(uri)
        safe_record_uploaded_file_from_path(job_id, audit_file, uri)

    # Registre final de metadades i passos.
    safe_sync_job_metadata_to_bigquery(job_id)

    return uploaded_uris


def safe_sync_job_to_gcs(job_id: str) -> list[str]:
    """
    Sincronització tolerant a errors cloud.
    """
    if not CLOUD_ENABLED:
        return []

    try:
        uploaded = sync_job_to_gcs(job_id)

        if uploaded:
            print(f"[GCS] Job {job_id}: {len(uploaded)} archivos subidos.")
            for uri in uploaded:
                print(f"[GCS]   {uri}")
        else:
            print(f"[GCS] Job {job_id}: no había archivos nuevos para subir.")

        return uploaded

    except Exception as exc:
        print(f"[GCS] Error sincronizando job {job_id}: {exc}")
        return []
