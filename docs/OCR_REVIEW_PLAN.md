# Decision plan — OCR findings

**Date:** 2026-11-15

**Status:** fixes and HTTP integration implemented; global limits and production
calibration pending.

**Scope:** OCR service and its asynchronous execution, before their integration
into `/api/extract`. The following points preserve the review evidence and
decisions; implementation status is summarized in the table.

## Objective

Fix the issues that can affect name placement, resources, and document
confidentiality while preserving the HTTP contract required by the test. The
indicated alternatives are recommendations to implement and validate; any
design change must be recorded in `DECISIONS.md`.

## Summary and proposed order

| Order | ID | Priority | Finding | Status |
|---|---|---|---|---|
| 1 | R-01 | High | A small PDF can produce a huge image | Fixed in the service; threshold to be calibrated |
| 2 | R-02 | High | Direct cancellation can leave Tesseract and temporary files behind | Fixed in controlled shutdown/timeout handling on POSIX |
| 3 | R-03 | High | Metadata rotation can correct an image that has already been rotated | Mitigated using a heuristic; accuracy not guaranteed |
| 4 | R-04 | Medium | The process limit does not bound the queue or PDF copies | Partial: per-process HTTP admission; global limit still needed |
| 5 | R-05 | Medium | The cached provider can create multiple instances during concurrent startup | Fixed in the lifecycle and connected to the endpoint |
| 6 | R-06 | Low | `LOG_LEVEL` is configured but not applied | Fixed and tested |

`/api/extract` applies per-process admission before multipart parsing, byte
limits and a read deadline, and uses the shared OCR dependency. The limits
remain per process, not global, and no cleanup is guaranteed if the entire
machine crashes abruptly.

## Post-review implementation

- **R-01:** `MAX_PAGE_PIXELS=20000000` by default; the area is estimated before
  rendering, and oversized pages are rejected with `PDFPageTooLargeError`. An
  exploratory measurement supports keeping it provisionally; it must still be
  calibrated for the actual deployment. See [measurements](OCR_MEMORY_CALIBRATION.md).
- **R-02:** supervised POSIX subprocesses in separate groups and private
  temporary directories. A canceled request does not abandon a job already
  started; the overall timeout or controlled shutdown terminates the group.
  Tests with a simulated subprocess that ignores `SIGTERM` verify that it does
  not remain active and no temporary files remain. An abrupt crash or a
  `SIGKILL` of the container offers no cleanup guarantees; behavior on the real
  endpoint still needs verification.
- **R-03:** OCR in the visible orientation and a second pass if mean confidence
  is below the configurable threshold; the higher confidence is selected. Real
  OCR and boxes are tested with scans in both orientations, with cropping and
  four rotations. Confidence is a heuristic, not an accuracy guarantee.
- **R-04:** per-process limits on active and queued jobs, maximum wait, read
  timeout, and `extract_stream_async()` that acquires admission before reading.
  The HTTP endpoint limits admitted requests per process before multipart
  parsing, the full size, and upload time. Requests beyond capacity are rejected
  with `503`; there is no global limit across workers or replicas.
- **R-05:** the service is created in the FastAPI lifecycle and retrieved through
  `app.api.dependencies.get_ocr_service`, used by `/api/extract`. The shared
  instance and the real endpoint are tested with OCR and NER.
- **R-06:** `LOG_LEVEL` is validated and applied to application and Uvicorn
  loggers; the application's own logs are emitted as JSON. The level and output
  are tested.

## R-01 — Rendered-pixel limit

**Evidence:** a 522-byte PDF with a 10,000 × 10,000-point page reached rendering.
At 200 DPI, this amounts to approximately 771.6 million pixels and 2.31 GB for a
single RGB copy; that allocation was not performed during the review.

**Recommended decision:** add a configurable maximum number of pixels per page.
Conservatively calculate the rendered dimensions before calling `get_pixmap()`;
reject pages that exceed the limit with a specific error. Start by evaluating a
20-million-pixel limit and adjust it to the actual available memory, considering
that each job can create several copies and that there is more than one
concurrent process. The final number requires measurement.

**Alternative:** automatically reduce the DPI. This is less predictable for OCR
accuracy; adopt it only if an acceptable minimum is defined and tested.

**Acceptance criteria:**

- A huge page is rejected before the image is allocated.
- Limits, invalid dimensions, and configurable DPI are tested.
- The sample PDFs are still processed; the HTTP-facing error is documented.

## R-02 — Process cancellation and cleanup

