# Benchmark historico de ReFilm

Fecha de ejecucion: 2026-05-31

## Dataset local

Se descargo una muestra reutilizable y trazable:

- `15` imagenes historicas `Public domain` desde Wikimedia Commons.
- `5` videos Prelinger desde Internet Archive con licencia de dominio publico indicada en
  su ficha.
- `5` clips de `8 s` extraidos de esos videos.
- Degradaciones sinteticas reproducibles aplicadas sobre imagenes y clips historicos.

Los binarios ocupan aproximadamente `325 MB` antes de generar restauraciones y quedan
ignorados por Git. La procedencia completa, licencia, URL y hash SHA-256 se guarda en
`datasets/historical/provenance.json`.

Fuentes de video:

| Identificador | Titulo |
| --- | --- |
| `AboutBan1935` | About Bananas |
| `Doctorin1946` | Doctor in Industry (Part I) |
| `HealthYo1953` | Health: Your Posture |
| `FromtheG1954` | From the Ground Up |
| `Sleepfor1950` | Sleep for Health |

## Linea base degradada

El informe `datasets/reports/historical_baseline.json` compara cada degradacion con su
referencia historica conservada.

| Tipo | Casos | SSIM medio | PSNR medio |
| --- | ---: | ---: | ---: |
| Imagen | 15 | `0.5544` | `24.4789 dB` |
| Video | 5 | `0.5518` | `19.9430 dB` |

VMAF por clip degradado: `43.3195`, `96.2400`, `98.3710`, `96.3972`, `96.8294`.

## Restauracion quality

El informe `datasets/reports/historical_quality.json` compara la salida del perfil
`quality` con la referencia historica.

| Metrica video | Degradado | Restaurado | Delta |
| --- | ---: | ---: | ---: |
| SSIM medio | `0.5518` | `0.5616` | `+0.0098` |
| PSNR medio | `19.9430 dB` | `20.0332 dB` | `+0.0902 dB` |
| VMAF medio | `86.2314` | `87.5892` | `+1.3577` |

La mejora es real pero moderada. El siguiente experimento util es comparar filtros y
Real-ESRGAN sobre estos mismos clips, manteniendo revision visual: ninguna metrica aislada
garantiza fidelidad historica.

## Reproducir

```powershell
.\.venv\Scripts\python.exe manual_tests\download_historical_benchmark.py --images 15 --videos 5
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py --manifest datasets\historical_manifest.json --output datasets\reports\historical_baseline.json
.\.venv\Scripts\python.exe manual_tests\restore_historical_benchmark.py
.\.venv\Scripts\python.exe manual_tests\run_benchmark.py --manifest datasets\historical_quality_manifest.json --output datasets\reports\historical_quality.json
```

## Referencias

- Wikimedia Commons, categoria consultada:
  https://commons.wikimedia.org/wiki/Category:Historical_images_of_the_United_States
- Wikimedia Commons API:
  https://www.mediawiki.org/wiki/API:Imageinfo
- Internet Archive, Prelinger Archives:
  https://archive.org/details/prelinger
- Internet Archive Metadata API:
  https://archive.org/developers/md-read.html
