"""Provider protocol tests use in-memory HTTP responses, never a real API."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.answer_generator import GeneratorRateLimitError, GeneratorTimeoutError, HTTPAnswerGenerator, MAX_PROVIDER_BYTES
from app.services.ask_service import AnswerGenerationError, ContextFragment


def settings(provider):
    return Settings(llm_provider=provider, llm_model="test-model", llm_api_key="test-key",
                    llm_allow_external=True)


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_provider_protocol_and_secret_handling(provider):
    async def run():
        seen = []
        def handler(request):
            seen.append(request)
            body = json.loads(request.content)
            encoded = json.dumps(body)
            assert "private question" in encoded and "untrusted context" in encoded
            if provider == "openai":
                assert request.headers["authorization"] == "Bearer test-key"
                assert request.url.host == "api.openai.com"
                data = {"choices": [{"message": {"content": '{"answer":"ok","citations":[{"id":"S1","quote":"untrusted context"}]}'}}]}
            else:
                assert request.headers["x-goog-api-key"] == "test-key"
                assert request.url.host == "generativelanguage.googleapis.com"
                data = {"candidates": [{"content": {"parts": [{"text": '{"answer":"ok","citations":[{"id":"S1","quote":"untrusted context"}]}'}]}}]}
            assert "test-key" not in str(request.url) and "test-key" not in encoded
            return httpx.Response(200, json=data)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        generator = HTTPAnswerGenerator(settings(provider), client=client)
        result = await generator.generate("private question", (ContextFragment("S1", "untrusted context"),))
        assert result.answer == "ok" and result.citations[0].citation_id == "S1"
        assert result.citations[0].quote == "untrusted context" and len(seen) == 1
        await generator.aclose()
        assert client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_external_models_require_explicit_permission_and_key(provider):
    with pytest.raises(ValidationError):
        Settings(llm_provider=provider, llm_model="model", llm_api_key="key")
    with pytest.raises(ValidationError):
        Settings(llm_provider=provider, llm_model="model", llm_allow_external=True)


def test_disabled_needs_no_model_or_key_and_secrets_are_hidden():
    assert Settings().llm_provider == "disabled"
    assert "test-key" not in repr(settings("openai"))
    with pytest.raises(ValidationError) as error:
        Settings(llm_provider="openai", llm_model="model", llm_api_key="secret-key")
    assert "secret-key" not in str(error.value)


def test_local_generation_is_not_a_supported_provider():
    with pytest.raises(ValidationError):
        Settings(llm_provider="local", llm_model="model")


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="sensitive provider response"),
    httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"ok","citations":"S1"}'}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"ok","citations":["S1"]}'}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"ok","citations":[{"id":"S1"}]}'}}]}),
    httpx.Response(200, content=b"x" * (MAX_PROVIDER_BYTES + 1)),
])
def test_bad_provider_response_fails_without_disclosing_content(response):
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response), trust_env=False)
        generator = HTTPAnswerGenerator(settings("openai"), client=client)
        try:
            with pytest.raises(AnswerGenerationError) as error:
                await generator.generate("question", (ContextFragment("S1", "context"),))
            assert "sensitive provider response" not in str(error.value)
        finally:
            await generator.aclose()
    asyncio.run(run())


def test_provider_timeout_is_distinct_and_not_retried():
    calls = []
    def timeout(request):
        calls.append(1)
        raise httpx.ReadTimeout("sensitive details", request=request)
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout), trust_env=False)
        generator = HTTPAnswerGenerator(settings("openai"), client=client)
        try:
            with pytest.raises(GeneratorTimeoutError, match="^Model provider timed out$"):
                await generator.generate("question", (ContextFragment("S1", "context"),))
        finally:
            await generator.aclose()
        assert len(calls) == 1
    asyncio.run(run())


def test_provider_rate_limit_is_distinct_and_not_retried():
    calls = []
    def limited(request):
        calls.append(1)
        return httpx.Response(429, text="private provider detail")
    async def run():
        generator = HTTPAnswerGenerator(settings("openai"), client=httpx.AsyncClient(
            transport=httpx.MockTransport(limited), trust_env=False,
        ))
        try:
            with pytest.raises(GeneratorRateLimitError, match="^Model provider rate or quota limit$"):
                await generator.generate("question", (ContextFragment("S1", "context"),))
        finally:
            await generator.aclose()
        assert calls == [1]
    asyncio.run(run())


def test_gemini_ignores_thought_parts_and_uses_visible_answer():
    async def run():
        response = httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"thought": True, "text": "private thought, not JSON"},
            {"text": '{"answer":"ok","citations":[{"id":"S1","quote":"context"}]}'},
        ]}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response), trust_env=False)
        generator = HTTPAnswerGenerator(settings("gemini"), client=client)
        try:
            result = await generator.generate("question", (ContextFragment("S1", "context"),))
            assert result.answer == "ok" and result.citations[0].quote == "context"
        finally:
            await generator.aclose()
    asyncio.run(run())


def test_gemini_missing_content_logs_only_safe_finish_reason(caplog):
    async def run():
        response = httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]})
        generator = HTTPAnswerGenerator(settings("gemini"), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response), trust_env=False,
        ))
        try:
            with pytest.raises(AnswerGenerationError):
                await generator.generate("sensitive question", (ContextFragment("S1", "private content"),))
        finally:
            await generator.aclose()
    asyncio.run(run())
    assert "reason=MAX_TOKENS" in caplog.text and "stage=provider_content" in caplog.text
    assert "sensitive question" not in caplog.text and "private content" not in caplog.text


def test_gemini_truncated_completion_is_rejected_even_if_json_is_valid():
    async def run():
        response = httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [
            {"text": '{"answer":"ok","citations":[{"id":"S1","quote":"context"}]}'},
        ]}}]})
        generator = HTTPAnswerGenerator(settings("gemini"), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response), trust_env=False,
        ))
        try:
            with pytest.raises(AnswerGenerationError):
                await generator.generate("question", (ContextFragment("S1", "context"),))
        finally:
            await generator.aclose()
    asyncio.run(run())
