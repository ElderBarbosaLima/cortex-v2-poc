"""How a record's body is sealed into `memory_record.body_sealed`.

Every row names its codec (`body_codec`) and its key reference (`key_ref`, the
record's lineage root), so any process holding the codecs can open it:
- `plain-json/1`: no encryption (unit tests, and rows written before issue 14);
- `aes-gcm/1`: AES-256-GCM with the lineage root's data key (app/keys).
`body_hash` is the sha256 of the canonical JSON, the same for every codec, so a
pack can prove which body it served without holding the plaintext.

The codec registry maps a row's `body_codec` to the codec that opens it. The
service, the projector and the CLIs install `aes-gcm/1` at startup and make it
the default for new rows; without that, new rows use `plain-json/1`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from sqlalchemy.orm import Session

Body = dict[str, Any]


class BodyUnreadableError(RuntimeError):
    """The body cannot be opened: tampered, wrong tenant, or its key was erased."""


def canonical(body: Body) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def body_hash(body: Body) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


class BodyCodec(Protocol):
    name: str

    def seal(self, session: Session, *, tenant_id: str, key_ref: str, body: Body) -> bytes: ...

    def open(self, session: Session, *, tenant_id: str, key_ref: str, sealed: bytes) -> Body: ...


class PlainJsonCodec:
    """No encryption: the canonical JSON as bytes."""

    name = "plain-json/1"

    def seal(self, session: Session, *, tenant_id: str, key_ref: str, body: Body) -> bytes:
        return canonical(body)

    def open(self, session: Session, *, tenant_id: str, key_ref: str, sealed: bytes) -> Body:
        loaded: Body = json.loads(sealed)
        return loaded


_CODECS: dict[str, BodyCodec] = {PlainJsonCodec.name: PlainJsonCodec()}
_default: list[BodyCodec] = [_CODECS[PlainJsonCodec.name]]


def install_codec(codec: BodyCodec, *, default: bool = False) -> None:
    _CODECS[codec.name] = codec
    if default:
        _default[0] = codec


def codec_for(name: str) -> BodyCodec:
    try:
        return _CODECS[name]
    except KeyError:
        raise BodyUnreadableError(f"no codec {name!r} installed in this process") from None


def default_codec() -> BodyCodec:
    return _default[0]
