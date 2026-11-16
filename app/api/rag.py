"""Ingestion HTTP adapter. Question answering belongs to the next milestone."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile

from app.api.dependencies import get_ingestion_service
from app.api.extraction_form import InvalidMultipartError, close_extraction_form, parse_extraction_form
from app.core.config import get_settings
from app.models.schemas import IngestResponse
from app.services.chunking_service import ChunkingError, ChunkingLimitError
from app.services.embedding_async import EmbeddingTimeoutError
from app.services.embedding_service import EmbeddingModelUnavailableError, EmbeddingProcessingError
from app.services.ingestion_ledger import LedgerError
from app.services.ingestion_service import IngestionBusyError, IngestionService, IngestionTimeoutError, NoDocumentTextError
from app.services.ingestion_vectors import VectorStoreError
from app.services.ocr_service import (
    InvalidPDFError, OCRBusyError, OCRDocumentTimeoutError, OCRProcessingError, OCRUploadTimeoutError,
    PDFPageCountError, PDFPageTooLargeError, PDFTooLargeError,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/ingest", response_model=IngestResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["pdf_file"], "properties": {"pdf_file": {"type": "string", "format": "binary"}},
    }}}}},
    responses={400: {"description": "Invalid PDF"}, 408: {"description": "Upload timeout"},
               413: {"description": "Input exceeds limits"}, 422: {"description": "Invalid form or text cannot be chunked"},
               503: {"description": "Dependency or capacity unavailable"}, 504: {"description": "Processing timeout"}},
)
async def ingest(request: Request, service: Annotated[IngestionService, Depends(get_ingestion_service)]) -> IngestResponse:
    try:
        form = await parse_extraction_form(request, get_settings(), max_fields=0)
        try:
            if len(form.multi_items()) != 1 or "pdf_file" not in form or not isinstance(form["pdf_file"], UploadFile):
                raise InvalidMultipartError("Expected one PDF")
            return await service.ingest(form["pdf_file"])
        finally:
            await close_extraction_form(form)
    except (PDFTooLargeError, PDFPageCountError, PDFPageTooLargeError, ChunkingLimitError):
        raise HTTPException(413, "Ingestion input exceeds a configured limit") from None
    except (InvalidMultipartError, NoDocumentTextError, ChunkingError):
        raise HTTPException(422, "Invalid ingestion input or text cannot be chunked") from None
    except InvalidPDFError:
        raise HTTPException(400, "Invalid or unsupported PDF") from None
    except OCRUploadTimeoutError:
        raise HTTPException(408, "Upload timed out") from None
    except (OCRDocumentTimeoutError, EmbeddingTimeoutError, IngestionTimeoutError):
        raise HTTPException(504, "Ingestion processing timed out") from None
    except (IngestionBusyError, LedgerError, VectorStoreError, OCRBusyError, OCRProcessingError,
            EmbeddingModelUnavailableError, EmbeddingProcessingError):
        raise HTTPException(503, "Ingestion service unavailable; inspect the ingestion ledger") from None
    except Exception:
        logger.error("Ingestion failed unexpectedly")
        raise HTTPException(500, "Ingestion failed") from None

# TODO: implement POST /api/ask    (response_model=RAGResponse)