**Evidence:** with `asyncio.Task.cancel()`, the worker process was observed to
terminate while a simulated Tesseract subprocess remained active and two
temporary files remained. The current test covers cancellation through AnyIO,
not this case. The simulated subprocess was terminated when the test ended.

**Recommended decision:** supervise jobs outside the lifetime of an individual
request. A request canceled while waiting for capacity must not start OCR; if
the job has already started, keep it supervised until completion or until an
explicit overall limit. Retain the concurrency permit while the job remains
alive. Define a shutdown and cleanup strategy for the process tree and a private
temporary directory per job, including during server shutdown or forced worker
termination.

**Alternative:** cancel the job immediately. This requires controlling the
termination of Tesseract and its descendants before releasing capacity, as well
as cleaning up temporary files; killing only the worker is not sufficient.

**Acceptance criteria:**

- Test direct `asyncio.Task.cancel()` cancellation, AnyIO cancellation, and
  canceled queued tasks; no orphaned subprocesses or temporary files remain.
- Verify that the job limit is still enforced during cancellations.
- Test behavior during server shutdown and document limits and exceptions,
  without claiming guarantees that cannot be met during a total crash.
- Maintain a reasonable maximum time for the complete document, in addition to
  the individual Tesseract timeout per page.

## R-03 — PDF and scan orientation

**Evidence:** a PDF with a physically rotated image and metadata that makes it
visible in the correct orientation produced `ALICE SMITH` when OCR was applied
to the displayed page, but the service returned incorrect text when undoing the
rotation.

**Recommended decision:** first evaluate the page in its visible orientation and
define an alternative orientation when there are signs of low quality. Compare
the options explicitly and preserve the transformation that allows boxes to be
returned in points within the unrotated crop. A second pass can increase the
time, and a confidence score does not guarantee detection of all errors; measure
behavior with representative examples.

**Alternatives:** always trust metadata or always trust the unrotated image.
Both fail in some combinations of physical orientation and metadata.

**Acceptance criteria:**

- Real OCR on horizontal content with rotation metadata and on a physically
  rotated image whose metadata rotation corrects the view.
- Check text and boxes for 0°, 90°, 180°, and 270°, with and without cropping.
- Keep the limitation regarding arbitrary skew documented.

## R-04 — Bounded admission and queue

**Evidence:** with capacity occupied, four 1 MB inputs were serialized before a
worker could start. The current limiter restricts processes, not the number of
requests or copies of data waiting.

**Recommended decision:** establish bounded admission before accumulating the
full payload in the future endpoint, also limit the wait for capacity, and use a
documented HTTP response when no capacity is available. In the service layer,
acquire capacity before any large serialization and evaluate options to avoid
blocking the event loop when copying data. Retain the per-process limit as local
protection and document that multiple workers multiply the total capacity.

**Acceptance criteria:**

- Tests with more requests than capacity, large inputs, and cancellation.
- API health remains responsive under load; the queue has a clear limit.
- Full payloads do not accumulate without limit; errors and capacity release are
  tested. Define a global policy if multiple workers or replicas are used.

## R-05 — Shared service instance

**Evidence:** four initial concurrent calls from threads to the function
decorated with `lru_cache` created four instances in a controlled test. The
problem would affect an integration that calls it concurrently from threads, for
example as a synchronous dependency; `/api/extract` did not yet exist.

**Recommended decision:** create the service once in the FastAPI lifecycle and
retrieve it through `app.state`, while keeping injection replaceable in tests.
Avoid relying on `lru_cache` as a guarantee of unique initialization.

**Acceptance criteria:** tests of concurrent initialization and the shared limit
using the real HTTP dependency when the endpoint is implemented.

## R-06 — Log configuration

**Evidence:** `LOG_LEVEL` is included in Compose and `Settings`, but is not used
to configure logging or Uvicorn.

**Recommended decision:** connect it to the effective logging configuration and
validate the accepted values; if it is not implemented, do not present it as an
operational option. Do not log credentials or sensitive document content.

**Acceptance criterion:** a test changes the level and verifies which records
are emitted; the documentation reflects the actual behavior.

## Delivery sequence

1. Fix R-01 and R-02 with tests and one log entry per change.
2. Fix R-03 and check boxes with real OCR and fixtures for both cases.
3. Prepare R-04 and R-05 when integrating the endpoint, before exposing it.
4. Resolve R-06 and complete observability without logging sensitive data.
5. Run the entire suite in the rebuilt image, record the results, and update
   `DECISIONS.md`. Do not mark findings as resolved until they pass their
   acceptance criteria.
