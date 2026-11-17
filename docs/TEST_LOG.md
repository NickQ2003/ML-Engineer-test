# Test and Execution Log

This version-controlled document records the checks performed during
development, their results, and troubleshooting. The outputs shown are relevant
excerpts, not necessarily the complete output. Timing, size, and identifier
values may vary between runs.

When adding an entry, record the date, environment, command, observed output,
result, and limitations and, if an error occurred, its diagnosis and resolution.
Do not include keys, credentials, personal data, or sensitive PDF content.

## 2026-11-14 — Milestone 1: Initial Infrastructure

**Environment:** macOS ARM64, Docker Desktop with Linux ARM64 containers, Python
3.12 in the container. Local Python tests ran in a temporary environment with
Python 3.12.16. The Docker engine reported version 28.3.3 and Compose version
2.39.2. The project is on the development branch.

### Configuration and Container Startup

```bash
docker compose config
docker compose up --build -d
docker compose ps
```

**Observed result:** Compose processed the configuration and built the image;
`app` and `qdrant` appeared with status `Up`. The
`ml-engineer-test_qdrant_data` volume was created. Checking the configuration
file alone does not guarantee that the services are ready to accept requests.

### API Health

```bash
curl --fail --silent --show-error http://localhost:8000/health
```

**Observed response:**

```json
{"status":"ok"}
```

**Result:** Passed. This route is an API liveness check; it does not verify the
application's connection to Qdrant or an LLM.

### Qdrant Availability from the Internal Network

```bash
docker compose exec -T app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://qdrant:6333/readyz', timeout=5).read().decode())"
```

**Observed response:**

```text
all shards are ready
```

**Result:** Passed for network connectivity and Qdrant availability. It does not
yet verify the application adapter or data persistence.

### Initial Unit Tests on macOS

They ran in a temporary environment with the minimum test dependencies, not
with all project dependencies:

```bash
python -m pytest tests/unit
```

**Relevant observed output:**

```text
2 passed, 1 warning
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

**Result:** The tests passed with a Starlette compatibility warning. No specific
fix was applied for that warning. It did not appear in the subsequent Linux run
with the image dependencies; those environments did not have exactly the same
set of dependencies.

### Issue: Oversized Image Due to CUDA

CUDA/NVIDIA packages were detected in the `uv sync` output during the first
build. The build completed successfully, but the size was excessive for the
project's CPU scope:

```bash
docker image inspect ml-engineer-test-app:latest --format '{{.Size}} bytes'
```

**Observed result before the change:**

```text
10691090779 bytes
```

**Diagnosis:** PyTorch resolved with CUDA dependencies on Linux. In addition,
the image retained the `uv` cache and an extra layer caused by recursively
changing the virtual environment owner. No GPU was needed.

**Fix:** `torch` was declared as a direct dependency, and the official PyTorch
CPU index for Linux was configured in `pyproject.toml`. `uv.lock` was
regenerated. In `Dockerfile`, the persistent `uv` cache was disabled and
`chown -R` on the virtual environment was removed while retaining the
unprivileged user. The decision and its trade-offs are documented in
[DECISIONS.md](../DECISIONS.md).

During the initial adjustment, configuring the index only for the transitive
dependency did not change resolution: NVIDIA packages continued to appear.
After declaring `torch` as a direct dependency and running `uv lock` again, the
result included `torch 2.15.0+cpu` for Linux and removed those dependencies.

### Validation After the CUDA Fix

The lockfile was regenerated and verified with `uv` 0.13.8. A temporary `uv`
installation, not a global one, was used on the development machine:

```bash
uv lock --python 3.12
uv lock --check --python 3.12
```

**Relevant check output:**

```text
Using CPython 3.12.16
Resolved 106 packages
```

The image was rebuilt and measured using the same metric as before:

```bash
docker compose up --build -d
docker image inspect ml-engineer-test-app:latest --format '{{.Size}} bytes'
```

**Observed result after the change:**

```text
629766551 bytes
```

The observed reduction corresponds to the combined dependency and layer
changes, not solely to removing CUDA. The previous image and cache were not
automatically deleted and may still occupy local disk space.

PyTorch and the `sentence-transformers` import were verified inside the image
without downloading or running an embeddings model:

```bash
docker compose exec -T app python -c \
  "import torch; import sentence_transformers; print('torch:', torch.__version__); print('CUDA build:', torch.version.cuda); print('tensor:', (torch.tensor([2.0]) * 3).item()); print('sentence-transformers:', sentence_transformers.__version__); assert torch.version.cuda is None"
```

**Observed output:**

```text
torch: 2.15.0+cpu
CUDA build: None
tensor: 6.0
sentence-transformers: 6.3.0
```

The runtime user was also checked with `docker compose exec -T app id`:

```text
uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)
```

The API returned `{"status":"ok"}` again after the image was rebuilt.

### Unit Tests Inside Linux

A temporary container based on the built image was used, with the test
directory mounted read-only. `pytest` was temporarily installed in that
container; the command requires network access:

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  --entrypoint /bin/sh \
  ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit'
```

**Relevant observed output:**

```text
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
3 passed in 2.82s
```

**Result:** Passed on Linux ARM64. Configuration, health, and the CPU variant of
PyTorch were verified. Timings may vary.

### GitHub Publishing Issue

```bash
git push -u origin feature/document-intelligence
```

**Observed response:** GitHub returned HTTP `403` because the authenticated
account lacked write permission on the original repository.

**Diagnosis:** The authenticated account does not have write permission on the
original repository. The chosen alternative was to create a personal fork and
use it to publish the branch while retaining the original repository as
`upstream`.

The attempt was repeated without terminal interaction:

```bash
GIT_TERMINAL_PROMPT=0 git push -u origin feature/document-intelligence
```

**Observed response:** The server rejected access again with HTTP 403 for the
`NickQ2003` account. The branch was not published, and no changes were made to
the remote. Uncommitted local changes were also not included in the attempt.

**Resolution:** The fork was created with:

```bash
gh repo fork anvasquezre-cuesta/ML-Engineer-test --clone=false
```

**Observed response:** `https://github.com/NickQ2003/ML-Engineer-test`.
That fork was configured as `origin` and the original repository as `upstream`.
The branch was then published to the fork:

```bash
GIT_TERMINAL_PROMPT=0 git push -u origin feature/document-intelligence
```

**Observed result:** The `origin/feature/document-intelligence` remote branch
was created, and the local branch was configured to track it. This resolves
publishing to the fork; it does not grant write permission on the original
repository. The pull request has not yet been opened.

### Pending Checks

- Write data to Qdrant, restart the container, and verify its persistence.
- Test the image on Linux x86_64; the previous run was on Linux ARM64.
- Test the Qdrant adapter, real models, and extraction and RAG endpoints when
  implemented; they are not part of the tests already run.

## 2026-11-14 — Milestone 2: OCR and Coordinates

**Environment:** Docker Linux ARM64, Python 3.12.16, and Tesseract with English
language data included in the image. Application and test files were mounted
from the working tree to verify changes before rebuilding the image.

### Initial Tests and Resource Cleanup

On the first OCR test suite run, the relevant output was:

```text
1 failed, 18 passed
test_wraps_ocr_failures_and_closes_images: DID NOT RAISE ValueError
```

**Diagnosis:** The context manager for an in-memory Pillow image did not close
the resource in the manner required by the test when an OCR error occurred.
**Resolution:** Use `contextlib.closing` to release images explicitly; the
subsequent test also verifies that the PDF document closes when OCR fails.

### Rotation Test and Fix

Real OCR ran on synthetic scanned PDFs with different rotations. Before the
fix, using the same test characters, the output was:

```text
rotation 0 text 'ALICE SMITH'
rotation 90 text 'ALICE SMITH'
rotation 180 text 'HLINS S0N1V'
rotation 270 text 'HLINS S0N1V'
```

**Diagnosis:** Rendering respected the PDF rotation, but Tesseract could read
the text upside down. **Resolution:** Normalize orientation using the PDF
metadata before OCR and transform the boxes back to points in the unrotated crop
area. This is not a general fix for skew or upside-down scans without metadata.

### Real OCR on Sample PDFs

```bash
docker run --rm \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  --entrypoint python ml-engineer-test-app:latest \
  -c 'from pathlib import Path; from app.services.ocr_service import OCRService; svc = OCRService(); [(print(path.name, "pages:", len(doc.pages), "words:", [len(page.words) for page in doc.pages], "dimensions:", [(round(page.width), round(page.height)) for page in doc.pages])) for path in sorted(Path("/samples").glob("*.pdf")) for doc in [svc.extract_file(path)]]'
```

**Observed output before the orientation adjustment:**

```text
company_memo.pdf pages: 1 words: [143] dimensions: [(612, 792)]
meeting_minutes.pdf pages: 2 words: [149, 134] dimensions: [(612, 792), (612, 792)]
research_report.pdf pages: 2 words: [129, 134] dimensions: [(612, 792), (612, 792)]
```

All three documents were processed again in the subsequent test suite. The word
count depends on the engine and its data; by itself, it does not demonstrate NER
or name-box accuracy.

### Final Suite: Unit and OCR Integration Tests

```bash
docker run --rm \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration/test_ocr_real.py'
```

**Observed output:**

```text
collected 33 items
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
tests/unit/test_ocr_service.py ...................
tests/integration/test_ocr_real.py ...........
33 passed in 6.86s
```

All three samples and synthetic scanned PDFs were tested with all four rotations
and with/without cropping. The synthetic tests verify that the center of a
word's OCR box matches its expected location; they do not guarantee perfect
accuracy for every edge or for arbitrary documents. Input limits, invalid and
encrypted PDFs, and cleanup on error were also tested.

The image was then rebuilt and the service verified:

```bash
docker compose up --build -d
curl --fail --silent --show-error http://localhost:8000/health
```

**Observed result:** Both containers started and `/health` returned
`{"status":"ok"}`. The suite was repeated using the code included in the rebuilt
image, without mounting `app/` from the host:

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration/test_ocr_real.py'
```

**Relevant output:** `33 passed in 7.32s`.

**Pending:** Implement HTTP validation and the extraction endpoint, NER, a
rendered-pixel limit, x86_64 testing, and Qdrant persistence. The installed
language is English; other languages require additional data.

## 2026-11-15 — Milestone 2: asynchronous OCR execution

**Objective:** Keep PDF processing from blocking the event loop and limit the
number of concurrent OCR jobs without changing the test's HTTP contract.
**Environment:** rebuilt Linux ARM64 Docker image, Python 3.12.16, English
Tesseract, and pytest 9.2.0 installed in a temporary test container.

**Implementation:** `extract_async` and `extract_file_async` dispatch blocking
work to AnyIO processes, avoiding parallel PyMuPDF execution in threads.
`get_ocr_service()` allows the service and its limiter to be reused.
`OCR_MAX_CONCURRENT_JOBS` is configurable and defaults to 2 per application
process. The HTTP endpoint is not implemented yet.

### Build and configuration check

```bash
uv lock --python 3.12
docker compose up --build -d
```

**Observed result:** `uv` resolved 106 packages; Docker built the image and
started the application. `anyio` was added as a direct dependency and `uv.lock`
was updated.

Lockfile consistency was also checked with `uv lock --check --python 3.12`
(106 packages resolved), and `docker compose config --quiet` completed without
errors. After startup, `docker compose ps` showed `app` and `qdrant` running;
`curl --fail --silent --show-error http://localhost:8000/health` returned
`{"status":"ok"}`. The following was checked inside the application:

```bash
docker compose exec -T app python -c "from app.core.config import get_settings; from app.services.ocr_service import get_ocr_service; print('OCR_MAX_CONCURRENT_JOBS:', get_settings().ocr_max_concurrent_jobs); print('shared_service:', get_ocr_service() is get_ocr_service())"
```

**Observed output:**

```text
OCR_MAX_CONCURRENT_JOBS: 2
shared_service: True
```

### Unit and integration tests in the rebuilt image

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Relevant observed output:**

```text
collected 44 items
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
tests/unit/test_ocr_async.py ........
tests/unit/test_ocr_service.py ...................
tests/integration/test_async_http.py .
tests/integration/test_ocr_real.py .............
44 passed in 16.76s
```

Coverage included execution in a separate process, a non-blocked event loop, a
concurrency limit, validation errors, rejecting large files before sending them
to the process, cancellation of a waiting task, completing an already-started
job after AnyIO cancellation, and real OCR through the bytes and file interfaces.
One test uses a simulated OCR path and the real `/health` route to verify that the
latter remains responsive; it does not test the future `/api/extract` endpoint.

**Limitations:** the limit applies per application process, not globally; it does
not limit the number of queued requests or the memory used by uploads already
received. This is not a persistent queue: if the process exits, the job is not
guaranteed to be retained. Real endpoints still need to be tested when
implemented, and behavior on Linux x86_64 remains to be validated.

## 2026-11-15 — OCR and concurrency review

**Status:** review completed; fixes are still pending. The plan and acceptance
criteria are in [OCR_REVIEW_PLAN.md](OCR_REVIEW_PLAN.md). The previous suite
command was repeated in the built image and reported `44 passed in 17.34s`. The
following reproductions used temporary Linux ARM64 containers; sample PDFs were
not changed.

### Oversized page: detection before image allocation

`get_pixmap()` was intercepted to observe the allocation that would have been
attempted, without actually allocating the estimated 2+ GB for one RGB copy:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import pymupdf
from app.services.ocr_service import OCRService, OCRProcessingError
with pymupdf.open() as doc:
    doc.new_page(width=10000, height=10000)
    data=doc.tobytes()
original=pymupdf.Page.get_pixmap
def intercepted(page, *args, **kwargs):
    scale=200/72
    print('Input bytes:',len(data))
    print('Approximate rendered pixels:',round(page.rect.width*scale)*round(page.rect.height*scale))
    print('Approximate RGB bytes for one copy:',3*round(page.rect.width*scale)*round(page.rect.height*scale))
    raise RuntimeError('render intercepted before allocation')
pymupdf.Page.get_pixmap=intercepted
try:
    OCRService().extract(data)
except OCRProcessingError as exc:
    print('Reached rendering instead of rejecting:',str(exc))
finally:
    pymupdf.Page.get_pixmap=original
PY
```

**Observed output:**

```text
Input bytes: 522
Approximate rendered pixels: 771617284
Approximate RGB bytes for one copy: 2314851852
Reached rendering instead of rejecting: OCR failed on page 0
```

**Result:** insufficient resource protection. R-01 was opened; no pixel limit had
been applied yet.

### Metadata rotation that corrects a rotated scan

A synthetic PDF was created with a physically rotated image and metadata
rotation that displays it upright. Real OCR on the displayed view was compared
with the service result:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
from io import BytesIO
from PIL import Image
import pymupdf, pytesseract
from app.services.ocr_service import OCRService
with pymupdf.open() as d:
    p=d.new_page(width=400,height=300)
    p.insert_text((40,100),'ALICE SMITH',fontsize=32)
    p.insert_text((40,150),'JOHN ANDERSON',fontsize=32)
    pix=p.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    sideways=im.rotate(90,expand=True)
    b=BytesIO(); sideways.save(b,format='PNG')
    with pymupdf.open() as doc:
        sp=doc.new_page(width=300,height=400)
        sp.insert_image(sp.rect,stream=b.getvalue())
        sp.set_rotation(90)
        visible=sp.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
        visible_img=Image.frombytes('RGB',(visible.width,visible.height),visible.samples)
        print('Displayed orientation OCR:',repr(pytesseract.image_to_string(visible_img,lang='eng').strip()))
        print('Service OCR:',repr(OCRService().extract(doc.tobytes()).text))
        visible_img.close()
    im.close(); sideways.close()
PY
```

**Observed output:**

```text
Displayed orientation OCR: 'ALICE SMITH\nJOHN ANDERSON'
Service OCR: 'NOSdYsAdNV NHOPr\nHLINWIS S0NIV'
```

**Result:** incorrect. R-03 was opened; rotation normalization still needed a fix.

### Direct cancellation during an OCR subprocess

The Tesseract executable inside the container was temporarily replaced with a
controlled long-running process while retaining the real version check. The
simulated process was stopped before the test ended:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" -v "$PWD/sample_pdfs:/samples:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio, os, signal
from pathlib import Path
from app.services.ocr_service import OCRService
fake=Path('/tmp/review-fakebin'); fake.mkdir()
tesseract=fake/'tesseract'
tesseract.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then exec /usr/bin/tesseract --version; fi\necho $$ > /tmp/review-tesseract.pid\nexec sleep 30\n')
tesseract.chmod(0o755)
os.environ['PATH']=str(fake)+':'+os.environ['PATH']
async def main():
    marker=Path('/tmp/review-tesseract.pid')
    task=asyncio.create_task(OCRService().extract_file_async('/samples/company_memo.pdf'))
    async with asyncio.timeout(15):
        while not marker.exists(): await asyncio.sleep(.02)
    pid=int(marker.read_text())
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    print('Tesseract survived:',Path(f'/proc/{pid}/status').exists())
    print('Tesseract-related temp files left:',len(list(Path('/tmp').glob('tess_*'))))
    try: os.kill(pid,signal.SIGKILL)
    except ProcessLookupError: pass
asyncio.run(main())
PY
```

**Observed output:**

```text
Tesseract survived: True
Tesseract-related temp files left: 2
```

**Result:** incorrect. R-02 was opened; the existing AnyIO cancellation test did
not cover this direct cancellation. The temporary container was removed at the
end.

### Capacity and concurrent initialization

With the sole limiter permit occupied, four calls were launched with instrumented
1 MB inputs:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
from app.core.config import Settings
from app.services.ocr_service import OCRService
calls=[]
class TrackedBytes(bytes):
    def __reduce_ex__(self, protocol):
        calls.append(len(self))
        return bytes, (bytes(self),)
async def main():
    service=OCRService(Settings(ocr_max_concurrent_jobs=1))
    async with service._process_limiter:
        tasks=[asyncio.create_task(service.extract_async(TrackedBytes(b'x'*1_000_000))) for _ in range(4)]
        await asyncio.sleep(.1)
        print('jobs waiting with no capacity: 4')
        print('payloads already serialized:',len(calls))
        print('total payload bytes already serialized:',sum(calls))
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
asyncio.run(main())
PY
```

**Observed output:**

```text
jobs waiting with no capacity: 4
payloads already serialized: 4
total payload bytes already serialized: 4000000
```

The provider was also called from four threads using a barrier in a simulated
constructor implementation:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import app.services.ocr_service as mod
barrier=Barrier(4)
class FakeService:
    def __init__(self):
        barrier.wait(timeout=10)
mod.OCRService=FakeService
mod.get_ocr_service.cache_clear()
with ThreadPoolExecutor(max_workers=4) as pool:
    instances=list(pool.map(lambda _:mod.get_ocr_service(),range(4)))
print('concurrent first calls:',len(instances))
print('distinct service instances:',len({id(s) for s in instances}))
print('sequential subsequent calls share instance:',mod.get_ocr_service() is mod.get_ocr_service())
PY
```

**Observed output:**

```text
concurrent first calls: 4
distinct service instances: 4
sequential subsequent calls share instance: True
```

**Result:** R-04 and R-05 remain open; details and proposed decisions are in the
review plan.

## 2026-11-15 — OCR review fixes

**Status:** fixes implemented at the service layer. Admission before multipart
parsing, global limits, and the real endpoint are still pending. Decisions and
limitations are documented in `docs/OCR_REVIEW_PLAN.md`.

- R-01: conservative pixel estimation before rendering and rejection of
  oversized pages with a configurable threshold; deployment calibration remains.
- R-02: supervised POSIX subprocesses, a private temporary directory, an overall
  limit, and process-group cleanup on timeout or controlled shutdown.
- R-03: visible orientation first and a second pass on low confidence; real tests
  with physical rotation, metadata, cropping, and boxes.
- R-04: local job and queue limits, queue timeout, and stream reading with
  admission acquired first; the real endpoint's multipart body still needs bounds.
- R-05: service created in the FastAPI lifecycle, tested with concurrent requests
  to a test route that uses the shared dependency.
- R-06: log level validated and applied; application logs emitted as JSON.

### Logging fix incident

After initially applying the log level without installing an application handler,
this command completed without printing the debug message:

```bash
docker compose exec -T app python -c "import logging; from app.core.logging import configure_logging; configure_logging('DEBUG'); logging.getLogger('app.review').debug('DEBUG_SHOULD_BE_VISIBLE')"
```

**Diagnosis:** the level was applied, but the application had no handler to emit
its records. **Fix:** install a JSON handler for `app` and keep Uvicorn levels
configurable. After rebuilding the image, the following was run:

```bash
docker compose exec -T app python -c "import logging; from app.core.logging import configure_logging; configure_logging('DEBUG'); logging.getLogger('app.check').debug('logging-ok')"
```

**Relevant observed output:**

```json
{"timestamp":"2026-11-15T06:27:18.597653+00:00","level":"DEBUG","logger":"app.check","message":"logging-ok"}
```

### Final suite in the rebuilt image

```bash
docker compose up --build -d
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Relevant observed output:**

