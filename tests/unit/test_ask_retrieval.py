import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.embedding_model import MODEL_NAME, MODEL_REVISION
from app.services.ask_retrieval import LedgerRetriever
from app.services.ingestion_vectors import VectorCompatibilityError, VectorStoreError, chunk_id


class Ledger:
    def __init__(self, records):
        self.records, self.locked = records, False

    @contextmanager
    def lock(self, name):
        assert name == "collection-test" and not self.locked
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def identity(self, *, create):
        assert self.locked and not create
        return "ledger"

    def get(self, ingestion_id):
        assert self.locked
        return self.records[ingestion_id]


class Store:
    ledger_id = "ledger"
    lock_name = "collection-test"
    collection = "collection"
    endpoint = "endpoint"
    settings = SimpleNamespace(qdrant_timeout_seconds=10, ingest_max_page_chars=1000)

    def __init__(self, ledger, points):
        self.ledger, self.points = ledger, points
        self.client = SimpleNamespace(query_points=self.query_points)

    def _condition(self, key, value):
        from qdrant_client import models
        return models.FieldCondition(key=key, match=models.MatchValue(value=value))

    async def ensure(self, *, create):
        assert self.ledger.locked and not create

    async def _call(self, fn, *, read, **kwargs):
        assert self.ledger.locked and read
        return await fn(**kwargs)

    async def query_points(self, **kwargs):
        assert kwargs["limit"] == 10 and kwargs["with_vectors"] is False
        return SimpleNamespace(points=self.points)


def candidate(state="completed", *, text="private fragment", score=0.9):
    ingestion, document = str(uuid4()), str(uuid4())
    cid = chunk_id(ingestion, 0)
    record = SimpleNamespace(document_id=document, collection="collection", endpoint_key="endpoint",
                             model_name=MODEL_NAME, model_revision=MODEL_REVISION,
                             processing_signature="signature", state=state, write_uncertain=False,
                             expected_chunks=1, physical_pages=1)
    payload = {"kind": "chunk", "schema_version": 1, "ledger_id": "ledger", "ingestion_id": ingestion,
               "document_id": document, "chunk_id": cid, "chunk_index": 0, "page_number": 0,
               "model_name": MODEL_NAME, "model_revision": MODEL_REVISION, "processing_signature": "signature",
               "text": text, "source_file": "test.pdf"}
    return ingestion, record, SimpleNamespace(id=cid, payload=payload, score=score)


def retrieve(items, limit=1):
    ledger = Ledger({ingestion: record for ingestion, record, _ in items})
    store = Store(ledger, [point for _, _, point in items])
    result = asyncio.run(LedgerRetriever(ledger, store, max_candidates=10).retrieve([1.0] * 384, limit=limit))
    assert not ledger.locked
    return result


def test_skips_incomplete_attempt_and_returns_completed_candidate():
    unfinished = candidate("writing", score=0.99)
    completed = candidate(text="eligible", score=0.8)
    result = retrieve([unfinished, completed])
    assert len(result) == 1 and result[0].text == "eligible"
    assert "página 1" in result[0].source and completed[1].document_id in result[0].source


@pytest.mark.parametrize("state", ["processing", "writing", "failed", "cleanup_pending", "cleaned"])
def test_noncompleted_ingestion_never_returns_text(state):
    assert retrieve([candidate(state)]) == []


def test_inconsistent_ledger_identity_fails_closed():
    item = candidate()
    item[1].document_id = str(uuid4())
    with pytest.raises(VectorCompatibilityError):
        retrieve([item])


@pytest.mark.parametrize("field,value", [("chunk_id", "bad"), ("page_number", -1), ("text", ""),
                                        ("source_file", "x\nheader"), ("schema_version", 2)])
def test_invalid_payload_fails_closed_without_leaking_text(field, value):
    item = candidate()
    item[2].payload[field] = value
    with pytest.raises(VectorStoreError, match="^Invalid vector payload$"):
        retrieve([item])


def test_completed_attempt_with_unconfirmed_write_is_rejected():
    item = candidate()
    item[1].write_uncertain = True
    with pytest.raises(VectorCompatibilityError):
        retrieve([item])


def test_exhausted_candidate_budget_is_not_reported_as_no_evidence():
    with pytest.raises(VectorStoreError, match="Candidate search limit"):
        retrieve([candidate("writing") for _ in range(10)])
