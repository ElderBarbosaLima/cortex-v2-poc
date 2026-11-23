"""A LiteLLM stand-in for tests: canned chat answers, deterministic embeddings."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from app.clients.llm import LlmUnavailable


class FakeLlm:
    chat_model = "fake/sonnet"

    def __init__(
        self,
        answer: dict[str, Any] | Callable[[str], dict[str, Any]] | None = None,
        topic: Callable[[str], str] | None = None,
    ) -> None:
        self.answer = answer
        self.topic = topic or (lambda text: text)
        self.chats: list[str] = []

    def chat_json(self, *, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        self.chats.append(user)
        if self.answer is None:
            raise LlmUnavailable("fake: down")
        return self.answer(user) if callable(self.answer) else dict(self.answer)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Same topic → same unit vector (16 dims), so meaning-only matches are testable."""
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(self.topic(text).encode()).digest()
            vector = [float(b) for b in digest[:16]]
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            out.append([v / norm for v in vector])
        return out
