import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://test-qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "test_documents")

    settings = Settings()

    assert settings.qdrant_url == "http://test-qdrant:6333"
    assert settings.qdrant_collection == "test_documents"


def test_extraction_settings_defaults_and_environment(monkeypatch):
    defaults = Settings()
    assert defaults.max_request_bytes == 21 * 1024 * 1024
    assert defaults.max_names_bytes == 64 * 1024
    assert defaults.max_names_count == 1000
    assert defaults.fuzzy_threshold == 0.90

    monkeypatch.setenv("MAX_REQUEST_BYTES", "12345")
    monkeypatch.setenv("MAX_NAMES_BYTES", "250")
    monkeypatch.setenv("MAX_NAMES_COUNT", "3")
    monkeypatch.setenv("FUZZY_THRESHOLD", "0.75")
    settings = Settings()
    assert (settings.max_request_bytes, settings.max_names_bytes, settings.max_names_count) == (12345, 250, 3)
    assert settings.fuzzy_threshold == 0.75


@pytest.mark.parametrize("field", ["max_request_bytes", "max_names_bytes", "max_names_count"])
@pytest.mark.parametrize("value", [0, -1, "not-a-number"])
def test_extraction_limits_must_be_positive_integers(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_fuzzy_threshold_accepts_inclusive_bounds(value):
    assert Settings(fuzzy_threshold=value).fuzzy_threshold == value


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), "invalid"])
def test_fuzzy_threshold_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        Settings(fuzzy_threshold=value)
