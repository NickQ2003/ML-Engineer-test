# ML Engineer Take-Home — Document Intelligence Service

Build, from scratch, a small production-quality service that extracts person
names (with their locations) from scanned PDFs, fuzzy-matches them against a
provided list, and answers questions about ingested documents using RAG
(Retrieval-Augmented Generation).

This repository started as an empty skeleton. The implemented extraction flow
and remaining RAG work are described below. We care as much about *how* it is
built (design, tests, engineering practices, decisions) as whether it runs.

## Why this exists (the problem)

Let's imagine a company that processes large volumes of scanned documents (contracts, KYC forms,
filings) that mention people. For **compliance**, it isn't enough to know *that*
a name appears — a reviewer has to be able to **find it on the page** and
**double-check that specific name against other records** (an ID, a sanctions
list, an internal system). A name with no location is not auditable.

So the core requirement is: **given a PDF, return each person's name together
with exactly where it sits on the page** (page number + bounding box). That
location is what lets a human or a downstream system verify the match against
other information. The fuzzy-matching and RAG pieces build on the same extracted
content. Keep this "locate it so it can be verified" purpose in mind — it's why
bounding-box accuracy and correct pages matter more than they might first seem.

---

## What you're building

A FastAPI service exposing four endpoints. The request/response **contract is
fixed** by `app/models/schemas.py` — do not change those shapes (add fields only
if you have a reason and document it).

### 1. `GET /health`
Liveness probe. Returns `{"status": "ok"}` (plus anything else useful).

### 2. `POST /api/extract`  — names + bounding boxes + fuzzy match
`multipart/form-data`:
- `pdf_file`: a (scanned) PDF.
- `names`: a JSON string — a list of `{"first_name": ..., "last_name": ...}`.

Pipeline:
1. **OCR** the PDF (it is scanned — there is no selectable text layer).
2. **NER**: extract **person** names only (no organizations, places, dates).
3. **Bounding boxes**: locate each extracted name in the page and return its box
   in **PDF coordinate space** (points, 72 per inch — *not* raw image pixels),
   with a **0-based page number**.
4. **Fuzzy match** the extracted names against the provided `names`.

Response: `ExtractionResponse` (see `schemas.py`) — `extracted_names[]` with a
`bounding_box`, and `fuzzy_matches[]`.

### 3. `POST /api/ingest` — index a document for RAG
`multipart/form-data` with `pdf_file`. OCR → chunk → embed → store in a vector
database. Response: `IngestResponse` (`status`, `chunks_stored`).

### 4. `POST /api/ask` — answer a question with RAG
JSON `{"question": "..."}`. Embed the question, retrieve the most relevant
chunks, call an LLM to answer **grounded in the retrieved context**, and return
`RAGResponse` (`answer`, `sources`).

---

## Requirements that matter (this is the actual evaluation)

> **Scope & time.** This is time-boxed — we do **not** expect fully isolated
> infrastructure. An **embedded / in-process vector store (e.g. Chroma) running
> locally is completely fine**, and you do not need Docker to pass. Things like a
> `docker-compose.yml` with an isolated vector DB, a hardened container, or a
> clean separation of services are **not required** — but if you add them, they
> count as **positive extras** in the evaluation. Prioritize a correct,
> well-structured core; reach for the infra polish only if you have time.

Anyone can wire libraries together. We're hiring an **ML Engineer**, so we look
for production judgment:

- **Architecture** — SOLID, dependency injection, separation of concerns.
  Services depend on abstractions (Protocols/ABCs), not concrete singletons.
  HTTP routers stay thin; business logic is testable without HTTP.
- **Configuration** — no hardcoded hosts/ports/model names/thresholds/DPI.
  Centralized, environment-overridable config.
- **Resilience** — timeouts and retries on external calls (LLM, vector store);
  meaningful failure modes; no silent `except: pass`.
- **Validation** — reject non-PDFs (by content, not just extension), empty
  files, oversized uploads; validate request bodies.
- **Resource management** — no leaked file handles or temp files.
- **Observability** — structured logging with useful context.
- **Correctness details we will check**, e.g.:
  - OCR must process **every** page (including the first).
  - Bounding boxes must be in **PDF space**, not image pixels.
  - Name matching must be **case-insensitive** and robust to OCR noise and
    accents (`María González` ~ `Maria Gonzalez`); prefer full-string
    similarity over substring tricks.
  - RAG chunking must not cut words in half; retrieval must be grounded (no
    hallucinated `sources`).
  - Vector-store IDs must be unique across documents (a second ingest must not
    overwrite the first).
