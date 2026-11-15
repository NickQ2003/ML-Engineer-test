# Medición preliminar del límite de píxeles OCR

**Fecha:** 2026-11-15

**Estado:** medición exploratoria; el límite de producción sigue pendiente de
validación con recursos y documentos representativos del despliegue.

## Objetivo y entorno

Evaluar si el valor actual `MAX_PAGE_PIXELS=20000000` es razonable para el
procesamiento OCR de hasta dos documentos simultáneos. No se cambió el valor.

- Host: macOS ARM64 con Docker Desktop; memoria informada por Docker: 8.218.316.800 bytes.
- Cada medición se ejecutó en un contenedor Linux ARM64 nuevo y aislado, con
  `--memory=2g --memory-swap=2g --cpus=2 --pids-limit=64`.
- DPI: 200; dos workers OCR cuando se indica concurrencia de 2.
- Se leyó `/sys/fs/cgroup/memory.peak`, que corresponde al pico del contenedor,
  incluidos los procesos hijos y la memoria contabilizada por el cgroup. No es
  solo la memoria RSS de Python.
- El contenedor de medición no incluyó el servidor Uvicorn, Qdrant ni modelos de
  NER/embeddings cargados. El límite de 2 GiB fue solo para el experimento; no
  se configuró ese límite en `docker-compose.yml`.

## Resultados observados

| Entrada | Píxeles estimados por página | OCR simultáneos | Pico del contenedor | Duración |
|---|---:|---:|---:|---:|
| Página sintética vectorial, 800 × 800 puntos | 4.946.176 | 1 | 204,2 MiB | 1,25 s |
| Página sintética vectorial, 1600 × 1600 puntos, texto escaso | 19.766.916 | 1 | 455,1 MiB | 1,97 s |
| Misma página, texto escaso | 19.766.916 | 2 | 854,2 MiB | 2,64 s |
| Página sintética vectorial, 3 columnas × 40 líneas | 19.766.916 | 2 | 869,5 MiB | 9,42 s |
| Copia rasterizada de la página anterior, similar a un escaneo | 19.766.916 | 2 | 985,3 MiB | 9,76 s |
| `meeting_minutes.pdf`, 2 páginas | Aproximadamente 3,75 millones por página | 2 | 364,3 MiB | 3,95 s |

En el caso rasterizado se reconocieron 840 palabras por trabajo. La imagen
insertada medía `4445 × 4445` píxeles; la estimación conservadora del servicio
incluye un píxel adicional por eje. El pico de 985,3 MiB equivale al 48,1 % del
límite experimental de 2 GiB, **no** al consumo máximo garantizado del servicio.

## Decisión provisional

Mantener por ahora el valor predeterminado de **20 millones de píxeles**, porque
estas ejecuciones completaron dos trabajos cerca del límite sin superar el
contenedor experimental de 2 GiB. No considerarlo calibrado para producción: se
necesitan las restricciones reales de memoria, pruebas con escaneos más diversos
y densos, orientación de baja confianza que active la segunda pasada, varios
documentos y la aplicación completa con sus modelos y dependencias.

Si el entorno tiene menos memoria o aumenta el número de workers, revisar
conjuntamente `MAX_PAGE_PIXELS`, `OCR_MAX_CONCURRENT_JOBS` y el límite de memoria
del contenedor. Los límites de concurrencia actuales son por proceso de la
aplicación, no globales.

## Comprobación del límite por encima del umbral

```bash
docker run --rm -i --memory=2g --memory-swap=2g --cpus=2 --pids-limit=64 --entrypoint python ml-engineer-test-app:latest - <<'PY'
import pymupdf
from app.services.ocr_service import OCRService, PDFPageTooLargeError
with pymupdf.open() as document:
    document.new_page(width=1610, height=1610)
    pdf = document.tobytes()
try:
    OCRService().extract(pdf)
except PDFPageTooLargeError as exc:
    print('Expected rejection:', type(exc).__name__, str(exc))
else:
    raise SystemExit('A page above the limit was unexpectedly accepted')
PY
```

**Salida observada:**

```text
Expected rejection: PDFPageTooLargeError Page 0 exceeds the rendered-pixel limit
```

## Reproducción: página sintética vectorial

El siguiente comando reproduce la configuración densa con dos trabajos. Cambiar
`BENCH_SIDE_PT`, `BENCH_JOBS`, `BENCH_LINES` y `BENCH_COLS` para los otros casos;
usar un contenedor nuevo por medición para que `memory.peak` no conserve el pico
de un caso anterior.

