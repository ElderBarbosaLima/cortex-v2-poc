"""Every use of a model in the PoC goes through LiteLLM (issue 30).

`LiteLlmClient` speaks LiteLLM's OpenAI-compatible API (`/v1/chat/completions`,
`/v1/embeddings`) with httpx, TLS verified. It fails closed: no key, a timeout
or a bad answer raises `LlmUnavailable`, and the caller falls back to the
deterministic path; it never invents an answer. The API key lives only in this
object (read from Secrets Manager at startup) and never appears in an error,
a log line or a repr.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
import structlog

_logger = structlog.get_logger("org-brain.llm")


class LlmUnavailable(RuntimeError):
    """No answer that can be used: off, unreachable, refused or malformed."""


class LlmClient(Protocol):
    @property
    def chat_model(self) -> str: ...

    def chat_json(self, *, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    chat_model: str
    embedding_model: str
    timeout_s: float = 60.0
    retries: int = 2


class LiteLlmClient:
    def __init__(
        self, settings: LlmSettings, api_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not api_key:
            raise LlmUnavailable("no LiteLLM API key")
        self._settings = settings
        self._http = httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            headers={"authorization": f"Bearer {api_key}"},
            timeout=settings.timeout_s,
            transport=transport,
        )

    def __repr__(self) -> str:
        s = self._settings
        return f"LiteLlmClient(base_url={s.base_url!r}, chat={s.chat_model!r})"

    @property
    def chat_model(self) -> str:
        return self._settings.chat_model

    @property
    def embedding_model(self) -> str:
        return self._settings.embedding_model

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last = "no attempt"
        for attempt in range(self._settings.retries + 1):
            try:
                response = self._http.post(path, json=payload)
            except httpx.HTTPError as exc:
                last = type(exc).__name__  # never the request (it carries the key)
            else:
                if response.status_code == 200:
                    try:
                        data: dict[str, Any] = response.json()
                    except ValueError:
                        raise LlmUnavailable(f"{path}: the answer is not JSON") from None
                    return data
                last = f"HTTP {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break  # a refusal (auth, bad request) will not change on retry
            _logger.warning("llm.retry", path=path, attempt=attempt + 1, error=last)
            time.sleep(0.5 * (attempt + 1))
        raise LlmUnavailable(f"{path}: {last}")

    def chat_json(self, *, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        data = self._post(
            "/v1/chat/completions",
            {
                "model": self._settings.chat_model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            text = str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            raise LlmUnavailable("chat: no message in the answer") from None
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise LlmUnavailable("chat: the answer holds no JSON object")
        try:
            parsed: dict[str, Any] = json.loads(text[start : end + 1])
        except ValueError:
            raise LlmUnavailable("chat: the JSON in the answer does not parse") from None
        return parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post(
            "/v1/embeddings", {"model": self._settings.embedding_model, "input": list(texts)}
        )
        try:
            rows = sorted(data["data"], key=lambda r: int(r["index"]))
            vectors = [[float(x) for x in r["embedding"]] for r in rows]
        except (KeyError, TypeError, ValueError):
            raise LlmUnavailable("embeddings: malformed answer") from None
        if len(vectors) != len(texts):
            raise LlmUnavailable("embeddings: one vector per text expected")
        return vectors


@dataclass
class _Installed:
    client: LlmClient | None = None
    rule_suggestion: bool = False
    embeddings: bool = False


_installed = _Installed()


def install_llm(
    client: LlmClient | None, *, rule_suggestion: bool = False, embeddings: bool = False
) -> None:
    """Which model uses this process has on. A use is on only with a client."""
    _installed.client = client
    _installed.rule_suggestion = bool(client) and rule_suggestion
    _installed.embeddings = bool(client) and embeddings


def rule_suggester() -> LlmClient | None:
    return _installed.client if _installed.rule_suggestion else None


def embedder() -> LlmClient | None:
    return _installed.client if _installed.embeddings else None


def configure(cfg: Any, sm: Any | None = None) -> None:
    """At startup: read the key from Secrets Manager only if a use is switched on."""
    if not (cfg.llm_rule_suggestion or cfg.llm_embeddings):
        install_llm(None)
        return
    if sm is None:
        import boto3  # pyright: ignore[reportMissingTypeStubs]

        sm = cast(Any, boto3).client("secretsmanager")
    settings = LlmSettings(
        cfg.litellm_base_url, cfg.litellm_chat_model, cfg.litellm_embedding_model
    )
    client = from_secrets_manager(settings, cfg.litellm_key_secret, sm)
    install_llm(client, rule_suggestion=cfg.llm_rule_suggestion, embeddings=cfg.llm_embeddings)
    _logger.info(
        "llm.configured",
        on=bool(client),
        rule_suggestion=_installed.rule_suggestion,
        embeddings=_installed.embeddings,
        chat_model=settings.chat_model if client else None,
    )


def from_secrets_manager(settings: LlmSettings, secret_id: str, sm: Any) -> LiteLlmClient | None:
    """The client when the secret holds a key; None (AI off) when it does not."""
    try:
        key = str(sm.get_secret_value(SecretId=secret_id)["SecretString"]).strip()
    except Exception:
        _logger.info("llm.off", reason="no LiteLLM key in Secrets Manager")
        return None
    return LiteLlmClient(settings, key) if key else None