```text
collected 67 items
tests/unit/test_lifecycle_and_logging.py ...
tests/unit/test_ocr_async.py ..............
tests/unit/test_ocr_service.py .....................
tests/integration/test_async_http.py .
tests/integration/test_ocr_real.py .......................
tests/integration/test_worker_cleanup.py ..
67 passed in 19.18s
```

Cleanup tests use a simulated subprocess that ignores `SIGTERM` and verify that
it stops and leaves no temporary files after cancellation with controlled
shutdown and after timeout. They do not demonstrate cleanup after an abrupt
machine crash or container `SIGKILL`. The HTTP test uses a simulated route, not
`/api/extract`. `docker compose ps` showed both containers running, and `curl
--fail --silent --show-error http://localhost:8000/health` returned
`{"status":"ok"}`.

## 2026-11-15 — Preliminary visual review of OCR boxes

`sample_pdfs/meeting_minutes.pdf` was processed with `OCRService`, and each word
box was drawn on a copy of the PDF. A temporary directory was used as the initial
destination; the file was then copied without overwriting to
`docs_dev/meeting_minutes_ocr_visual.pdf`, which remains outside Git.

```bash
docker run --rm -i --user "$(id -u):$(id -g)" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -v "<directorio-temporal>:/output" \
  --entrypoint python ml-engineer-test-app:latest - <<'PY'
from pathlib import Path
import pymupdf
from app.services.ocr_service import OCRService

source = Path('/samples/meeting_minutes.pdf')
destination = Path('/output/meeting_minutes_ocr_visual.pdf')
if destination.exists():
    raise SystemExit(f'File already exists; it was not overwritten: {destination}')
result = OCRService().extract_file(source)
with pymupdf.open(source) as document:
    for page_result in result.pages:
        page = document[page_result.page_number]
        for word in page_result.words:
            box = word.box
            page.draw_rect(pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height), color=(1, 0, 0), width=0.7, overlay=True)
    document.save(destination, deflate=True)
print(f'Generated PDF: {destination}')
print(f'Pages: {len(result.pages)}; words per page: {[len(page.words) for page in result.pages]}')
PY
```

**Observed output:** `Pages: 2; words per page: [149, 134]`. The user visually
reviewed and approved the boxes for this PDF. Their observations and the review's
limitations are in [MANUAL_TEST_LOG.md](MANUAL_TEST_LOG.md).

## 2026-11-15 — Exploratory OCR memory measurement

The cgroup memory peak was measured in temporary containers limited to 2 GiB,
with one or two concurrent OCR jobs. Two jobs on a synthetic rasterized page of
approximately 19.77 million pixels produced a peak of 985.3 MiB; a page above the
limit was correctly rejected.

**Decision:** provisionally retain `MAX_PAGE_PIXELS=20000000`. This measurement
does not include the complete application or test the worst case, and is not
production calibration. Full environment, commands, results, and limitations are in
[OCR_MEMORY_CALIBRATION.md](OCR_MEMORY_CALIBRATION.md).

## 2026-11-15 — Milestone 3: input contracts and limits

**Environment:** Docker on macOS ARM64; Linux aarch64 container, Python 3.12.16,
FastAPI 0.163.0, Starlette 1.10.0, Uvicorn 0.54.0, and pytest 9.2.0. Pytest was
installed only in a temporary test container, not in the production image.

**Changes checked:** page-local span contract; `names` validator with UTF-8 byte
and item-count limits; validated configuration; ASGI request-body byte middleware
for `POST /api/extract`. Public schemas were not changed, and the endpoint, NER,
and fuzzy matching were not implemented at this point.

### Initial validation and build

```bash
docker compose config --quiet
docker compose build app
git diff --check
git diff -- app/models/schemas.py
```

`docker compose config --quiet` and both `git diff` commands completed without
output; the image built successfully. The last command confirms that the public
schema file did not change. The focused suite was also run during development with
the code mounted read-only; the two runs collected 68 and 70 tests, respectively,
and all passed.

```bash
docker run --rm \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/tests:/tests:ro" \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit/test_config.py /tests/unit/test_extraction_contracts.py /tests/integration/test_extraction_request_limits.py'
```

### Full suite in the rebuilt image

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Abbreviated final observed output:**

```text
platform linux -- Python 3.12.16, pytest-9.2.0
collected 146 items
tests/unit/test_config.py ...................
tests/unit/test_extraction_contracts.py ...
tests/integration/test_extraction_request_limits.py ...
tests/integration/test_ocr_real.py .......................
tests/integration/test_worker_cleanup.py ..
146 passed in 20.12s
```

The suite includes chunked multipart requests without `Content-Length`, with an
exact length, and with a falsely small length at the ASGI layer; early rejection
of excessive or malformed declared lengths; exact `names` limits and overflow;
and a real chunked HTTP request to Uvicorn. The temporary multipart file was
verified to close on overflow, cancellation, and disconnect, with the disk-spool
threshold lowered for those tests. The middleware was also verified not to send a
second response if overflow is detected after a response has started.

**Result:** correct for the tested components and simulated routes. The real HTTP
extraction route did not yet exist; multipart tests use a test endpoint and do
not demonstrate the OCR/NER flow. The middleware counts only bytes read by the
application and does not limit concurrent uploads or read time. The `names`
validator still needed to be connected when implementing the endpoint.

**Open finding:** on the simulated route, FastAPI/Starlette returned `400` when
rejecting a multipart field larger than 1 MiB before running the validator. The
real integration needed to handle parsing and error translation to return the
intended `413` for oversized fields. Keep the cleanup test when upgrading
Starlette because the middleware depends on its parser error handling.

**Running containers:** the new image was built without restarting the existing
Compose service; it continues to use the previous image until recreated. No claim
was made that the already-running service was using the new code.

## 2026-11-15 — Milestone 3: page-local NER and model packaging

