from typing import Protocol

import httpx


class ProviderError(Exception):
    pass


class Provider(Protocol):
    def complete(self, messages: list[dict], max_tokens: int = 400) -> str: ...


class StubProvider:
    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], max_tokens: int = 400) -> str:
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if not self._script:
            raise ProviderError("script exhausted")
        return self._script.pop(0)


class LlamaCppProvider:
    def __init__(self, base_url: str, model: str, timeout_s: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def complete(self, messages: list[dict], max_tokens: int = 400) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        # the only network call in the agent package
        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions", json=payload, timeout=self.timeout_s
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ProviderError(f"llama.cpp transport error: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"llama.cpp returned HTTP {response.status_code}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("llama.cpp response has no message content") from exc
        if not isinstance(content, str):
            raise ProviderError("llama.cpp response has no message content")
        return content.strip()


def make_provider(name: str, **kwargs) -> Provider:
    if name == "stub":
        if "script" not in kwargs:
            raise ValueError("stub provider requires script")
        return StubProvider(kwargs["script"])
    if name == "llama_cpp":
        missing = [key for key in ("base_url", "model") if key not in kwargs]
        if missing:
            raise ValueError(f"llama_cpp provider requires {', '.join(missing)}")
        return LlamaCppProvider(**kwargs)
    raise ValueError(f"unknown provider: {name}")
