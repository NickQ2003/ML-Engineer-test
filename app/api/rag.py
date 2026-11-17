"""Thin HTTP adapters for document ingestion and question answering."""

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.datastructures import UploadFile

from app.api.dependencies import get_ask_service, get_ingestion_service
from app.api.extraction_form import InvalidMultipartError, close_extraction_form, parse_extraction_form
from app.core.config import get_settings
from app.models.schemas import IngestResponse, RAGRequest, RAGResponse
from app.services.ask_input import InvalidQuestionError, normalize_question
from app.services.ask_service import AnswerGenerationError, AskService
from app.services.chunking_service import ChunkingError, ChunkingLimitError
from app.services.embedding_async import EmbeddingTimeoutError
from app.services.embedding_service import EmbeddingInputError, EmbeddingModelUnavailableError, EmbeddingProcessingError
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

@router.post(
    "/ask", response_model=RAGResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["question"], "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
    }}}}},
    responses={408: {"description": "Upload timeout"}, 413: {"description": "Request body too large"},
               422: {"description": "Invalid question"}, 503: {"description": "Service unavailable"},
               504: {"description": "Processing timeout"}},
)
async def ask(request: Request, service: Annotated[AskService | None, Depends(get_ask_service)]):
    # Validate manually to avoid echoing sensitive invalid inputs in FastAPI's
    # default validation-error payload. The body middleware bounds these reads.
    try:
        body = await request.json()
        question = normalize_question(RAGRequest.model_validate(body).question)
    except (ValueError, TypeError, UnicodeError, ValidationError, json.JSONDecodeError):
        raise HTTPException(422, "Invalid question") from None
    if service is None:
        raise HTTPException(503, "Question answering is not configured")
    try:
        async with asyncio.timeout(get_settings().ask_job_timeout_seconds):
            result = await service.ask(question)
        return JSONResponse(RAGResponse(answer=result.answer, sources=list(result.sources)).model_dump(),
                            headers={"Cache-Control": "no-store"})
    except (InvalidQuestionError, EmbeddingInputError):
        raise HTTPException(422, "Invalid question") from None
    except (TimeoutError, EmbeddingTimeoutError):
        raise HTTPException(504, "Question answering timed out") from None
    except (LedgerError, VectorStoreError, EmbeddingModelUnavailableError, EmbeddingProcessingError,
            AnswerGenerationError):
        raise HTTPException(503, "Question answering unavailable") from None
    except Exception:
        logger.error("Question answering failed unexpectedly")
        raise HTTPException(500, "Question answering failed") from None