- **Tests** — you write them. Cover the tricky logic above with unit tests
  (mock the heavy models / external services) and at least one end-to-end path.
  We look at *what* you chose to test and how.
- **Containerization (bonus, not required)** — if you have time, a `Dockerfile`
  and a `docker-compose.yml` that brings up the app **and its dependencies** with
  `docker compose up` (and `GET /health` passing) is a strong positive. Skipping
  it costs you nothing on the core; a local/embedded setup is fine.
- **Audit viewer (bonus, not required)** — a simple frontend that, given a PDF,
  **renders the pages and draws the returned bounding boxes over the detected
  names**. This is the compliance reviewer's tool: it's how a human confirms a
  name really is where the API says it is. Nothing fancy — a single self-
  contained page (e.g. vanilla JS + pdf.js) that calls `/api/extract` and
  overlays the boxes is plenty. Strong positive if included.
- **DESIGN.md** (≤ 3 pages) — component + sequence view, technology choices and
  trade-offs, how you'd scale to 1000+ PDFs/hour, failure modes and mitigations,
  and — if you were to route between models by query complexity — how.

---

## Suggested stack (substitute freely, but justify it in DESIGN.md)

- Python 3.12, FastAPI, `pytest`
- OCR: Tesseract via `pytesseract` + `PyMuPDF` (PDF → image)
- NER: spaCy (`en_core_web_sm`); handle non-English if you add it
- Fuzzy: `thefuzz`
- Embeddings: `sentence-transformers`
- Vector DB: **Chroma** (embedded/local — simplest) or Qdrant if you prefer a
  separate service
- LLM: any OpenAI-compatible endpoint (configurable base URL + key)

