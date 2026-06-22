# Dataset benchmark

`manifest.json` registra pares revisables sin incluir material audiovisual pesado en Git.

Cada caso debe tener:

```json
{
  "id": "clip-001-quality",
  "reference": "clips_reference/clip-001-clean.mp4",
  "candidate": "results/clip-001-quality.mp4",
  "profile": "quality"
}
```

Usa `kind=image` de forma implícita dentro de `image_cases` y `kind=video` dentro de
`video_cases`. Las rutas son relativas a esta carpeta. Para ejecutar todas las métricas:

```powershell
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py
```

El informe incluye hash SHA-256 de cada archivo, SSIM, PSNR y VMAF para vídeo cuando el
FFmpeg instalado expone `libvmaf`.

Para crear una base sintética determinista con `20` imágenes y `3` clips:

```powershell
.\.venv\Scripts\python.exe manual_tests\generate_synthetic_benchmark.py
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py --manifest datasets\generated_manifest.json
```

Los archivos generados quedan fuera de Git. Añade material histórico real revisado en una
carpeta gestionada según su licencia y registra sus rutas en un manifiesto separado.

Para descargar la muestra histórica pública, conservar procedencia y crear degradaciones:

```powershell
.\.venv\Scripts\python.exe manual_tests\download_historical_benchmark.py
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py --manifest datasets\historical_manifest.json --output datasets\reports\historical_baseline.json
```

El descargador filtra imágenes `Public domain` de Wikimedia Commons y utiliza vídeos
Prelinger cuya ficha de Internet Archive expone licencia de dominio público.

Para medir el perfil clásico `quality` sobre los clips históricos degradados:

```powershell
.\.venv\Scripts\python.exe manual_tests\restore_historical_benchmark.py
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py --manifest datasets\historical_quality_manifest.json --output datasets\reports\historical_quality.json
```