```bash
docker run --rm -i --memory=2g --memory-swap=2g --cpus=2 --pids-limit=64 \
  -e BENCH_SIDE_PT=1600 -e BENCH_JOBS=2 -e BENCH_LINES=40 -e BENCH_COLS=3 \
  --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
import json
import math
import os
from pathlib import Path
import time
import pymupdf
from app.core.config import Settings
from app.services.ocr_service import OCRService

side = int(os.environ['BENCH_SIDE_PT'])
jobs = int(os.environ['BENCH_JOBS'])
lines = int(os.environ.get('BENCH_LINES', '15'))
columns = int(os.environ.get('BENCH_COLS', '1'))
with pymupdf.open() as document:
    page = document.new_page(width=side, height=side)
    for column in range(columns):
        for index in range(lines):
            page.insert_text((40 + column * 500, 70 + index * 24), f'Sample document text line {index} Alice Smith', fontsize=14)
    pdf = document.tobytes()

async def run():
    service = OCRService(Settings(ocr_max_concurrent_jobs=jobs))
    try:
        results = await asyncio.gather(*(service.extract_async(pdf) for _ in range(jobs)))
        return [len(result.pages[0].words) for result in results]
    finally:
        await service.aclose()

start = time.monotonic()
counts = asyncio.run(run())
peak_path = Path('/sys/fs/cgroup/memory.peak')
print(json.dumps({
    'side_points': side, 'dpi': 200, 'jobs': jobs,
    'lines': lines, 'columns': columns,
    'estimated_pixels': (math.ceil(side * 200 / 72) + 1) ** 2,
    'pdf_bytes': len(pdf), 'word_counts': counts,
    'elapsed_seconds': round(time.monotonic() - start, 2),
    'cgroup_peak_bytes': int(peak_path.read_text().strip()) if peak_path.exists() else None,
}))
PY
```

**Salida relevante observada para el caso denso:**

```json
{"side_points":1600,"dpi":200,"jobs":2,"lines":40,"columns":3,"estimated_pixels":19766916,"pdf_bytes":26035,"word_counts":[840,840],"elapsed_seconds":9.42,"cgroup_peak_bytes":911765504}
```

## Reproducción: PDF de ejemplo

```bash
docker run --rm -i --memory=2g --memory-swap=2g --cpus=2 --pids-limit=64 \
  -v "$PWD/sample_pdfs:/samples:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
import json
from pathlib import Path
import time
from app.core.config import Settings
from app.services.ocr_service import OCRService

async def run():
    service = OCRService(Settings(ocr_max_concurrent_jobs=2))
    try:
        documents = await asyncio.gather(*(service.extract_file_async('/samples/meeting_minutes.pdf') for _ in range(2)))
        return [[len(page.words) for page in document.pages] for document in documents]
    finally:
        await service.aclose()

start = time.monotonic()
counts = asyncio.run(run())
print(json.dumps({'document': 'meeting_minutes.pdf', 'jobs': 2, 'words_by_page': counts, 'elapsed_seconds': round(time.monotonic()-start, 2), 'cgroup_peak_bytes': int(Path('/sys/fs/cgroup/memory.peak').read_text())}))
PY
```

**Salida observada:**

```json
{"document":"meeting_minutes.pdf","jobs":2,"words_by_page":[[149,134],[149,134]],"elapsed_seconds":3.95,"cgroup_peak_bytes":382009344}
```

## Reproducción: página rasterizada cercana al límite

Se utilizó un directorio temporal vacío, representado aquí por `BENCH_DIR`. El
archivo sintético no contiene datos reales y no debe sobrescribir uno existente.
Su creación ocurre en **otro contenedor**, para no contaminar el pico del
contenedor que ejecuta OCR.

```bash
BENCH_DIR="$(mktemp -d)"
```

```bash
docker run --rm -i --memory=2g --memory-swap=2g --cpus=2 --pids-limit=64 \
  --user "$(id -u):$(id -g)" -v "$BENCH_DIR:/bench" \
  --entrypoint python ml-engineer-test-app:latest - <<'PY'
from pathlib import Path
import pymupdf
out = Path('/bench/synthetic_scanned_20m.pdf')
if out.exists():
    raise SystemExit('Refusing to overwrite existing benchmark PDF')
with pymupdf.open() as original:
    page = original.new_page(width=1600, height=1600)
    for column in range(3):
        for index in range(40):
            page.insert_text((40 + column * 500, 70 + index * 24), f'Sample document text line {index} Alice Smith', fontsize=14)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(200/72, 200/72), alpha=False)
    with pymupdf.open() as scanned:
        page = scanned.new_page(width=1600, height=1600)
        page.insert_image(page.rect, pixmap=pix)
        scanned.save(out, deflate=True)
print('pixel_dimensions:', pix.width, pix.height)
print('pdf_bytes:', out.stat().st_size)
PY
```

**Salida observada:** `pixel_dimensions: 4445 4445`; `pdf_bytes: 625481`.
Después, en un contenedor nuevo:

```bash
docker run --rm -i --memory=2g --memory-swap=2g --cpus=2 --pids-limit=64 \
  -v "$BENCH_DIR:/bench:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
import json
from pathlib import Path
import time
from app.core.config import Settings
from app.services.ocr_service import OCRService

async def run():
    service = OCRService(Settings(ocr_max_concurrent_jobs=2))
    try:
        documents = await asyncio.gather(*(service.extract_file_async('/bench/synthetic_scanned_20m.pdf') for _ in range(2)))
        return [[len(page.words) for page in document.pages] for document in documents]
    finally:
        await service.aclose()

start = time.monotonic()
counts = asyncio.run(run())
print(json.dumps({'document': 'synthetic_scanned_20m.pdf', 'jobs': 2, 'words_by_page': counts, 'elapsed_seconds': round(time.monotonic()-start, 2), 'cgroup_peak_bytes': int(Path('/sys/fs/cgroup/memory.peak').read_text())}))
PY
```

**Salida observada:**

```json
{"document":"synthetic_scanned_20m.pdf","jobs":2,"words_by_page":[[840],[840]],"elapsed_seconds":9.76,"cgroup_peak_bytes":1033195520}
```
