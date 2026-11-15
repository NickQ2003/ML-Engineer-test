"""Exercise the real health route while a simulated OCR worker is running."""

import asyncio
import time
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.core.config import Settings
from app.main import app as actual_app
from app.services.ocr_service import OCRService


def _slow_worker(pdf_bytes, settings):
    marker = Path(pdf_bytes.decode())
    marker.write_text("started")
    time.sleep(0.7)
    marker.write_text("finished")
    return "done"


def test_health_remains_responsive_during_async_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.ocr_service._extract_pdf_in_worker", _slow_worker)
    service = OCRService(Settings(ocr_max_concurrent_jobs=1))
    marker = tmp_path / "worker-state"
    # Use the real health route with a test-only endpoint, not the unfinished API.
    test_app = FastAPI()
    test_app.include_router(actual_app.router)

    @test_app.post("/_test_ocr")
    async def run_ocr():
        return {"result": await service.extract_async(str(marker).encode())}

    async def run():
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ocr_request = asyncio.create_task(client.post("/_test_ocr"))
            async with asyncio.timeout(15):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            assert marker.read_text() == "started"
            health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
            assert not ocr_request.done()
            assert (await ocr_request).json() == {"result": "done"}

    asyncio.run(run())
