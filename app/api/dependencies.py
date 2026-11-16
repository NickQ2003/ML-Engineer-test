"""FastAPI dependencies shared by API routers."""

from fastapi import Request

from app.services.ner_async import NERService
from app.services.ocr_service import OCRService
from app.services.extraction_service import ExtractionService
from app.core.config import get_settings


def get_ocr_service(request: Request) -> OCRService:
    """Return the service created once during this application's lifespan."""
    return request.app.state.ocr_service


def get_ner_service(request: Request) -> NERService:
    """Return the lazy worker pool created during the application's lifespan."""
    return request.app.state.ner_service


def get_extraction_service(request: Request) -> ExtractionService:
    """Combine the lifespan-managed OCR and NER services for one request."""
    return ExtractionService(get_ocr_service(request), get_ner_service(request), get_settings())
