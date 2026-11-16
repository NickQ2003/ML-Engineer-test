# Engineering decisions

## CPU-only PyTorch in Linux containers

- **Decision:** install PyTorch from the official CPU index on Linux, while
  keeping the normal PyPI source on other platforms. PyTorch is declared as a
  direct dependency so the index also applies when `sentence-transformers`
  requires it. The lockfile records the resulting dependencies.
- **Why:** the first image included CUDA/NVIDIA dependencies despite the
  initial deployment not requiring a GPU. The same Docker image-size metric
  fell from 10,691,090,779 to 629,766,551 bytes after switching to CPU and
  reducing cached/duplicated layers. The reduction is not solely due to CUDA.
- **Trade-off:** the Linux build cannot use CUDA acceleration. A GPU deployment
  requires a different dependency/build configuration. CPU inference can be
  slower. The container was tested on Linux ARM64; Linux x86_64 remains untested.
- **Verification:** PyTorch reported `2.15.0+cpu` and `torch.version.cuda` as
  `None`; a tensor operation and `sentence-transformers` import succeeded. See
  [the test log](docs/TEST_LOG.md) for commands and observed results.

## OCR coordinates and resource limits

- **Decision:** OCR every page per structured extraction and retain each
  word's box, page-local text offsets and line identifiers. Boxes use PDF points
  relative to the top-left of the unrotated crop box. OCR starts with the
  visible orientation and tries an alternative when confidence is low; the
  selected orientation is incorporated in the coordinate transformation.
- **Why:** page-local offsets preserve the relationship between recognized text
  and words for later NER, while crop-relative coordinates provide a consistent
  convention for rotated and cropped pages.
- **Trade-offs:** confidence-based orientation is heuristic and may select the
  wrong result; arbitrary skew is not corrected. The default image includes only
  English language data. Byte size, page count, rendered pixels, DPI and timeouts
  are bounded and configurable. HTTP validation and admission are described below.
  See [the OCR review plan](docs/OCR_REVIEW_PLAN.md) for remaining limitations.

## Asynchronous OCR without changing the API contract

- **Decision:** expose asynchronous OCR methods that dispatch blocking PDF and
  Tesseract work to supervised POSIX subprocesses in private job directories.
  A service created in the FastAPI lifespan shares admission and concurrency
  limits; the default is two running and two queued jobs per application process.
  The synchronous methods remain available for non-async callers.
- **Why:** simply making a function `async` would not make PyMuPDF and Tesseract
  non-blocking. Process isolation also avoids parallel PyMuPDF work in threads.
- **Trade-offs:** limits are per application worker, not global. The endpoint
  now adds per-process admission before multipart parsing. A cancelled caller does
  not immediately stop a started job; the service supervises it through a total
  deadline and uses process-group cleanup on shutdown or timeout. No durable
  queue exists, and abrupt machine/container termination can defeat cleanup.
  The endpoint uses the lifespan-managed dependency and preserves the fixed
  response contract. See [the review plan](docs/OCR_REVIEW_PLAN.md).

## Resource admission and logging

- **Decision:** reject pages above a configurable 20-million-pixel default
  before rendering. Bound active and queued OCR jobs, queue wait, stream read,
  total document processing and shutdown time per application process. Apply
  `LOG_LEVEL` to application and Uvicorn loggers; application records use JSON.
- **Trade-offs:** the pixel limit needs calibration against actual memory and
  document sizes. Limits are not shared between server processes or replicas.
  HTTP admission is bounded per process before multipart parsing; global limits
  remain pending. Logs contain job status and page counts, not document content
  or credentials.
- **Preliminary measurement:** two concurrent OCR jobs on a synthetic raster
  page near the 20-million-pixel limit peaked at 985.3 MiB in an isolated
  container capped at 2 GiB. This is not a production capacity guarantee; see
  [the measurement and its limitations](docs/OCR_MEMORY_CALIBRATION.md).

## Extraction input contracts and request byte guard

- **Decision:** retain the public schemas unchanged. Introduce an immutable,
  page-local `PersonSpan` and validate the `names` JSON against the
  existing `NamePair`, rejecting blank names without rewriting the original
  strings. Bound the UTF-8 size of the decoded field to 64 KiB and its list to
  1,000 items by default; allow an empty list. Reserve a configurable, finite
  fuzzy threshold of 0.90.
