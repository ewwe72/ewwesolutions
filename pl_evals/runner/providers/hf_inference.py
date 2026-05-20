from __future__ import annotations
import time
import httpx
from .base import Provider, ProviderResult, ProviderError

BASE_URL = "https://api-inference.huggingface.co/models"


class HFInferenceProvider(Provider):
    def __init__(self, api_key: str, *, timeout_s: float = 120.0) -> None:
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_s)

    async def complete(self, prompt: str, model_id: str) -> ProviderResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "inputs": prompt,
            "parameters": {"temperature": 0.0, "return_full_text": False},
        }
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{BASE_URL}/{model_id}", headers=headers, json=body)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code in (429, 503) or resp.status_code >= 500:
            raise ProviderError(f"hf {resp.status_code}: {resp.text[:200]}", retryable=True)
        if resp.status_code >= 400:
            raise ProviderError(f"hf {resp.status_code}: {resp.text[:200]}", retryable=False)

        data = resp.json()
        text = data[0]["generated_text"] if isinstance(data, list) and data else ""
        return ProviderResult(
            text=text,
            model_id=model_id,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            raw={"response": data},
        )
