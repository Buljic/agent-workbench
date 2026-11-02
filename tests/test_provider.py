import pytest

from workbench.agents import LlamaCppProvider, ProviderError, StubProvider, make_provider


def test_stub_returns_script_in_order_and_records_calls():
    provider = StubProvider(["first", "second"])
    messages = [{"role": "user", "content": "hi"}]

    assert provider.complete(messages, max_tokens=50) == "first"
    assert provider.complete(messages) == "second"

    assert len(provider.calls) == 2
    assert provider.calls[0]["messages"] == messages
    assert provider.calls[0]["max_tokens"] == 50
    assert provider.calls[1]["max_tokens"] == 400


def test_stub_exhausted_script_raises():
    provider = StubProvider([])
    with pytest.raises(ProviderError, match="script exhausted"):
        provider.complete([])


def test_make_provider_stub():
    provider = make_provider("stub", script=["ok"])
    assert isinstance(provider, StubProvider)
    assert provider.complete([]) == "ok"


def test_make_provider_stub_requires_script():
    with pytest.raises(ValueError, match="script"):
        make_provider("stub")


def test_make_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider: nope"):
        make_provider("nope")


def test_make_provider_llama_cpp_constructed_without_network():
    provider = make_provider("llama_cpp", base_url="http://127.0.0.1:9", model="local")
    assert isinstance(provider, LlamaCppProvider)
    assert provider.base_url == "http://127.0.0.1:9"


def test_make_provider_llama_cpp_requires_model():
    with pytest.raises(ValueError, match="model"):
        make_provider("llama_cpp", base_url="http://127.0.0.1:9")


def test_openai_alias_builds_the_compatible_client():
    provider = make_provider("openai", base_url="http://127.0.0.1:9", model="m")
    assert isinstance(provider, LlamaCppProvider)


def test_openai_alias_requires_endpoint_and_model():
    with pytest.raises(ValueError):
        make_provider("openai", model="m")


def test_stub_records_the_guided_schema_argument():
    provider = StubProvider(["{\"final\": \"ok\"}"])
    provider.complete([{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    assert provider.calls[0]["json_schema"] == {"type": "object"}
