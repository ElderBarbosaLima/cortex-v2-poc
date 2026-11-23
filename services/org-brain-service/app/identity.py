"""Consumer identity (PoC stand-in for OIDC, D1; docs/discovery.md P3).

`Authorization: Bearer <static API key>` → sha256 → one consumer, which belongs
to one tenant. The tenant always comes from that identity, never from the URL,
query or body (doc 12). `X-End-User-Id` names the person behind the call (a
UUID), when there is one; OPA must allow both. Only key hashes are held, loaded
from Secrets Manager at startup; raw keys exist only in the caller's hands.

Shape follows integrations-service's `AuthedCaller` and `require_scope`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable, Generator
from typing import Any, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]
from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.errors import CortexApiError
from app.db import tenant_session
from app.seed.tenants import ConsumerInfo


class AuthedCaller(BaseModel):
    consumer_id: str
    tenant_id: str
    end_user_id: str | None = None
    scopes: list[str] = []


class ConsumerKeyStore:
    """sha256(api key) -> consumer id, compared in constant time."""

    def __init__(self, hashes: dict[str, str]) -> None:
        self._hashes = dict(hashes)

    @classmethod
    def from_secrets_manager(cls, secret_id: str, client: Any | None = None) -> ConsumerKeyStore:
        sm: Any = client if client is not None else cast(Any, boto3).client("secretsmanager")
        response: Any = sm.get_secret_value(SecretId=secret_id)
        payload: dict[str, Any] = json.loads(str(response["SecretString"]))
        return cls({consumer: str(entry["sha256"]) for consumer, entry in payload.items()})

    def consumer_for(self, api_key: str) -> str | None:
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        found = None
        for consumer, expected in self._hashes.items():
            if hmac.compare_digest(digest, expected):
                found = consumer
        return found


def _unauthorized(detail: str) -> CortexApiError:
    return CortexApiError(401, "unauthorized", detail)


def get_caller(request: Request) -> AuthedCaller:
    keys: ConsumerKeyStore = request.app.state.consumer_keys
    consumers: dict[str, ConsumerInfo] = request.app.state.consumers
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing bearer API key")
    consumer_id = keys.consumer_for(token.strip())
    info = consumers.get(consumer_id or "")
    if info is None:
        raise _unauthorized("unknown API key")
    end_user = request.headers.get("X-End-User-Id")
    if end_user is not None:
        try:
            end_user = str(uuid.UUID(end_user))
        except ValueError:
            raise CortexApiError(400, "invalid_end_user", "X-End-User-Id must be a UUID") from None
    return AuthedCaller(
        consumer_id=info.consumer_id,
        tenant_id=info.tenant_id,
        end_user_id=end_user,
        scopes=list(info.scopes),
    )


# The Phase 1 integration profile (issue 36): a consumer holding only this
# scope may get a context pack (and read a served one back), nothing else.
PACK_SCOPE = "cortex:pack"


def require_scope(*scopes: str) -> Callable[[AuthedCaller], AuthedCaller]:
    """The caller must hold at least one of `scopes`."""

    def dependency(caller: AuthedCaller = Depends(get_caller)) -> AuthedCaller:
        if not set(scopes) & set(caller.scopes):
            raise CortexApiError(403, "insufficient_scope", " or ".join(scopes))
        return caller

    return dependency


def caller_session(caller: AuthedCaller = Depends(get_caller)) -> Generator[Session]:
    """A database session bound to the caller's tenant (RLS) for the request."""
    with tenant_session(caller.tenant_id) as session:
        yield session