**Environment:** Docker on macOS ARM64; Linux aarch64, Python 3.12.16,
spaCy 3.8.18, `en_core_web_sm` 3.8.0, Uvicorn 0.54.0, and pytest 9.2.0.
The test uses the English model included in the image, not a simulated model.

### Dependencies and image

The official model wheel was added to `pyproject.toml` and the lockfile was
regenerated; the already-pinned spaCy version remained 3.8.18. `uv.lock` records
the wheel URL and SHA-256. The Dockerfile checks that the model loads and provides
NER.

```bash
/private/var/folders/3b/9b_qzjg94m99y011jncyj9300000gn/T/opencode/uv-tool/bin/uv lock --python 3.12
/private/var/folders/3b/9b_qzjg94m99y011jncyj9300000gn/T/opencode/uv-tool/bin/uv lock --check --python 3.12
docker compose config --quiet
docker compose build app
```

**Result:** `Resolved 107 packages`; the lockfile added `en-core-web-sm v3.8.0`
and the image built successfully. The model verification layer passed. The `uv`
binary path shown is the one used on this host; another environment can use `uv`
from its PATH. The initial build required network access to GitHub and the
dependencies; later builds may use the Docker cache.

### Offline inference and worker reuse

```bash
docker run --rm --network none -i --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
from importlib.metadata import version
from app.services.ner_async import NERService

async def main():
    service = NERService()
    try:
        pages = ['Barack Obama met Angela Merkel in Berlin.', 'Alice Johnson met Alice Johnson.']
        first = await service.detect_pages(pages)
        process = service._slots[0].process
        second = await service.detect_pages(pages)
        assert first == second and service._slots[0].process is process
        assert [[span.text for span in page] for page in first] == [['Barack Obama', 'Angela Merkel'], ['Alice Johnson', 'Alice Johnson']]
        assert all(page[span.char_start:span.char_end] == span.text for page, result in zip(pages, first) for span in result)
        print('spaCy:', version('spacy'), '| model:', version('en-core-web-sm'))
        print('network: disabled | pages: 2 | detections: 4 | worker reused: yes')
    finally:
        await service.aclose()
    assert process.returncode is not None
    print('worker stopped: yes')

asyncio.run(main())
PY
```

**Observed output:**

```text
spaCy: 3.8.18 | model: 3.8.0
network: disabled | pages: 2 | detections: 4 | worker reused: yes
worker stopped: yes
```

### Full suite

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Relevant output:** `collected 195 items` → **`195 passed in 32.71s`**.
Tests cover page-local offsets, repeated occurrences, label filtering, missing
model, excessive input, worker reuse, saturation, cancellation, timeout, and
shutdown of a simulated process that ignores `SIGTERM`. They also check
`/health` during real inference and during a slow simulated process. Real OCR and
NER were tested together on `meeting_minutes.pdf` without an HTTP endpoint.

### Exploratory observations on sample PDFs

Exact per-page OCR + NER occurrences were compared with lists prepared by the
assistant after reading the sample PDFs. **These references have not been reviewed
or approved by a person**, are not an independent evaluation set, and do not allow
accuracy to be extrapolated to other documents. Repeated occurrences are counted
separately; the criterion was an exact full-name match, not location or the
internal label of other entities.

| PDF | Reference | Exact matches | False positives | False negatives |
|---|---:|---:|---:|---:|
| `company_memo.pdf` | 8 | 8 | 1 | 0 |
| `meeting_minutes.pdf` | 24 | 22 | 1 | 2 |

In the first PDF, the project name `Atlas` was labeled as a person. In the second,
a heading fragment was labeled as a person and two occurrences of `Elizabeth
Park` were missed. OCR combined with a small English model can be wrong; filtering
on the `PERSON` label is not a semantic guarantee.

Command used for the comparison:

```bash
docker run --rm --network none -i -v "$PWD/sample_pdfs:/samples:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
from collections import Counter
from pathlib import Path
from app.services.ocr_service import OCRService
from app.services.ner_service import SpacyPersonRecognizer

reference = {
    'company_memo.pdf': [[
        'Margaret Thompson', 'Robert Chen', 'Sarah Williams', 'James Anderson',
        'Maria Garcia', 'David Nakamura', 'Patricia Okonkwo', 'Margaret Thompson',
    ]],
    'meeting_minutes.pdf': [
        ['Richard Hernandez', 'Elizabeth Park', 'Thomas Muller', 'Aisha Patel', "Kevin O'Brien", 'Jennifer Liu', 'Carlos Mendoza',
         'Richard Hernandez', 'Jennifer Liu', 'Elizabeth Park', 'Thomas Muller', 'Elizabeth Park', 'Aisha Patel', "Kevin O'Brien"],
        ['Thomas Muller', 'Yuki Tanaka', 'Alexander Popov', 'Richard Hernandez', 'Catherine Dubois', 'Aisha Patel', "Kevin O'Brien", 'Richard Hernandez', 'Jennifer Liu', 'Richard Hernandez'],
    ],
}
ner = SpacyPersonRecognizer.load('en_core_web_sm', 100000)
for filename, pages in reference.items():
    document = OCRService().extract_file(Path('/samples') / filename)
    tp = fp = fn = 0
    extras, missing = [], []
    for page, expected in zip(document.pages, pages, strict=True):
        predicted = Counter(span.text for span in ner.detect_page(page.text))
        expected = Counter(expected)
        tp += sum((predicted & expected).values())
        fp += sum((predicted - expected).values())
        fn += sum((expected - predicted).values())
        extras.extend((predicted - expected).elements())
        missing.extend((expected - predicted).elements())
    print(f'{filename}: reference={tp + fn}, detected={tp + fp}, exact_TP={tp}, FP={fp}, FN={fn}')
    print(' extras:', extras)
    print(' missing:', missing)
PY
```

**Scope and pending work:** boxes, fuzzy matching, and the real endpoint had not
been implemented. NER limits are per process, and admission for the complete
OCR–NER flow remains pending. The existing Compose service was not restarted and
still uses its previous image. An exploratory GitHub API request failed because
of incorrect quoting in the script; it was not needed to install the wheel, whose
resolution and loading were verified with `uv` and the tests described above.

## 2026-11-15 — Milestone 3: extraction endpoint and bounding boxes

**Environment:** Docker on macOS ARM64; Linux aarch64, Python 3.12.16,
FastAPI 0.163.0, Starlette 1.10.0, English Tesseract, spaCy 3.8.18,
`en_core_web_sm` 3.8.0, and pytest 9.2.0.

**Scope:** real `POST /api/extract` endpoint, page-local association of spans
with OCR words, bounding boxes, full-name fuzzy matching, multipart validation,
and per-process HTTP admission. `app/models/schemas.py` was unchanged.

### Build and full test suite

```bash
docker compose config --quiet
docker compose build app
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
git diff --check
git diff -- app/models/schemas.py
```

