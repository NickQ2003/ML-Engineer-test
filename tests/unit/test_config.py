from app.core.config import Settings


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://test-qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "test_documents")

    settings = Settings()

    assert settings.qdrant_url == "http://test-qdrant:6333"
    assert settings.qdrant_collection == "test_documents"
