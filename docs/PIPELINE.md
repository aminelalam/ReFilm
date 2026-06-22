# Pipeline de ReFilm

## Objetivo

ReFilm separa un camino base rapido de un camino avanzado con GPU. El camino base permite
usar la aplicacion y revisar metricas sin depender de cuota cloud. El camino avanzado se
reserva para clips seleccionados donde la superresolucion real justifica el coste.

## Flujo base

1. FastAPI recibe el video y crea un `job_id`.
2. `LocalBucket` guarda el original en `data/originals/{job_id}`.
3. `AuditStore` registra estado, eventos, pasos y metricas en SQLite.
4. Si `REFILM_CLOUD_ENABLED=true`, se sincroniza el original.
5. Si ademas `REFILM_CLOUD_RUN_JOBS_ENABLED=true`, la web despacha un Cloud Run Job y deja
   el render pesado fuera del proceso HTTP. El worker ejecuta Video Intelligence en paralelo
   con la restauracion y registra shots en BigQuery.
6. En ejecucion cloud sin jobs, Video Intelligence se ejecuta antes del pipeline inline.
7. En ejecucion local, `VideoPipeline` valida el archivo y usa PySceneDetect para dividirlo en escenas. Si la
   libreria no esta instalada o no hay cortes, usa una unica escena de forma segura.
8. La restauracion clasica aplica en una sola codificacion:
   - reduccion moderada de ruido;
   - escalado Lanczos `2x` si la fuente tiene menos de 960 px de ancho;
   - enfoque moderado;
   - ajuste clasico de color solo cuando `color_mode=enhance`;
   - conservacion del audio.
9. Cuando `color_mode=ai_natural`, el pipeline anade una etapa auditada de colorizacion
   neuronal por frames con OpenCV DNN, crominancia limitada por luminancia, bloqueo de color
   por regiones visuales y estabilizacion temporal global para reducir parpadeos sin arrastre
   por pixel.
10. El perfil `ai_realesrgan` ejecuta el binario NCNN Vulkan real en local o el worker GPU
   dedicado en cloud.
11. Se genera `restored.mp4` y una comparacion lateral.
12. OpenCV calcula metricas sobre frames normalizados a la misma anchura.
13. La web muestra el resultado y permite reproducir la comparacion.

Combinar los filtros base en una sola pasada evita perdida generacional y reduce la latencia
frente al flujo anterior, que recodificaba el video una vez por adaptador.

## Almacenamiento

```text
data/
  originals/{job_id}/
  scenes/{job_id}/
  processed/{job_id}/
  final/{job_id}/
  audit/{job_id}/
  dataset/{pair_id}/
  art_audit/{audit_id}/
```

En modo cloud, la misma estructura se replica en Cloud Storage. BigQuery recibe `jobs`,
`job_files`, `processing_steps`, `metrics`, `video_shots`, `vertex_model_runs` y
`restoration_metrics`.

## Metricas

La web muestra indicadores aproximados para comparar original y restaurado:

- resolucion;
- nitidez normalizada por tamano;
- contraste;
- brillo;
- ruido de alta frecuencia;
- saturacion;
- cambio temporal aproximado.

Estas metricas no sustituyen revision visual. Para un benchmark con referencia, usa
`POST /api/dataset/images/degrade`: genera una imagen degradada reproducible y calcula SSIM
y PSNR contra la imagen limpia. `POST /api/dataset/images/evaluate` compara cualquier salida
restaurada con una referencia limpia. `POST /api/dataset/videos/evaluate` calcula PSNR y
SSIM muestreados sobre pares de video con referencia. Si recibe `include_vmaf=true`, tambien
ejecuta VMAF cuando FFmpeg expone `libvmaf`. `datasets/manifest.json` y
`manual_tests/run_benchmark.py` permiten ejecutar pares versionados con hashes SHA-256.

## GPU y modelos reales

`app/colorization.py` ejecuta colorizacion neuronal real con el modelo Caffe
`colorization_release_v2` desde OpenCV DNN. Descarga `colorization_deploy_v2.prototxt`,
`colorization_release_v2.caffemodel` y `pts_in_hull.npy` en `data/models/colorization`
cuando `REFILM_COLORIZATION_AUTO_DOWNLOAD=true`.

El perfil `premium` usa una cadena local mas lenta que `quality`: denoise 3D mas fuerte,
deband, escalado `spline`, enfoque moderado y CRF mas bajo. Debe usarse cuando el tiempo de
render sea menos importante que la limpieza del resultado.

`vertex_realesrgan_worker/worker.py` ejecuta Real-ESRGAN x4plus real en un Cloud Run Job GPU
o Vertex AI Custom Job. Cuando la cuenta no dispone de cuota GPU cloud, el perfil web
`ai_realesrgan` usa `app/ai_upscale.py` y el binario NCNN Vulkan local.

Los nombres ProPainter y DDColor del pipeline base identifican puntos de sustitucion para
futuros workers. La colorizacion actual ya no usa esos nombres como placeholder: el modo
`ai_natural` ejecuta inferencia real y registra el motor usado.

## APIs adicionales

Prioridad recomendada:

1. Vertex AI Gemini para enriquecer el informe ya expuesto en `/api/jobs/{job_id}/report`.
2. Vision API para describir frames clave seleccionados por shot.
3. Speech-to-Text solo para videos con audio relevante.
4. Translation API solo cuando exista una transcripcion.

No conviene invocar estas APIs en cada subida por defecto: aumentan coste y latencia. Deben
ser opciones explicitas por trabajo.

## Despliegue

`infra/main.tf` declara las APIs necesarias, bucket con retencion de temporales, Artifact
Registry, cuentas de servicio, IAM, servicio web, jobs GPU y tablas BigQuery.
Para Cloud Run configura:

```text
REFILM_CLOUD_ENABLED=true
GOOGLE_CLOUD_PROJECT=<project>
GCS_BUCKET_NAME=<bucket>
BQ_DATASET_ID=refilm_audit
BQ_LOCATION=<location>
```

Con `REFILM_CLOUD_RUN_JOBS_ENABLED=true`, la web despacha Cloud Run Jobs antes de devolver
la respuesta. `BackgroundTasks` queda como fallback de desarrollo.
