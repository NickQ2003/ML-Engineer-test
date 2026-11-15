"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.extract import router as extract_router
from app.api.rag import router as rag_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.request_limits import ExtractionBodyLimitMiddleware
from app.models.schemas import HealthResponse
from app.services.ocr_service import OCRService

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging(settings.log_level)
    application.state.ocr_service = OCRService(settings)
    try:
        yield
    finally:
        await application.state.ocr_service.aclose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(ExtractionBodyLimitMiddleware, max_request_bytes=settings.max_request_bytes)

app.include_router(extract_router, prefix="/api", tags=["extraction"])
app.include_router(rag_router, prefix="/api", tags=["rag"])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report application liveness independently of external services."""
    return HealthResponse(status="ok")
