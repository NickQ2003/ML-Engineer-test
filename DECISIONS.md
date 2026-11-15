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

- **Decision:** OCR every page once per structured extraction and retain each
  word's box, page-local text offsets and line identifiers. Boxes use PDF points
  relative to the top-left of the unrotated crop box. PDF rotation metadata is
  undone before Tesseract and incorporated in the coordinate transformation.
- **Why:** page-local offsets preserve the relationship between recognized text
  and words for later NER, while crop-relative coordinates provide a consistent
  convention for rotated and cropped pages.
- **Trade-offs:** PDF rotation metadata does not correct arbitrary scan skew or
  upside-down images without metadata. The default image includes only English
  language data. Byte size, page count, DPI and Tesseract timeout are bounded and
  configurable; a separate rendered-pixel limit and upload/HTTP validation remain
  to be implemented. The public extraction endpoint is not implemented yet.
- **Review status:** the current unconditional derotation fails when PDF
  rotation metadata corrects a physically rotated scan. A correction is planned
  in [the OCR review plan](docs/OCR_REVIEW_PLAN.md#r-03--orientación-de-pdf-y-escaneo).

## Asynchronous OCR without changing the API contract

- **Decision:** expose asynchronous OCR methods that dispatch blocking PDF and
  Tesseract work to AnyIO worker processes. A reusable service shares a
  configurable capacity limiter; the default is two jobs per application process.
  The synchronous methods remain available for non-async callers.
- **Why:** simply making a function `async` would not make PyMuPDF and Tesseract
  non-blocking. Process isolation also avoids parallel PyMuPDF work in threads.
- **Trade-offs:** each application worker has its own concurrency limit; queued
  callers and already-uploaded data are not globally bounded. A started task is
  shielded from AnyIO scope cancellation so the OCR worker can finish and clean
  up, which can consume resources after cancellation. There is no durable queue or
  job-status API. The future endpoint must reuse `get_ocr_service()` and await
  the result without changing the fixed response contract.
- **Review status:** direct `asyncio.Task.cancel()` can terminate the worker
  while leaving a Tesseract subprocess and temporary files. See
  [the OCR review plan](docs/OCR_REVIEW_PLAN.md#r-02--cancelación-y-limpieza-de-procesos).

## Use of AI tools

OpenCode was used to help plan and implement the initial infrastructure, translate
the README, investigate the CUDA image size, and document and run checks. The
project author remains responsible for reviewing, understanding, and validating
the resulting code and decisions.
