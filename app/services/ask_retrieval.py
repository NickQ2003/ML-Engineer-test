"""Bounded, read-only Qdrant retrieval checked against the ingestion ledger.

Only cooperating writers using the same host and collection lock are supported.
The caller owns the ledger and vector-store lifecycle; no collection is created.
"""

import math
from uuid import UUID

from qdrant_client import models

from app.core.embedding_model import MODEL_DIMENSIONS, MODEL_NAME, MODEL_REVISION
from app.services.ask_service import RetrievedFragment
from app.services.ingestion_vectors import VectorCompatibilityError, VectorStoreError, chunk_id
from app.services.ocr_service import _run_blocking


class LedgerRetriever:
    def __init__(self, ledger, store, *, max_candidates: int = 100):
        if type(max_candidates) is not int or not 1 <= max_candidates <= 1000:
            raise ValueError("Invalid candidate limit")
        self.ledger, self.store, self.max_candidates = ledger, store, max_candidates

    async def retrieve(self, vector: list[float], *, limit: int) -> list[RetrievedFragment]:
        if (type(limit) is not int or not 1 <= limit <= self.max_candidates
                or not isinstance(vector, list) or len(vector) != MODEL_DIMENSIONS
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
                or not any(vector)):
            raise ValueError("Invalid retrieval input")
        store = self.store
        # The nonblocking collection lock spans both Qdrant and ledger reads.
        # Ingestion and maintenance acquire this same lock for mutations.
        with self.ledger.lock(store.lock_name):
            identity = await _run_blocking(lambda: self.ledger.identity(create=False))
            if identity != store.ledger_id:
                raise VectorCompatibilityError("Ledger identity does not match vector collection")
            await store.ensure(create=False)
            conditions = [store._condition(key, value) for key, value in (
                ("kind", "chunk"), ("schema_version", 1), ("ledger_id", store.ledger_id),
                ("model_name", MODEL_NAME), ("model_revision", MODEL_REVISION),
            )]
            result = await store._call(
                store.client.query_points, read=True, collection_name=store.collection,
                query=vector, query_filter=models.Filter(must=conditions),
                limit=self.max_candidates, with_payload=True, with_vectors=False,
                timeout=store.settings.qdrant_timeout_seconds,
            )
            if len(result.points) > self.max_candidates:
                raise VectorStoreError("Invalid vector search result")
            ordered = []
            for point in result.points:
                if type(point.score) not in (int, float) or not math.isfinite(point.score):
                    raise VectorStoreError("Invalid vector search score")
                ordered.append(point)
            ordered.sort(key=lambda point: (-point.score, str(point.id)))
            records, selected = {}, []
            for point in ordered:
                payload = point.payload
                if not isinstance(payload, dict):
                    raise VectorStoreError("Invalid vector payload")
                try:
                    ingestion_id = str(UUID(payload["ingestion_id"]))
                    document_id = str(UUID(payload["document_id"]))
                    index, page = payload["chunk_index"], payload["page_number"]
                    if (type(index) is not int or index < 0 or type(page) is not int or page < 0
                            or payload["chunk_id"] != chunk_id(ingestion_id, index)
                            or str(point.id) != payload["chunk_id"]
                            or payload["kind"] != "chunk" or payload["schema_version"] != 1
                            or payload["ledger_id"] != identity or payload["model_name"] != MODEL_NAME
                            or payload["model_revision"] != MODEL_REVISION):
                        raise ValueError
                    text, filename = payload["text"], payload["source_file"]
                    if (not isinstance(text, str) or not text.strip() or len(text) > store.settings.ingest_max_page_chars
                            or not isinstance(filename, str) or not filename.strip() or len(filename) > 255
                            or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
                        raise ValueError
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise VectorStoreError("Invalid vector payload") from None
                if ingestion_id not in records:
                    records[ingestion_id] = await _run_blocking(self.ledger.get, ingestion_id)
                record = records[ingestion_id]
                if (record.document_id != document_id or record.collection != store.collection
                        or record.endpoint_key != store.endpoint or record.model_name != MODEL_NAME
                        or record.model_revision != MODEL_REVISION
                        or record.processing_signature != payload.get("processing_signature")):
                    raise VectorCompatibilityError("Vector identity does not match ingestion ledger")
                if record.state != "completed":
                    continue
                if (record.write_uncertain or type(record.expected_chunks) is not int
                        or index >= record.expected_chunks or type(record.physical_pages) is not int
                        or page >= record.physical_pages):
                    raise VectorCompatibilityError("Invalid completed ingestion metadata")
                selected.append(RetrievedFragment(text, f"{filename} — página {page + 1} — documento {document_id}"))
                if len(selected) == limit:
                    break
            if len(selected) < limit and len(result.points) == self.max_candidates:
                # More eligible points may be hidden beyond the bounded search.
                # Do not misreport an incomplete search as absence of evidence.
                raise VectorStoreError("Candidate search limit reached before retrieval was complete")
            return selected
