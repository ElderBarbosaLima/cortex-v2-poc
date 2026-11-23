"""The LiteLLM client (issue 30), hermetic: httpx.MockTransport, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.clients import llm
from app.clients.llm import LiteLlmClient, LlmSettings, LlmUnavailable

CANARY = "sk-canary-0123456789abcdef"
SETTINGS = LlmSettings(
    "https://litellm.test", "anthropic/claude-sonnet-5-5", "azure/text-embedding-ada-002", retries=1
)


def client(handler: Any) -> LiteLlmClient:
    return LiteLlmClient(SETTINGS, CANARY, transport=httpx.MockTransport(handler))


def test_chat_sends_the_model_and_the_key_and_reads_the_json_object() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        text = 'Here it is:\n```json\n{"text_ja": "検品必須", "targets": ["t"]}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    answer = client(handler).chat_json(system="s", user="u")

    assert answer == {"text_ja": "検品必須", "targets": ["t"]}
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["authorization"] == f"Bearer {CANARY}"
    assert (body["model"], body["temperature"]) == ("anthropic/claude-sonnet-5-5", 0)


def test_embeddings_come_back_in_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "azure/text-embedding-ada-002"
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )

    assert client(handler).embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_a_refusal_is_not_retried_and_a_server_error_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    calls = {"401": 0, "503": 0}

    def refuse(_r: httpx.Request) -> httpx.Response:
        calls["401"] += 1
        return httpx.Response(401, json={"error": "auth"})

    def down(_r: httpx.Request) -> httpx.Response:
        calls["503"] += 1
        return httpx.Response(503)

    with pytest.raises(LlmUnavailable, match="HTTP 401"):
        client(refuse).embed(["x"])
    with pytest.raises(LlmUnavailable, match="HTTP 503"):
        client(down).embed(["x"])
    assert calls == {"401": 1, "503": 2}


@pytest.mark.parametrize(
    "content", ["no json here", '{"broken": ', ""], ids=["prose", "truncated", "empty"]
)
def test_an_answer_without_a_json_object_is_unavailable(content: str) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with pytest.raises(LlmUnavailable):
        client(handler).chat_json(system="s", user="u")


def test_the_key_never_shows_in_errors_repr_or_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    c = client(boom)
    with pytest.raises(LlmUnavailable) as failure:
        c.chat_json(system="s", user="u")

    assert CANARY not in str(failure.value) and CANARY not in repr(failure.value)
    assert CANARY not in repr(c)
    out = capsys.readouterr()
    assert CANARY not in out.out + out.err


def test_no_key_means_no_client() -> None:
    with pytest.raises(LlmUnavailable):
        LiteLlmClient(SETTINGS, "")


class _Cfg:
    litellm_base_url = "https://litellm.test"
    litellm_chat_model = "m"
    litellm_embedding_model = "e"
    litellm_key_secret = "cortex-poc/litellm-api-key"

    def __init__(self, rules: bool, embeddings: bool) -> None:
        self.llm_rule_suggestion = rules
        self.llm_embeddings = embeddings


class _Secrets:
    def __init__(self, value: str | None) -> None:
        self.value, self.asked = value, 0

    def get_secret_value(self, SecretId: str) -> dict[str, str]:
        self.asked += 1
        if self.value is None:
            raise RuntimeError("ResourceNotFoundException")
        return {"SecretString": self.value}


def test_configure_reads_the_key_only_for_a_use_that_is_on() -> None:
    off, missing, on = _Secrets("k"), _Secrets(None), _Secrets("k")
    try:
        llm.configure(_Cfg(False, False), off)
        assert (off.asked, llm.rule_suggester(), llm.embedder()) == (0, None, None)

        llm.configure(_Cfg(True, True), missing)  # no secret value: AI stays off, no failure
        assert (llm.rule_suggester(), llm.embedder()) == (None, None)

        llm.configure(_Cfg(True, False), on)
        assert llm.rule_suggester() is not None and llm.embedder() is None
    finally:
        llm.install_llm(None)
