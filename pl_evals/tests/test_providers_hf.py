import pytest
import respx
import httpx
from runner.providers.hf_inference import HFInferenceProvider
from runner.providers.base import ProviderError


@pytest.mark.asyncio
@respx.mock
async def test_hf_happy_path():
    respx.post("https://api-inference.huggingface.co/models/speakleash/Bielik-11B-v2.3-Instruct").mock(
        return_value=httpx.Response(200, json=[{"generated_text": "wynik testu"}]),
    )
    p = HFInferenceProvider(api_key="test")
    r = await p.complete("test", "speakleash/Bielik-11B-v2.3-Instruct")
    assert r.text == "wynik testu"
    assert r.model_id == "speakleash/Bielik-11B-v2.3-Instruct"
    assert r.input_tokens == 0
    assert r.output_tokens == 0


@pytest.mark.asyncio
@respx.mock
async def test_hf_cold_start_503_is_retryable():
    respx.post("https://api-inference.huggingface.co/models/m").mock(
        return_value=httpx.Response(503, json={"error": "Model loading", "estimated_time": 20})
    )
    with pytest.raises(ProviderError) as exc:
        await HFInferenceProvider(api_key="t").complete("x", "m")
    assert exc.value.retryable is True
