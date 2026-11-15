"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.extract import router as extract_router
from app.api.rag import router as rag_router
from app.core.config import get_settings
from app.models.schemas import HealthResponse

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.include_router(extract_router, prefix="/api", tags=["extraction"])
app.include_router(rag_router, prefix="/api", tags=["rag"])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report application liveness independently of external services."""
    return HealthResponse(status="ok")
