"""FastAPI dependencies shared by API routers."""

from fastapi import Request

from app.services.ner_async import NERService
from app.services.ocr_service import OCRService


def get_ocr_service(request: Request) -> OCRService:
    """Return the service created once during this application's lifespan."""
    return request.app.state.ocr_service


def get_ner_service(request: Request) -> NERService:
    """Return the lazy worker pool created during the application's lifespan."""
    return request.app.state.ner_service