- **HTTP protection:** for `POST /api/extract`, limit the complete consumed body
  to 21 MiB by default, including multipart overhead. Reject a declared excessive
  length early, reject malformed/repeated lengths, and count received bytes even
  when the header is absent or false. The separate OCR PDF limit remains 20 MiB.
- **Implementation trade-off:** the pure ASGI guard raises a private
  `BaseException` control signal to escape FastAPI's generic body-parse error
  handling and return `413`. With the locked Starlette version the multipart
  parser closes temporary files when unwinding; tests cover a file rolled to disk,
  cancellation and a real chunked HTTP request. This behavior must be rechecked
  when upgrading Starlette or changing middleware layers. If a response has
  already started, the guard propagates the error rather than sending a second
  response; the ASGI server must handle the interrupted response.
- **Limits:** the guard only counts bytes the application reads. It now also
  admits at most two in-flight requests per application process and limits upload
  time. The endpoint uses a bounded multipart parser that rejects excessive file
  and text parts with `413` and requires a final boundary. Global worker/replica
  limits remain pending.

## Page-local NER and reproducible model

- **Decision:** use the official English `en_core_web_sm` 3.8.0 model with
  spaCy 3.8.x; `uv.lock` fixes the wheel URL, SHA-256 and resolved spaCy 3.8.18.
  Install the model with the other dependencies at build/sync time and check
  that it loads during image construction. Inference does not download models.
- **Offsets:** process each OCR page separately and return every `PERSON` span
  in original order, with Python character offsets local to that page and an
  exclusive end. Preserve original text and repeated occurrences; validate the
  correspondence between offsets and text again at the worker boundary.
- **Concurrency:** `NERService` runs a bounded number of lazy, persistent POSIX
  worker processes and loads the model once in each worker. Jobs and the queue,
  per-page and per-document character counts, queue wait, job deadline and shutdown
  are bounded. Documents move over private process pipes, not log messages; the
  worker releases request-local references afterwards, without claiming secure
  memory erasure. A
  started job remains supervised if its caller cancels; a timeout or shutdown
  terminates an unresponsive process group. The FastAPI lifespan shares one
  service per application process; `/api/extract` uses that shared service.
- **Trade-offs:** model download requires network and the upstream release to be
  available during installation. Limits, including admission of the HTTP flow,
  are per application process, not global. Workers require
  POSIX and cleanup cannot be guaranteed after an abrupt machine/container death.
  The English small model can miss names or label non-persons as `PERSON`; no
  accuracy or multilingual guarantee is claimed. Observations on sample PDFs are
  recorded in [the test log](docs/TEST_LOG.md).

## Extraction orchestration, locations and matching

- **Decision:** parse one `pdf_file` and one JSON `names` field incrementally;
  acquire HTTP admission before parsing and run OCR once. Detect page-local NER
  spans, associate them with the same OCR page's ordered words, then union their
  boxes. Preserve one location per occurrence. If offsets, blocks, line adjacency
  or geometry are inconsistent, omit the location rather than invent a box.
- **Geometry trade-off:** same-line word gaps may not exceed four times the
  larger word height; adjacent lines must share block and paragraph and have a
  bounded vertical gap. Partial-word spans use the complete OCR word's box. A
  wrapped name uses one enclosing rectangle, which may contain whitespace.
  These conservative heuristics can omit difficult but genuine names.
- **Matching:** normalize full names with Unicode decomposition, casefold and
  accent removal; remove apostrophes and convert other punctuation to spaces.
  Use full-string `thefuzz.ratio`, score `0..1`, include all pairs at or above
  the configured threshold and deduplicate identical string pairs. Preserve
  original detected names and the requested spelling apart from outer whitespace.
- **Limits and trade-offs:** 1,000 NER spans, 512 characters per query-name
  component or detected span, and 100,000 potential comparisons per document by
  default. The product of total normalized extracted and query lengths is also
  bounded to limit long-string matching work. Exceeding a limit rejects rather
  than truncates.
  Admission and OCR/NER limits are per process, not global. A cancelled HTTP
  caller can release its HTTP slot while started OCR/NER work remains supervised
  by those services' independent bounds. Human review of full-name boxes and
  production capacity calibration remain pending.

## Use of AI tools

OpenCode was used to help plan and implement infrastructure, OCR, NER and the
initial extraction input safeguards, translate the README, investigate CUDA image size
and review findings, and document and run checks. The project author remains
responsible for reviewing, understanding, and validating the resulting code and
decisions.
