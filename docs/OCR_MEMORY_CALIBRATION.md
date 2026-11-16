# Preliminary measurement of the OCR pixel limit

**Date:** 2026-11-15

**Status:** exploratory measurement; the production limit still requires
validation with resources and documents representative of the deployment.

## Objective and environment

Assess whether the current value `MAX_PAGE_PIXELS=20000000` is reasonable for
OCR processing of up to two concurrent documents. The value was not changed.

- Host: macOS ARM64 with Docker Desktop; memory reported by Docker: 8,218,316,800 bytes.
- Each measurement ran in a fresh, isolated Linux ARM64 container with
  `--memory=2g --memory-swap=2g --cpus=2 --pids-limit=64`.
- DPI: 200; two OCR workers when concurrency is specified as 2.
- `/sys/fs/cgroup/memory.peak` was read; it corresponds to the container peak,
  including child processes and memory accounted for by the cgroup. It is not
  just Python RSS memory.
- The measurement container did not include the Uvicorn server, Qdrant, or
  loaded NER/embedding models. The 2 GiB limit was only for the experiment; that
  limit was not configured in `docker-compose.yml`.

## Observed results

| Input | Estimated pixels per page | Concurrent OCR jobs | Container peak | Duration |
|---|---:|---:|---:|---:|
| Vector-based synthetic page, 800 × 800 points | 4,946,176 | 1 | 204.2 MiB | 1.25 s |
| Vector-based synthetic page, 1600 × 1600 points, sparse text | 19,766,916 | 1 | 455.1 MiB | 1.97 s |
| Same page, sparse text | 19,766,916 | 2 | 854.2 MiB | 2.64 s |
| Vector-based synthetic page, 3 columns × 40 lines | 19,766,916 | 2 | 869.5 MiB | 9.42 s |
| Rasterized copy of the previous page, similar to a scan | 19,766,916 | 2 | 985.3 MiB | 9.76 s |
| `meeting_minutes.pdf`, 2 pages | Approximately 3.75 million per page | 2 | 364.3 MiB | 3.95 s |

In the rasterized case, 840 words were recognized per job. The embedded image
measured `4445 × 4445` pixels; the service's conservative estimate includes one
additional pixel per axis. The 985.3 MiB peak equals 48.1% of the experimental
2 GiB limit, **not** the service's guaranteed maximum consumption.

## Provisional decision

Keep the default value of **20 million pixels** for now, because these runs
completed two jobs near the limit without exceeding the experimental 2 GiB
container. Do not consider it calibrated for production: actual memory
constraints, tests with more diverse and dense scans, low-confidence orientation
that triggers the second pass, multiple documents, and the full application with
its models and dependencies are needed.

If the environment has less memory or the number of workers increases, review
`MAX_PAGE_PIXELS`, `OCR_MAX_CONCURRENT_JOBS`, and the container memory limit
together. Current concurrency limits are per application process, not global.

## Checking the limit above the threshold

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

**Observed output:**

```text
Expected rejection: PDFPageTooLargeError Page 0 exceeds the rendered-pixel limit
```

## Reproduction: vector-based synthetic page

The following command reproduces the dense configuration with two jobs. Change
`BENCH_SIDE_PT`, `BENCH_JOBS`, `BENCH_LINES`, and `BENCH_COLS` for the other
cases; use a fresh container for each measurement so that `memory.peak` does not
retain the peak from a previous case.

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

**Relevant observed output for the dense case:**

```json
{"side_points":1600,"dpi":200,"jobs":2,"lines":40,"columns":3,"estimated_pixels":19766916,"pdf_bytes":26035,"word_counts":[840,840],"elapsed_seconds":9.42,"cgroup_peak_bytes":911765504}
```

## Reproduction: sample PDF

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

**Observed output:**

```json
{"document":"meeting_minutes.pdf","jobs":2,"words_by_page":[[149,134],[149,134]],"elapsed_seconds":3.95,"cgroup_peak_bytes":382009344}
```

## Reproduction: rasterized page near the limit

An empty temporary directory, represented here by `BENCH_DIR`, was used. The
synthetic file contains no real data and must not overwrite an existing file.
It is created in **another container** to avoid contaminating the peak of the
container running OCR.

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

**Observed output:** `pixel_dimensions: 4445 4445`; `pdf_bytes: 625481`.
Then, in a fresh container:

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

**Observed output:**

```json
{"document":"synthetic_scanned_20m.pdf","jobs":2,"words_by_page":[[840],[840]],"elapsed_seconds":9.76,"cgroup_peak_bytes":1033195520}
```