**Result:** image built; `collected 264 items` → **`264 passed in 42.53s`**.
Configuration and `git diff` commands completed without output.

The suite covers repeated and multipage occurrences, nearby but unrelated words,
coordinate limits and validation, full-string matching, accents, and the
threshold. It checks errors, corrupt or protected files, incomplete multipart
forms, fields larger than 1 MiB, page-count overflow, length and comparison-work
limits, rejection before reading when capacity is unavailable, chunked uploads
with missing or false `Content-Length`, timeouts, and temporary-file cleanup. One
test uses the real endpoint with real OCR and NER on the sample PDF; another
checks box centers against known positions on two synthetic scanned pages; a
third checks a rotated and cropped scan. `/health` was tested while the endpoint
was waiting on simulated NER. The dedicated parser fixes the `400` documented in
the previous milestone: the tested oversized fields return `413`.

### Isolated real Compose and HTTP checks

To avoid restarting the user's containers, a separate Compose project and a new
volume were used solely for this check:

```bash
APP_PORT=18087 docker compose -p hito3verify20261115 up --build -d
APP_PORT=18087 docker compose -p hito3verify20261115 ps
curl --fail --silent --show-error http://localhost:18087/health
curl --fail --silent --show-error --max-time 180 \
  -F 'pdf_file=@sample_pdfs/meeting_minutes.pdf;type=application/pdf' \
  -F 'names=[{"first_name":"Thomas","last_name":"Muller"}]' \
  http://localhost:18087/api/extract | python3 -c 'import json,sys; x=json.load(sys.stdin); pages=sorted({n["bounding_box"]["page_number"] for n in x["extracted_names"] if n["name"] == "Thomas Muller"}); matches=[m for m in x["fuzzy_matches"] if m["matched_name"] == "Thomas Muller"]; assert pages == [0,1] and matches and matches[0]["score"] == 1.0; print("HTTP extraction: ok | pages:", pages, "| detected:", len(x["extracted_names"]), "| matches:", len(x["fuzzy_matches"]))'
curl --fail --silent --show-error --max-time 180 -H 'Transfer-Encoding: chunked' \
  -F 'pdf_file=@sample_pdfs/meeting_minutes.pdf;type=application/pdf' \
  -F 'names=[{"first_name":"Thomas","last_name":"Muller"}]' \
  http://localhost:18087/api/extract | python3 -c 'import json,sys; x=json.load(sys.stdin); pages=sorted({n["bounding_box"]["page_number"] for n in x["extracted_names"] if n["name"] == "Thomas Muller"}); assert pages == [0,1]; print("Chunked HTTP extraction: ok | pages:", pages, "| detected:", len(x["extracted_names"]))'
APP_PORT=18087 docker compose -p hito3verify20261115 down --volumes
```

**Relevant output:** both services were running; `/health` returned
`{"status":"ok"}`; regular and chunked HTTP extraction returned pages `[0, 1]`
and 22 occurrences; the first request returned a match for `Thomas Muller` with
score `1.0`. At the end, only the containers, network, and volume created for the
temporary project were removed. The pre-existing Compose project was not restarted.

### Evidence for human visual review

`docs_dev/meeting_minutes_names_visual.pdf` was generated from the real endpoint
response and the sample PDF. The destination was opened exclusively to avoid
overwriting an existing file. **12 boxes were drawn on page 0 and 10 on page 1**;
one span was omitted because it could not be safely associated. The copy remains
outside Git. **Human review is still pending** and is recorded as MT-002 in
[MANUAL_TEST_LOG.md](MANUAL_TEST_LOG.md).

```bash
docker run --rm --network none -i --user "$(id -u):$(id -g)" \
  -v "$PWD/sample_pdfs:/samples:ro" -v "$PWD/docs_dev:/output" \
  --entrypoint python ml-engineer-test-app:latest - <<'PY'
from collections import Counter
import json
from pathlib import Path
import pymupdf
from fastapi.testclient import TestClient
from app.main import app

source = Path('/samples/meeting_minutes.pdf')
destination = Path('/output/meeting_minutes_names_visual.pdf')
if destination.exists():
    raise SystemExit('The visual review file already exists; it was not overwritten')
with TestClient(app) as client:
    response = client.post('/api/extract', files={
        'pdf_file': ('meeting_minutes.pdf', source.read_bytes(), 'application/pdf'),
        'names': (None, json.dumps([{'first_name': 'Thomas', 'last_name': 'Muller'}])),
    })
    response.raise_for_status()
    result = response.json()
with pymupdf.open(source) as pdf:
    for item in result['extracted_names']:
        box = item['bounding_box']
        rect = pymupdf.Rect(box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height'])
        pdf[box['page_number']].draw_rect(rect, color=(1, 0, 0), width=0.8, overlay=True)
    with destination.open('xb') as output:
        pdf.save(output, deflate=True)
counts = Counter(item['bounding_box']['page_number'] for item in result['extracted_names'])
print('output: docs_dev/meeting_minutes_names_visual.pdf')
print('pages:', len(counts), '| boxes per page:', [counts[index] for index in range(2)])
print('fuzzy matches:', len(result['fuzzy_matches']))
PY
```

**Limitations:** the NER model may miss people or classify non-person text as a
person; geometric heuristics may also omit locations. Admission is per process,
not global; calibration under real load remains. The full-name visual test has
not yet been approved by a person. RAG and ingestion are outside this milestone.

## 2026-11-16 — Shared real-world smoke checks

**Environment:** Docker Desktop on macOS ARM64; Linux image built from the
project Dockerfile, with Tesseract and the spaCy model installed.

**Objective:** verify the shared scripts in `scripts/smoke/` without mocks and
without starting or restarting the existing Compose services.

```bash
docker build -t document-intelligence-smoke:local .
bash -n scripts/smoke/ejecutar.sh
bash scripts/smoke/ejecutar.sh --help
bash scripts/smoke/ejecutar.sh todo
```

**Result:** the image built and `todo` passed. The component checks located 22
occurrences and omitted one span because its association was unsafe; they found
one exact fuzzy pair for `Thomas Muller`. Over real HTTP, `/health` and valid
cases returned `200`; invalid JSON returned `422`, non-PDF content `400`, and an
oversized `names` field `413`. An empty list did not prevent name extraction. On
the synthetic cropped and rotated scan, the detected box center fell within the
known position. Temporary containers were removed.

`componentes` was also checked from an external directory using another local image
through `SMOKE_IMAGE`; it passed. An unknown argument returned exit code `2`, and
a missing image returned `1`. A controlled `SIGINT` interruption of `http`
returned `130` and removed the temporary container it had created. The
pre-existing Compose services remained running.

**Limitations:** the sample contains known names, and the fuzzy strings and
rotated scan use synthetic data; the services processing them are real. These
checks do not test Compose, Qdrant, load, or general accuracy. They overlap with
integration tests using `TestClient`, but the HTTP script starts Uvicorn and uses
real loopback. The 264-test suite was not rerun during this check; human visual
review MT-002 remains pending.

