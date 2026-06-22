# Despliegue cloud de ReFilm

## Objetivo

La web FastAPI puede seguir procesando localmente para desarrollo. En cloud, activa Cloud
Run Jobs para que los renders pesados no vivan dentro del proceso HTTP.

Cloud Run Jobs con GPU admite una NVIDIA L4 de 24 GB por instancia. Google exige al menos
4 CPU y 16 GiB; esta configuracion usa 8 CPU y 32 GiB.

Referencia oficial:
https://docs.cloud.google.com/run/docs/configuring/jobs/gpu

## 1. Variables

```powershell
$PROJECT_ID = "your-project"
$REGION = "europe-west1"
$REPO = "$REGION-docker.pkg.dev/$PROJECT_ID/refilm-workers"
```

`europe-west1` esta entre las regiones GPU soportadas por Cloud Run Jobs. El dataset BigQuery
puede usar otra ubicacion compatible con el proyecto.

## 2. Preparar infraestructura base

La primera aplicacion Terraform crea APIs, bucket, Artifact Registry, cuenta de servicio,
Pub/Sub y tablas. Los jobs se omiten mientras las URLs de imagen esten vacias.

```powershell
terraform -chdir=infra init
terraform -chdir=infra apply `
  -var "project_id=$PROJECT_ID" `
  -var "bucket_name=your-globally-unique-bucket"
```

## 3. Construir imagenes

```powershell
gcloud builds submit `
  --project $PROJECT_ID `
  --config cloudbuild.web.yaml `
  --substitutions "_IMAGE=$REPO/web:latest" .

gcloud builds submit `
  --project $PROJECT_ID `
  --config cloud_render_worker/cloudbuild.yaml `
  --substitutions "_IMAGE=$REPO/render-gpu:latest" .

gcloud builds submit `
  --project $PROJECT_ID `
  --config vertex_realesrgan_worker/cloudbuild.yaml `
  --substitutions "_IMAGE=$REPO/realesrgan-gpu:latest" .
```

El worker clasico intenta `h264_nvenc` y usa `libx264` como fallback si el FFmpeg de la
imagen no incluye NVENC. Verifica logs del primer render antes de asumir aceleracion de
codificacion.

Los workers ejecutan Video Intelligence en paralelo con el procesamiento y registran shots
en BigQuery. Esto conserva el analisis sin bloquear el proceso HTTP.

## 4. Crear jobs GPU

```powershell
terraform -chdir=infra apply `
  -var "project_id=$PROJECT_ID" `
  -var "bucket_name=your-globally-unique-bucket" `
  -var "web_image=$REPO/web:latest" `
  -var "render_worker_image=$REPO/render-gpu:latest" `
  -var "realesrgan_worker_image=$REPO/realesrgan-gpu:latest" `
  -var "enable_cloud_run_jobs=true"
```

## 5. Configurar la web

```text
REFILM_CLOUD_ENABLED=true
REFILM_CLOUD_RUN_JOBS_ENABLED=true
REFILM_CLOUD_RUN_JOB_LOCATION=europe-west1
REFILM_CLOUD_RUN_RENDER_JOB=refilm-render-gpu
REFILM_CLOUD_RUN_AI_JOB=refilm-realesrgan-gpu
GOOGLE_CLOUD_PROJECT=<project>
GCS_BUCKET_NAME=<bucket>
BQ_DATASET_ID=refilm_audit
BQ_LOCATION=<location>
```

La identidad de la web necesita permiso `run.jobs.runWithOverrides` sobre ambos jobs. La
identidad `refilm-worker` necesita acceso de escritura a GCS y BigQuery; Terraform ya anade
esos roles a nivel proyecto para simplificar el primer despliegue. En produccion, reduce
alcance a bucket y dataset concretos.

## 6. Perfiles

| Perfil | Worker cloud | Uso |
| --- | --- | --- |
| `fast` | `refilm-render-gpu` | Preview clasica rapida |
| `quality` | `refilm-render-gpu` | Master clasico conservador |
| `ai_realesrgan` | `refilm-realesrgan-gpu` | Superresolucion neuronal |

## 7. Limitaciones pendientes

- Medir coste y tiempo reales por minuto de video.
- Anadir dead-letter queue y reintentos operacionales si se incorpora Pub/Sub.
- Paralelizar por escenas en cloud para videos largos.
