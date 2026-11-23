"""The /v1/cortex contract (D15): one generated spec, MCP tools marked, no
numeric keys on the wire, one error shape."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app
from app.routers.cortex import MCP_TOOLS
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate
from scripts.export_openapi import build_spec, render

CONTRACT = Path(os.environ.get("CORTEX_CONTRACT_DIR", "/libraries/cortex-contract"))
SEED_DIR = Path(os.environ.get("CORTEX_SYNTHETIC_DIR", "/data/synthetic"))
KEY = "contract-test-key"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return build_spec()


@pytest.fixture
def api() -> TestClient:
    keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest()})
    services = AppServices(
        seed=load_seed(SEED_DIR), consumer_keys=keys, policy_gate=cast(PolicyGate, None)
    )
    return TestClient(create_app(services=services))


def test_the_committed_spec_matches_the_code(spec: dict[str, Any]) -> None:
    assert (CONTRACT / "openapi.json").read_text(encoding="utf-8") == render(spec), (
        "run make contract"
    )


def test_the_mcp_tool_list_is_fixed(spec: dict[str, Any]) -> None:
    tools = {
        op["x-mcp-tool"]["name"]: op["operationId"]
        for path in spec["paths"].values()
        for op in path.values()
        if "x-mcp-tool" in op
    }

    assert tools == {name: name for name in MCP_TOOLS}
    assert set(MCP_TOOLS) == {
        "request_context_pack",
        "verify_output",
        "submit_correction",
        "report_outcome",  # issue 43: IBM's third operation
    }


def _properties(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for name, schema in spec["components"]["schemas"].items():
        for prop, definition in schema.get("properties", {}).items():
            out.append((name, prop, definition))
    for path in spec["paths"].values():
        for op in path.values():
            for param in op.get("parameters", []):
                out.append((op["operationId"], param["name"], param["schema"]))
    return out


def _types(definition: dict[str, Any]) -> set[str]:
    found = {str(definition["type"])} if "type" in definition else set()
    for option in definition.get("anyOf", []):
        found |= _types(option)
    return found


def test_no_numeric_identifier_crosses_the_api(spec: dict[str, Any]) -> None:
    """House rule: UUIDs at the API boundary, numeric keys never leave the service."""
    offenders = [
        f"{owner}.{prop}"
        for owner, prop, definition in _properties(spec)
        if (prop == "id" or prop.endswith("_id") or prop == "cursor")
        and _types(definition) & {"integer", "number"}
    ]

    assert offenders == []
    uuid_ids = [p for _, p, d in _properties(spec) if p.endswith("record_id") or p == "pack_id"]
    assert uuid_ids and all(p for p in uuid_ids)


def test_record_ids_are_uuids_on_the_wire(spec: dict[str, Any]) -> None:
    for owner, prop, definition in _properties(spec):
        if prop in ("record_id", "pack_id", "tenant_id", "scope"):
            formats = {definition.get("format")} | {
                o.get("format") for o in definition.get("anyOf", [])
            }
            assert "uuid" in formats, f"{owner}.{prop}"


@pytest.mark.parametrize(
    ("method", "path", "headers", "status", "error"),
    [
        ("get", "/v1/cortex/whoami", {}, 401, "unauthorized"),
        ("get", "/v1/cortex/nope", {}, 404, "not_found"),
        (
            "get",
            "/v1/cortex/records?purpose=x&scope=not-a-uuid",
            {"Authorization": f"Bearer {KEY}"},
            422,
            "invalid_request",
        ),
        ("post", "/v1/cortex/verify", {"Authorization": f"Bearer {KEY}"}, 422, "invalid_request"),
    ],
)
def test_every_error_has_the_same_shape(
    api: TestClient, method: str, path: str, headers: dict[str, str], status: int, error: str
) -> None:
    resp = getattr(api, method)(path, headers=headers)

    assert resp.status_code == status
    assert resp.json()["error"] == error
    assert "detail" in resp.json()


def test_spec_is_valid_json_with_the_cortex_title(spec: dict[str, Any]) -> None:
    assert json.loads(render(spec))["info"]["title"] == "Cortex API"


def test_the_pack_only_profile_is_documented_on_the_pack_operations(spec: dict[str, Any]) -> None:
    ops = {op["operationId"]: op for path in spec["paths"].values() for op in path.values()}

    for name in ("request_context_pack", "get_context_pack"):
        assert "cortex:pack" in ops[name]["description"], name
