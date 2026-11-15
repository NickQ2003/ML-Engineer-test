"""FastAPI dependencies shared by API routers."""

from fastapi import Request

from app.services.ocr_service import OCRService


def get_ocr_service(request: Request) -> OCRService:
    """Return the service created once during this application's lifespan."""
    return request.app.state.ocr_service
