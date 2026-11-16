"""Orchestrate OCR, NER, spatial mapping and name comparison without HTTP."""

import asyncio
import logging
from typing import Protocol

from app.core.config import Settings
from app.models.document import OCRDocument
from app.models.extraction import PersonSpan
from app.models.schemas import ExtractionResponse, NamePair
from app.services.bbox_service import PageLocator
from app.services.fuzzy_service import match_names, normalize_name
from app.services.ner_service import NERProcessingError
from app.services.ocr_service import AsyncReadable

logger = logging.getLogger(__name__)


class OCRProvider(Protocol):
    async def extract_stream_async(self, stream: AsyncReadable) -> OCRDocument: ...


class NERProvider(Protocol):
    async def detect_pages(self, pages: list[str] | tuple[str, ...]) -> list[list[PersonSpan]]: ...


class ExtractionLimitError(ValueError):
    """A document would exceed a configured extraction complexity limit."""


class ExtractionService:
    def __init__(self, ocr: OCRProvider, ner: NERProvider, settings: Settings):
        self.ocr = ocr
        self.ner = ner
        self.settings = settings

    async def extract(self, stream: AsyncReadable, names: list[NamePair]) -> ExtractionResponse:
        document = await self.ocr.extract_stream_async(stream)
        entities = await self.ner.detect_pages([page.text for page in document.pages])
        if len(entities) != len(document.pages):
            raise NERProcessingError("NER returned an invalid page count")
        if sum(len(page) for page in entities) > self.settings.max_extracted_names:
            raise ExtractionLimitError("Document exceeds the configured entity limit")
        if any(len(span.text) > self.settings.max_name_chars for page in entities for span in page):
            raise ExtractionLimitError("Document contains an excessively long entity")

        # Keep the request admission slot until the CPU-bound assembly really
        # finishes, even if its caller disconnects while the thread is running.
        task = asyncio.create_task(asyncio.to_thread(self._assemble, document, entities, names))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _assemble(
        self, document: OCRDocument, entities: list[list[PersonSpan]], names: list[NamePair],
    ) -> ExtractionResponse:
        located = []
        skipped = 0
        for page, spans in zip(document.pages, entities, strict=True):
            locator = PageLocator(page)
            for span in spans:
                result = locator.locate(span)
                if result is None:
                    skipped += 1
                else:
                    located.append(result)
        if skipped:
            logger.warning("NER spans without safe OCR locations: count=%d", skipped)

        distinct_names = list(dict.fromkeys(item.name for item in located))
        if len(distinct_names) * len(names) > self.settings.max_fuzzy_comparisons:
            raise ExtractionLimitError("Document exceeds the configured comparison limit")
        extracted_chars = sum(len(normalize_name(name)) for name in distinct_names)
        query_chars = sum(len(normalize_name(f"{name.first_name.strip()} {name.last_name.strip()}")) for name in names)
        if extracted_chars * query_chars > self.settings.max_fuzzy_work_units:
            raise ExtractionLimitError("Document exceeds the configured matching work limit")
        matches = match_names(distinct_names, names, self.settings.fuzzy_threshold)
        return ExtractionResponse(extracted_names=located, fuzzy_matches=matches)
