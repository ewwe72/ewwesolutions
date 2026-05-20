import pytest
import respx
import httpx
from runner.providers.groq import GroqProvider
from runner.providers.base import ProviderError


@pytest.mark.asyncio
@respx.mock
async def test_groq_happy_path():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "grq-1",
                "model": "llama-3.3-70b-versatile",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1},
            },
        )
    )
    p = GroqProvider(api_key="test")
    r = await p.complete("hi", "llama-3.3-70b-versatile")
    assert r.text == "ok"
    assert r.input_tokens == 4


@pytest.mark.asyncio
@respx.mock
async def test_groq_rate_limit_retryable():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate"})
    )
    with pytest.raises(ProviderError) as exc:
        await GroqProvider(api_key="t").complete("x", "m")
    assert exc.value.retryable is True