Dependencies live in `pyproject.toml` and are managed with **[uv](https://docs.astral.sh/uv/)** — change them as you see fit (`uv add ...`). Commit the resulting `uv.lock`.

---

## Deliverables

1. A **branch + pull request** against this repo with your implementation.
2. `DESIGN.md`.
3. A short **`DECISIONS.md`** — notable engineering decisions and trade-offs
   (bullets are fine). This is where your reasoning earns credit.
4. Tests (required). `Dockerfile` / `docker-compose.yml` are **optional bonus**.

## How we evaluate (weights)

| Criterion | Weight |
|---|---|
| Correctness — the pipeline works on the sample PDFs | 30% |
| Architecture & design (SOLID, DI, abstractions, thin routers) | 20% |
| Engineering practices (config, logging, resilience, validation) | 20% |
| Code quality & decisions | 15% |
| Tests you wrote (coverage of the tricky logic) | 10% |
| Design document | 5% |

**Positive extras (bonus, on top of the above):** an **audit viewer** that
overlays the bounding boxes on the PDF, containerization (`Dockerfile` /
`docker-compose.yml`), a truly isolated vector DB, multi-language support,
batching, or other production hardening. Not required — credited when present.

## Getting started

```bash
uv sync --extra dev                          # create .venv and install deps from pyproject.toml
# The locked spaCy model is installed by uv sync; no separate download command.
# Vector DB: embedded Chroma needs nothing extra. Only if you choose Qdrant:
#   docker run -p 6333:6333 qdrant/qdrant
uv run uvicorn app.main:app --reload
```

Sample scanned PDFs are in `sample_pdfs/`.

## Use of AI tools

Using AI assistants (Copilot, Claude, ChatGPT, agents) is **allowed and
expected** — that's how modern engineering works. Two conditions:

1. **Disclose it** in `DECISIONS.md` (what you used it for).
2. **Own every line.** In a follow-up conversation we will ask you to explain
   design choices, walk through your code, and make a live change. Code you
   can't reason about will not count in your favor.

We are evaluating *your* engineering judgment, not an agent's output. Submitting
something you don't understand is the one thing that fails this test.

---

## Docker development

The initial infrastructure runs FastAPI and Qdrant as separate containers. Docker
Engine and Docker Compose are required. Qdrant data persists in the `qdrant_data`
volume; it is not exposed directly to the host. The first build downloads the
Python dependencies and requires network access.

The Linux image installs the CPU-only PyTorch build; it does not require NVIDIA
drivers or CUDA. Changing to GPU acceleration requires a different build setup.

```bash
# Optional: customize defaults before starting.
cp .env.example .env
docker compose up --build
```

In another terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

`GET /health` is a liveness check for the API, not a Qdrant readiness check.
`POST /api/extract` is implemented; the ingestion and question-answering RAG
endpoints and their Qdrant, embedding and LLM integrations remain pending.

The OCR component returns words with page-relative character offsets and boxes
in PDF points, relative to the top-left of the **unrotated crop box**. It first
uses the visible orientation and tries an alternative when confidence is low;
this heuristic is not guaranteed to choose correctly and does not handle
arbitrary skew. Byte, page and rendered-pixel limits, DPI, language and timeouts
are configurable in `.env.example`. The image includes only English Tesseract
language data; other languages require their corresponding packages.

For asynchronous callers, `OCRService.extract_async(pdf_bytes)`,
`extract_file_async(path)` and `extract_stream_async(stream)` use supervised
POSIX worker processes. FastAPI creates one shared service during startup; the
dependency `app.api.dependencies.get_ocr_service` retrieves it. Concurrent jobs,
queued jobs, queue wait, upload, document and shutdown timeouts are configurable
per application process. A started job remains supervised after caller
cancellation; shutdown terminates its process group after the configured grace
period. This is not a durable queue and cannot guarantee cleanup after a machine
or container is killed abruptly.

### Extraction API

Send one PDF and a JSON array of name pairs as multipart form data. An empty list is valid.

```bash
curl --fail --silent --show-error http://localhost:8000/api/extract \
  -F 'pdf_file=@sample_pdfs/meeting_minutes.pdf;type=application/pdf' \
  -F 'names=[{"first_name":"Thomas","last_name":"Muller"}]'
```

The response follows `ExtractionResponse`: one `extracted_names` entry per
located occurrence and distinct `fuzzy_matches` pairs. Pages start at zero;
boxes use points relative to the top-left of the unrotated crop box. OCR is run
once per request. NER spans are mapped to OCR words on the same page; unsafe or
distant associations are omitted rather than assigned invented coordinates.
For a name split over adjacent lines, one enclosing rectangle may include blank
space. Full-name similarity ignores case and accents, removes apostrophes and
treats other punctuation as spaces. It uses `thefuzz.ratio` and returns scores
from 0 to 1; qualifying pairs retain deterministic input order.

`400`: invalid PDF or malformed `Content-Length`; `408`: upload timeout;
`413`: configured size or complexity limit; `422`: invalid multipart or names;
`503`: no capacity or dependency unavailable; `504`: processing timeout. Error
messages do not include document text.

| Variable | Default | Scope |
|---|---:|---|
| `MAX_REQUEST_BYTES` | 22020096 (21 MiB) | Entire extraction request body, including multipart boundaries, part headers, fields, and file. |
| `MAX_UPLOAD_BYTES` | 20971520 (20 MiB) | PDF limit enforced by OCR, separate from the request body. |
| `MAX_NAMES_BYTES` | 65536 (64 KiB) | Raw text-part size and UTF-8 size of the decoded JSON field. |
| `MAX_NAMES_COUNT` | 1000 | Maximum number of pairs; an empty list is valid. |
| `MAX_NAME_CHARS` | 512 | Maximum characters per query component and detected span. |
| `FUZZY_THRESHOLD` | 0.90 | Full-name matching threshold, finite and within `0..1`. |
| `EXTRACT_MAX_IN_FLIGHT` | 2 | Maximum in-flight extraction requests per application process. |
| `EXTRACT_UPLOAD_TIMEOUT_SECONDS` | 30 | Total body-read deadline. |
| `MAX_EXTRACTED_NAMES` | 1000 | Maximum NER occurrences before location mapping. |
| `MAX_FUZZY_COMPARISONS` | 100000 | Maximum possible distinct extracted-name/query comparisons. |
| `MAX_FUZZY_WORK_UNITS` | 100000000 | Maximum product of total normalized extracted-name and query lengths. |

The body guard counts the actual ASGI chunks consumed by the application even
when `Content-Length` is missing or too small. It returns `413` when that limit
is exceeded, and `400` for malformed or repeated `Content-Length` headers. It
does not consume a body that the application never reads, and a response already
started cannot be changed to `413`. The endpoint consumes its form before
responding. A per-process admission limit is acquired before multipart parsing;
excess requests are rejected without reading their body. Uploads have a deadline,
and the multipart parser limits the PDF and text fields incrementally. These
controls are **not global** across HTTP workers or replicas. If a caller cancels,
already-started OCR/NER work remains supervised under its own service limits.

### Person detection

`uv.lock` pins spaCy 3.8.18 and the official `en_core_web_sm` 3.8.0 wheel,
including its SHA-256. The wheel is downloaded from the official spaCy model
release during `uv sync` or image build; the build verifies it loads. Inference
requires no network or model download. `NER_MODEL` defaults to `en_core_web_sm`;
another model must already be installed and compatible. A missing model raises a
specific error instead of returning an empty result.

`NERService.detect_pages()` receives a list of page strings and returns a list of
`PersonSpan` lists in the same order, including empty pages. Offsets are Python
character positions local to each page, with an exclusive end; repeated
occurrences are kept. The async service executes spaCy in lazy, persistent POSIX
worker processes and reuses the model within each worker. The FastAPI lifespan
owns one service per application process. Synchronous callers may use
`SpacyPersonRecognizer` or `extract_names`; they must not run those blocking
functions directly on an async event loop.

| Variable | Default | Meaning |
|---|---:|---|
| `NER_MODEL` | `en_core_web_sm` | Installed model. |
| `NER_MAX_PAGE_CHARS` | 100000 | Characters per page, at most 1,000,000. |
| `NER_MAX_DOCUMENT_CHARS` | 1000000 | Total characters across pages. |
| `NER_MAX_CONCURRENT_JOBS` | 1 | Active NER workers per application process. |
| `NER_MAX_QUEUED_JOBS` | 1 | Additional queued jobs per application process. |
| `NER_QUEUE_TIMEOUT_SECONDS` | 10 | Maximum queue wait. |
| `NER_JOB_TIMEOUT_SECONDS` | 60 | Worker startup and inference deadline. |
| `NER_SHUTDOWN_TIMEOUT_SECONDS` | 10 | Grace period for active work and worker exit. |

The existing `MAX_PDF_PAGES` also bounds the number of pages sent to NER. Limits
are per process, not shared across HTTP workers or replicas. Extraction admission
also bounds the HTTP flow per process. On a timeout the worker is
terminated; a cancelled caller does not release an already-running job's capacity
until that work finishes or times out. Abrupt machine/container termination cannot
guarantee cleanup. The English small model predicts entity labels: filtering
`PERSON` does **not** guarantee all outputs are people or all people are found.
Sample OCR/NER observations and their limitations are in `docs/TEST_LOG.md`.

### Smoke checks without a UI

These optional checks use the current code, real Tesseract and spaCy, and the
repository's `sample_pdfs/meeting_minutes.pdf`; there are no mocks. They require
Docker Engine. Build a dedicated local image once, and rebuild it when the
Dockerfile or dependencies change. Building may require network access, but does
not start or restart the existing Compose services.

```bash
# From the repository root:
docker build -t document-intelligence-smoke:local .

# Run one check at a time, or both:
bash scripts/smoke/ejecutar.sh components
bash scripts/smoke/ejecutar.sh http
bash scripts/smoke/ejecutar.sh all
```

`components` checks OCR word offsets, NER spans, page bounds and fuzzy matching
directly. `http` sends real HTTP requests to Uvicorn in the temporary container:
it checks the sample PDF, an empty name list, invalid inputs and the center of a
box in an **in-memory synthetic**, cropped and rotated scan. The scripts return
nonzero on failure. They expect the known names in the sample; counts and model
predictions may vary across environments.

Each check uses disposable containers with `--network none`; code, scripts and
sample PDFs are mounted read-only. HTTP uses loopback inside the container and
publishes no host ports. Only the HTTP container created by the runner is stopped;
cleanup cannot be guaranteed after an abrupt termination. The container uses its
image's configuration, not the host's Compose `.env`. To use another compatible
local image, set `SMOKE_IMAGE`, for example:

```bash
SMOKE_IMAGE=another-local-image:tag bash scripts/smoke/ejecutar.sh all
```

The field-size check expects `MAX_NAMES_BYTES` at most 1 MiB and enough request
capacity for the sample PDF and that field; the image built above uses compatible
defaults. These checks do not test Compose, Qdrant, production traffic or general
model accuracy, and do not replace pytest or human visual review (MT-002).

Test commands, observed results and incident resolutions are recorded in
[`docs/TEST_LOG.md`](docs/TEST_LOG.md); human review results are recorded in
[`docs/MANUAL_TEST_LOG.md`](docs/MANUAL_TEST_LOG.md); engineering decisions are in
[`DECISIONS.md`](DECISIONS.md). Open OCR review findings and proposed fixes are
tracked in [`docs/OCR_REVIEW_PLAN.md`](docs/OCR_REVIEW_PLAN.md).