### Rerunning the suite before commits

A temporary image with pytest was built from the smoke image. Installing pytest
during the build required access to package dependencies; the tests were then run
in a container without external network access, with source, tests, and PDFs
mounted read-only:

```bash
docker build -t document-intelligence-pytest-verify-20261116:local - <<'EOF'
FROM document-intelligence-smoke:local
RUN uv pip install --target /tmp/test-deps "pytest==9.2.0"
EOF
docker run --rm --network none \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e PYTHONPATH=/tmp/test-deps:/app -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint python document-intelligence-pytest-verify-20261116:local \
  -m pytest -p no:cacheprovider /tests/unit /tests/integration
```

**Result:** `collected 264 items` → **`264 passed in 44.21s`**. This rerun does not
replace human visual review MT-002 and did not start or restart Compose.

To check intermediate commits without using pending working-tree changes, each
revision was extracted with `git archive` in another offline container: at
`b3b5bdf`, `test_bbox_and_fuzzy.py` passed its **17 tests**; at `673bc8b`, the
bounding-box, configuration, contract, asynchronous OCR, and HTTP-limit tests
passed **121 tests**. These suites include unit tests with simulated components.

## 2026-11-16 — Milestone 4: ingestion, recovery and persistence

**Environment:** macOS ARM64 with Docker Desktop; Python 3.12 in Linux ARM64
containers, Qdrant 1.13.6 and the pinned local MiniLM model. The pre-existing
Compose API and Qdrant were already running and were not recreated or stopped.

### Offline unit and integration suite

The temporary `document-intelligence-h4-tests:local` image was derived from an
earlier build of this Dockerfile with `pytest==9.2.0` installed. The current
application, tests and sample PDFs were mounted read-only; no network was
available to the test container:

```bash
docker run --rm --network none \
  -v "$PWD/app:/app/app:ro" -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e PYTHONPATH=/tmp/test-deps:/app -e SAMPLE_PDFS_DIR=/samples -e HF_HUB_OFFLINE=1 \
  --entrypoint python document-intelligence-h4-tests:local \
  -m pytest -p no:cacheprovider /tests/unit /tests/integration
```

**Observed result:** `collected 370 items` → **`370 passed in 50.01s`**. The
suite includes simulated adapters and failure injection as well as real local
OCR, the fixed embedding model and subprocess tests. It covers physical-page
records, admission, repeated cancellation, an overall timeout during a
simulated write, process death with persistent uncertainty, and cleanup guards.
The real tokenizer emitted `[UNK]` for a document symbol and a 110-character
word; the revised test confirms these are accepted while retaining original
text. Their vector representation can still lose information.

### Packaged-image smoke tests

```bash
docker build -t document-intelligence-h4-model:local .
SMOKE_IMAGE=document-intelligence-h4-model:local SMOKE_PACKAGED_APP=1 \
  bash scripts/smoke/ingestion.sh
SMOKE_IMAGE=document-intelligence-h4-model:local SMOKE_PACKAGED_APP=1 \
  bash scripts/smoke/ejecutar.sh todo
```

**Observed result:** the image built successfully. Both smoke scripts passed
using the code packaged in that image rather than a mounted `app/` directory.
The isolated ingestion test used real Uvicorn HTTP, OCR, embeddings, SQLite
and Qdrant: two sample PDFs were stored, the repeated PDF produced distinct
identities with the same input hash, and a synthetic scan retained its empty
first page without points for that page. Invalid PDF and no detected text
returned `400` and `422`, respectively. Completed IDs and points survived
recreation of only the test API and Qdrant using their test volumes.

The real cleanup CLI inventoried and removed a synthetic partial attempt while
preserving completed attempts. Preview, active-owner and completed-attempt
guards were checked. The partial record and a deliberately delayed point write
to real Qdrant simulated an ambiguous timeout; the point arrived after the first
preview, and cleanup without explicit quiescence confirmation remained refused.
No actual Qdrant server timeout or uncontrolled late network request was
induced. Separately, injected client failures before and after a real manifest
write checked refusal of an unmarked collection and revalidation of a matching
manifest. Untagged points, another ledger identity and an incompatible vector
dimension were refused. The extraction component and real-loopback HTTP checks
also passed, including the synthetic cropped and rotated scan.

The smoke tests used no published host ports. Their temporary containers,
networks and volumes were removed; the locally built test images were retained.
The original Compose containers remained running with the same container IDs.
`docker compose config --quiet`, shell syntax checks and `git diff --check`
passed. The running Compose API still uses its previous image and has not been
upgraded to this implementation.

**Limitations:** SQLite/Qdrant operations are not atomic, and an unknown write
can arrive late; quiescence remains an explicit operational responsibility.
The checks do not establish retrieval accuracy, production capacity or Linux
x86_64 behavior. `/api/ask`, Celery and the UI are outside this milestone;
human visual review MT-002 remains pending.

## 2026-11-16 — Interactive visual ingestion walkthrough

**Environment:** Docker Desktop on macOS ARM64, using the packaged
`document-intelligence-h4-model:local` image and Qdrant 1.13.6. The walkthrough
uses its own bridge network and two temporary volumes; API and dashboard ports
are published only on `127.0.0.1`.

```bash
SMOKE_IMAGE=document-intelligence-h4-model:local \
  bash scripts/smoke/recorrido_visual.sh --automatico
```

**Observed result:** all five stages passed. The example PDF produced four
fragments on two physical pages; a second ingestion retained the hash but had new
IDs. The real Qdrant point contained 384 dimensions and scored approximately 1
when queried with its own vector. A synthetic two-page scan recorded no text or
points on its first page. A consistent, standalone SQLite snapshot contained
three completed ingestions and the empty-page record.

The interactive pauses were also exercised with a pseudoterminal. During the
pauses an invalid PDF returned `400`, real Qdrant count and point-ID query
requests succeeded, and macOS SQLite opened the snapshot read-only and passed
`integrity_check`. The synthetic PDF and snapshot existed during the pauses and
were removed on exit. A separate controlled `SIGINT` exited `130` and ran the
cleanup. The dashboard HTTP route responded; an actual browser or DB Browser GUI
was not operated during this automated check.

**Issues and fixes:** Docker Desktop did not publish ports on the original
`--internal` network, so the visual walkthrough uses a separate bridge network
with loopback-only published ports. A point-ID similarity query excluded its
reference point, so the self-similarity check uses the stored vector. A snapshot
initially opened in the container but failed to open read-only on the host; the
backup is now converted to standalone `DELETE` journal mode and was rechecked
from macOS. No existing Compose container or volume was restarted or changed.
The walkthrough does not establish retrieval accuracy or replace MT-002.

## 2026-11-16 — Optional repeated-name recovery

