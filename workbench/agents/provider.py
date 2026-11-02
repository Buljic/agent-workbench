from typing import Protocol

import httpx


class ProviderError(Exception):
    pass


class Provider(Protocol):
    def complete(
        self, messages: list[dict], max_tokens: int = 400, json_schema: dict | None = None
    ) -> str: ...


class StubProvider:
    # the stub plays the role of a compliant server, so it accepts the schema
    supports_guided_json = True

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def complete(
        self, messages: list[dict], max_tokens: int = 400, json_schema: dict | None = None
    ) -> str:
        self.calls.append({"messages": messages, "max_tokens": max_tokens, "json_schema": json_schema})
        if not self._script:
            raise ProviderError("script exhausted")
        return self._script.pop(0)


class LlamaCppProvider:
    def __init__(self, base_url: str, model: str, timeout_s: float = 120.0,
                 guided: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        # only send guided_json when the caller opted in; older committed runs
        # used unguided requests, so this stays off by default
        self.supports_guided_json = guided

    def complete(
        self, messages: list[dict], max_tokens: int = 400, json_schema: dict | None = None
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if json_schema is not None and self.supports_guided_json:
            payload["guided_json"] = json_schema
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
    if name in ("llama_cpp", "openai"):
        # both speak the OpenAI chat completions protocol; the name only records
        # which server ran the model
        missing = [key for key in ("base_url", "model") if key not in kwargs]
        if missing:
            raise ValueError(f"{name} provider requires {', '.join(missing)}")
        return LlamaCppProvider(**kwargs)
    raise ValueError(f"unknown provider: {name}")
