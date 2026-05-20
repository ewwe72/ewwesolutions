import pytest
import respx
import httpx
from runner.providers.openrouter import OpenRouterProvider
from runner.providers.base import ProviderError


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_happy_path():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-abc",
                "model": "anthropic/claude-sonnet-4-6",
                "choices": [{"message": {"role": "assistant", "content": "{\"k\": 1}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
        )
    )
    p = OpenRouterProvider(api_key="test-key")
    r = await p.complete("prompt text", "anthropic/claude-sonnet-4-6")
    assert r.text == '{"k": 1}'
    assert r.model_id == "anthropic/claude-sonnet-4-6"
    assert r.input_tokens == 12
    assert r.output_tokens == 6
    assert r.latency_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_rate_limit_is_retryable():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    p = OpenRouterProvider(api_key="test-key")
    with pytest.raises(ProviderError) as exc:
        await p.complete("x", "model-id")
    assert exc.value.retryable is True


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_400_is_fatal():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    p = OpenRouterProvider(api_key="test-key")
    with pytest.raises(ProviderError) as exc:
        await p.complete("x", "model-id")
    assert exc.value.retryable is False
