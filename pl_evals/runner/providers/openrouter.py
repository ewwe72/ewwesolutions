from __future__ import annotations
import time
import httpx
from .base import Provider, ProviderResult, ProviderError

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(Provider):
    def __init__(self, api_key: str, *, timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_s)

    async def complete(self, prompt: str, model_id: str) -> ProviderResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://leaderboard.ewwesolutions.work",
            "X-Title": "pl_evals",
        }
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{BASE_URL}/chat/completions", headers=headers, json=body)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderError(f"openrouter {resp.status_code}: {resp.text[:200]}", retryable=True)
        if resp.status_code >= 400:
            raise ProviderError(f"openrouter {resp.status_code}: {resp.text[:200]}", retryable=False)

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ProviderResult(
            text=text,
            model_id=data.get("model", model_id),
            latency_ms=latency_ms,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )
