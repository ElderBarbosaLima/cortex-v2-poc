"""OPA client and policy gate (hermetic: httpx.MockTransport). Fail closed."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.clients.opa import AccessGateUnavailable, OpaClient
from app.seed.tenants import Seed, TenantSeed
from app.services.policy_gate import PolicyGate

ALLOW = {
    "allow": True,
    "reason": "Allowed by cortex_consumer at jp",
    "policy_revision": "sha256:abc",
    "readable_scopes": ["n1", "n2"],
    "sensitivity_ceiling": "confidential",
}


def client(handler: Any) -> OpaClient:
    return OpaClient("http://opa:8181", transport=httpx.MockTransport(handler))


def test_an_allow_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/data/cortex/policy/authz/decision"
        assert json.loads(request.content)["input"] == {"tenant_id": "t"}
        return httpx.Response(200, json={"result": ALLOW})

    decision = client(handler).read_decision({"tenant_id": "t"})

    assert decision.allow
    assert decision.policy_revision == "sha256:abc"
    assert decision.readable_scopes == ["n1", "n2"]
    assert decision.sensitivity_ceiling == "confidential"


def test_a_denial_is_an_answer_not_an_error() -> None:
    deny = {"allow": False, "reason": "purpose not registered", "policy_revision": "sha256:abc"}

    decision = client(lambda r: httpx.Response(200, json={"result": deny})).read_decision({})

    assert not decision.allow
    assert decision.reason == "purpose not registered"


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda r: httpx.Response(500, text="boom"), id="http-500"),
        pytest.param(lambda r: httpx.Response(200, json={}), id="no-result"),
        pytest.param(
            lambda r: httpx.Response(200, json={"result": {"reason": "x"}}),
            id="result-without-allow",
        ),
        pytest.param(
            lambda r: httpx.Response(200, json={"result": {"allow": "yes"}}), id="allow-not-bool"
        ),
        pytest.param(lambda r: httpx.Response(200, text="not json"), id="not-json"),
    ],
)
def test_anything_but_a_clear_decision_fails_closed(handler: Any) -> None:
    with pytest.raises(AccessGateUnavailable):
        client(handler).read_decision({})


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_connection_errors_and_timeouts_fail_closed(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(AccessGateUnavailable, match="unavailable"):
        client(handler).approval_decision({})


def test_the_gate_reloads_an_unknown_tenant_once_then_asks_again() -> None:
    calls: list[str] = []
    loaded = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "PUT":
            loaded["done"] = True
            return httpx.Response(204)
        if not loaded["done"]:
            return httpx.Response(
                200, json={"result": {"allow": False, "reason": "unknown tenant"}}
            )
        return httpx.Response(200, json={"result": ALLOW})

    seed = Seed(
        tenants=(TenantSeed("t-jp", "jp", {"nodes": {}}),), consumers={}, node_ids={}, people={}
    )
    decision = PolicyGate(client(handler), seed).read({"tenant_id": "t-jp"})

    assert decision.allow
    assert calls == [
        "POST /v1/data/cortex/policy/authz/decision",
        "PUT /v1/data/cortex/tenants/t-jp",
        "POST /v1/data/cortex/policy/authz/decision",
    ]


def test_the_gate_does_not_reload_for_a_tenant_it_does_not_hold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json={"result": {"allow": False, "reason": "unknown tenant"}})

    seed = Seed(tenants=(), consumers={}, node_ids={}, people={})

    assert not PolicyGate(client(handler), seed).read({"tenant_id": "t-x"}).allow
