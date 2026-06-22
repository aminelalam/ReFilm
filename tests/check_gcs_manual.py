from google.cloud import storage

BUCKET_NAME = "refilm-sm-alfonso-2026"

client = storage.Client()
bucket = client.bucket(BUCKET_NAME)

blob = bucket.blob("pruebas/hola_desde_python.txt")
blob.upload_from_string("Hola desde Python y ReFilm usando Cloud Storage")

print("Archivo subido correctamente a Cloud Storage")