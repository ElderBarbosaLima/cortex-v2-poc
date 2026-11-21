"""How a record's body is sealed into `memory_record.body_sealed`.

Every row names its codec (`body_codec`), so readers always know how to open
it. `plain-json/1` is the placeholder until per-record keys arrive (issue 14,
`aes-gcm/1`); the interface does not change then. `body_hash` is the sha256 of
the canonical JSON, the same for every codec, so a pack can prove which body it
served without holding the plaintext.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

Body = dict[str, Any]


def canonical(body: Body) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def body_hash(body: Body) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


class BodyCodec(Protocol):
    name: str

    def seal(self, *, tenant_id: str, record_id: str, body: Body) -> bytes: ...

    def open(self, *, tenant_id: str, record_id: str, sealed: bytes) -> Body: ...


class PlainJsonCodec:
    """No encryption: the canonical JSON as bytes. Placeholder for issue 14."""

    name = "plain-json/1"

    def seal(self, *, tenant_id: str, record_id: str, body: Body) -> bytes:
        return canonical(body)

    def open(self, *, tenant_id: str, record_id: str, sealed: bytes) -> Body:
        loaded: Body = json.loads(sealed)
        return loaded
