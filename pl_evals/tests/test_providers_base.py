import pytest
from runner.providers.base import Provider, ProviderResult, ProviderError


def test_provider_result_has_required_fields():
    r = ProviderResult(
        text="hello",
        model_id="anthropic/claude-sonnet-4-6",
        latency_ms=123,
        input_tokens=10,
        output_tokens=5,
        raw={"id": "test"},
    )
    assert r.text == "hello"
    assert r.latency_ms == 123
    assert r.input_tokens == 10


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


def test_provider_error_is_exception():
    e = ProviderError("rate limited", retryable=True)
    assert isinstance(e, Exception)
    assert e.retryable is True