**Environment:** Docker Desktop on macOS ARM64, Linux ARM64 containers with real
OCR, spaCy NER, Uvicorn HTTP, and the packaged `document-intelligence-name-recovery:local`
image. The existing Compose services were not restarted or modified.

The opt-in feature uses `NER_NAME_RECOVERY_PASSES=0..3`, with 0 as default.
Dedicated tests cover exact recovery, one-edit fuzzy recovery, compact OCR
segmentation, ambiguity rejection, cross-page matches, limits, cancellation,
and preservation of NER locations. Controlled NER omissions are explicitly
simulated fixtures; real HTTP checks use actual OCR and NER.

```bash
SMOKE_IMAGE=document-intelligence-name-recovery:local   bash scripts/smoke/recuperacion_nombres.sh --automatico
SMOKE_IMAGE=document-intelligence-name-recovery:local SMOKE_PACKAGED_APP=1   bash scripts/smoke/ejecutar.sh todo
SMOKE_IMAGE=document-intelligence-name-recovery:local SMOKE_PACKAGED_APP=1   bash scripts/smoke/ingestion.sh
```

**Observed result:** 421 unit and integration tests passed in 49.30s.
The real HTTP comparison produced 22 baseline name locations and 23 locations
at levels 1, 2 and 3, with original boxes retained. The four generated PDF
copies were readable and had red-overlay counts [12, 10] at level 0 and
[13, 10] at levels 1–3. The interactive pause preserved artifacts for
inspection and cleanup succeeded both after Enter and Ctrl-C. Packaged
extraction and ingestion smoke tests passed.

**Limitations:** these results do not establish precision/recall or human
approval of the recovered location. The exact visual case initially reported
by the user was not independently identified from supplied observations;
MT-002 remains pending. Long names, ambiguous fuzzy candidates and missing
OCR text are conservatively not recovered.

## 2026-11-16 — Component matching and temporary stage preview

**Environment:** Docker Desktop on macOS ARM64; Linux ARM64 containers using the
packaged `document-intelligence-stage-preview:local` image. Existing Compose
containers and volumes were not restarted or modified.

**Review and diagnosis:** real OCR of the sample found three occurrences of
the name investigated by the user. The previous implementation located one with
NER and recovered only one more: a standalone bullet created two overlapping,
equivalent normalized windows for the remaining occurrence, which were rejected
as ambiguous. The diagnosis and a provisional engineering assessment, not an
accuracy score, are recorded in [REVISION_RECUPERACION.md](REVISION_RECUPERACION.md).

**Changes:** exclude standalone punctuation at candidate boundaries and compare
full names component by component at recovery level 2; both name and surname
remain required. Add an opt-in, authenticated, single-process in-memory preview
of the latest extraction with the original PDF proxy and stage overlays. It is
disabled by default, expires after ten minutes, and is invalidated by the next
accepted extraction. Existing extraction and ingestion schemas are unchanged.

```bash
SMOKE_IMAGE=document-intelligence-stage-preview:local \
  bash scripts/smoke/recuperacion_nombres.sh --automatico
SMOKE_IMAGE=document-intelligence-stage-preview:local \
  bash scripts/smoke/vista_etapas.sh --automatico
SMOKE_IMAGE=document-intelligence-stage-preview:local SMOKE_PACKAGED_APP=1 \
  bash scripts/smoke/ejecutar.sh todo
SMOKE_IMAGE=document-intelligence-stage-preview:local SMOKE_PACKAGED_APP=1 \
  bash scripts/smoke/ingestion.sh
```

**Observed result:** 441 unit and integration tests passed in 51.02 seconds.
The real sample comparison retained 22 NER boxes and returned 24 at recovery
levels 1–3; the real OCR/spaCy regression found all three occurrences of the
investigated name. The preview smoke checked authentication, stage data, the PDF
proxy and invalidation of the old ID after a different extraction. An interactive
pseudoterminal check confirmed that the OCR, NER, recovery and final PDF overlays
were readable while paused and removed on exit. Extraction and ingestion smoke
tests passed with the packaged image. The ingestion cleanup timeout/late-write
fixtures remain simulated, as described in the earlier milestone entry.

**Issue fixed during verification:** the preview smoke initially interpreted
HTTP header names case-sensitively and incorrectly reported a successful
authenticated response as a failure. The verifier now uses the HTTP header
object's case-insensitive lookup; the repeated smoke passed.

**Limitations:** the preview is a bounded development aid for one trusted
operator and one process, not multi-tenant authentication or a shared UI store.
It is available only after a successful extraction; downloaded copies cannot be
revoked or securely erased. OCR, NER and matching quality require a representative
annotated evaluation; a higher count does not prove precision. MT-002 remains
pending human review.

## Hito 5 — Recuperación, concurrencia y fallos

El smoke aislado `SMOKE_IMAGE=document-intelligence-stage-preview:local bash
scripts/smoke/ingestion.sh` pasó con código local montado en solo lectura,
Qdrant, SQLite y embeddings reales en recursos temporales propios. Se comprobó
que una consulta concurrente y una limpieza no adquieren el lock durante otra
recuperación, que la cancelación libera el lock y permite consultar después, y
que Qdrant inaccesible y un ledger ausente producen errores controlados. El
ledger ausente no se creó. La demora para probar cancelación se inyectó después
de una respuesta real de Qdrant; no se produjo un timeout real del servidor.
El generador del pipeline era simulado y no se evaluó calidad de respuestas.
El lock es local y cooperativo: no valida concurrencia distribuida ni protege
contra administradores externos. Las pruebas HTTP de errores quedan pendientes.
La batería completa de unitarias e integración pasó: **504 pruebas en 60,16 s**
en un contenedor aislado con el código local montado en solo lectura.

Con `SMOKE_IMAGE=document-intelligence-stage-preview:local SMOKE_ASK_FAKE=1 bash
scripts/smoke/ingestion.sh` se verificó además `/api/ask` por HTTP real con OCR,
embeddings, Qdrant y SQLite reales, usando exclusivamente un generador simulado
inyectado en el servidor de pruebas. Se repitió tras recrear los contenedores
temporales. Se montó el código de `app/` en solo lectura; no es validación de
una imagen empaquetada nueva. Gemini, OpenAI y el modelo local no se llamaron.
Tras añadir los adaptadores, la batería completa pasó **538 pruebas en 61,48 s**;
las pruebas de protocolo de proveedores usaron HTTP simulado, no modelos reales.

## Template for future runs

````markdown
## YYYY-MM-DD — Milestone or change
**Environment:** system, architecture, and relevant versions.
**Objective:** what is being checked.
**Command:**
```bash
command executed
```
**Observed output:**
```text
relevant excerpt, no secrets
```
**Result:** passed / failed / partial.
**Diagnosis and fix:** how errors were investigated and corrected; explicitly
state anything that remains unresolved.
**Limitations / pending work:** what was not tested.
````
