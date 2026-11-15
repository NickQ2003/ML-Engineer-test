import asyncio
import os
import time
from pathlib import Path

import anyio
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.ocr_service import InvalidPDFError, OCRService, PDFTooLargeError, get_ocr_service


def _timed_worker(pdf_bytes, settings):
    """Picklable worker used to observe real process concurrency."""
    started = time.monotonic()
    time.sleep(0.5)
    return started, time.monotonic(), os.getpid()


def _notifying_worker(pdf_bytes, settings):
    marker = Path(pdf_bytes.decode())
    marker.write_text("started")
    time.sleep(0.5)
    marker.write_text("finished")


def test_shared_service_is_reused():
    assert get_ocr_service() is get_ocr_service()


def test_concurrency_limit_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(ocr_max_concurrent_jobs=0)


def test_ocr_worker_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr("app.services.ocr_service._extract_pdf_in_worker", _timed_worker)

    async def run():
        service = OCRService(Settings(ocr_max_concurrent_jobs=1))
        task = asyncio.create_task(service.extract_async(b"test"))
        await asyncio.sleep(0.1)
        assert not task.done()
        return await task

    _, _, worker_pid = asyncio.run(run())
    assert worker_pid != os.getpid()


def test_ocr_concurrency_is_bounded(monkeypatch):
    monkeypatch.setattr("app.services.ocr_service._extract_pdf_in_worker", _timed_worker)

    async def run():
        service = OCRService(Settings(ocr_max_concurrent_jobs=2))
        return await asyncio.gather(*(service.extract_async(b"test") for _ in range(4)))

    intervals = asyncio.run(run())
    assert len({pid for _, _, pid in intervals}) == 2
    for start, _, _ in intervals:
        assert sum(other_start <= start < other_end for other_start, other_end, _ in intervals) <= 2


def test_async_worker_propagates_pdf_validation_errors():
    async def run():
        return await OCRService().extract_async(b"not a PDF")

    with pytest.raises(InvalidPDFError):
        asyncio.run(run())


def test_oversized_async_input_is_rejected_before_process_dispatch(monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError("oversized input was dispatched")

    monkeypatch.setattr("app.services.ocr_service.anyio.to_process.run_sync", should_not_run)

    async def run():
        return await OCRService(Settings(max_upload_bytes=4)).extract_async(b"12345")

    with pytest.raises(PDFTooLargeError):
        asyncio.run(run())


def test_started_worker_finishes_when_request_is_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.ocr_service._extract_pdf_in_worker", _notifying_worker)
    marker = tmp_path / "worker-state"

    async def run():
        service = OCRService(Settings(ocr_max_concurrent_jobs=1))
        async with anyio.create_task_group() as group:
            group.start_soon(service.extract_async, str(marker).encode())
            with anyio.fail_after(15):
                while not marker.exists():
                    await anyio.sleep(0.01)
            group.cancel_scope.cancel()
        assert marker.read_text() == "finished"

    anyio.run(run)


def test_cancelled_queued_request_does_not_start_worker(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.ocr_service._extract_pdf_in_worker", _notifying_worker)
    first_marker = tmp_path / "first"
    second_marker = tmp_path / "second"

    async def run():
        service = OCRService(Settings(ocr_max_concurrent_jobs=1))
        ready = anyio.Event()
        scope_holder = []

        async def queued_request():
            with anyio.CancelScope() as scope:
                scope_holder.append(scope)
                ready.set()
                await service.extract_async(str(second_marker).encode())

        async with anyio.create_task_group() as group:
            group.start_soon(service.extract_async, str(first_marker).encode())
            with anyio.fail_after(15):
                while not first_marker.exists():
                    await anyio.sleep(0.01)
            group.start_soon(queued_request)
            await ready.wait()
            await anyio.sleep(0.05)
            scope_holder[0].cancel()

        assert first_marker.read_text() == "finished"
        assert not second_marker.exists()

    anyio.run(run)
