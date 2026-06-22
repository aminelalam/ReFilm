from app.cloud_storage import upload_text_to_gcs, upload_json_to_gcs

uri_1 = upload_text_to_gcs(
    "Hola desde ReFilm usando app/cloud_storage.py",
    "pruebas/hola_desde_cloud_storage_py.txt",
)

print("Texto subido a:", uri_1)

uri_2 = upload_json_to_gcs(
    {
        "project": "ReFilm",
        "status": "cloud_storage_ok",
        "message": "Primera integración de Google Cloud Storage desde Python"
    },
    "pruebas/audit_test.json",
)

print("JSON subido a:", uri_2)