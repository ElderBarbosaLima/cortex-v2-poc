"""Golden set for cash_allocation (issues 40–41): IBM's workflow through the
real API. Every payment, sent in IBM's shape by IBM's consumer, gets exactly
the expected rules, each with both layers: the engine fields and the guidance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from fastapi.testclient import TestClient

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine
from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.seed.rules import load_pack_rules
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate

pytestmark = pytest.mark.integration
IBM, AGENT = "it-alloc-ibm", "it-alloc-agent"


def test_ibms_payments_get_exactly_their_rules_with_both_layers() -> None:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    data = Path(cfg.seed_dir) / "cash_allocation"
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        loaded = {x.pack: x for x in load_pack_rules(seed, Path(cfg.seed_dir))}
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
    api = TestClient(
        create_app(
            services=AppServices(
                seed=seed,
                consumer_keys=ConsumerKeyStore(
                    {
                        "ibm-pr-agent": hashlib.sha256(IBM.encode()).hexdigest(),
                        "askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest(),
                    }
                ),
                policy_gate=gate,
            )
        )
    )
    cases = [json.loads(line) for line in (data / "cases.jsonl").read_text().splitlines()]
    expected = {e["case_id"]: e for e in json.loads((data / "expected.json").read_text())}
    keys = {r["record_id"]: r["key"] for r in json.loads((data / "rules.json").read_text())}
    scope = seed.node_ids["jp/finance/q2c-cash-application"]

    mismatches: list[str] = []
    fee_rule: dict[str, Any] | None = None
    try:
        for case in cases:
            exp = expected[case["case_id"]]
            with allow_network():
                resp = api.post(
                    "/v1/cortex/context-packs",
                    headers={"Authorization": f"Bearer {IBM}"},
                    json={
                        "domain": "cash_allocation",
                        "case": case,
                        "scope": scope,
                        "purpose": "payment_reconciliation",
                        "as_of": exp["as_of"],
                    },
                )
            if resp.status_code != 201:
                mismatches.append(f"{case['case_id']}: {resp.status_code} {resp.text[:200]}")
                continue
            rules = [r for r in resp.json()["records"] if r["kind"] == "rule"]
            kept = sorted(
                keys.get(r["record_id"], r["record_id"]) for r in rules if r["role"] != "overridden"
            )
            if kept != exp["expected_rules"]:
                mismatches.append(f"{case['case_id']}: {kept} != {exp['expected_rules']}")
            if case["case_id"] == "PAY-A001":
                fee_rule = next(r for r in rules if keys.get(r["record_id"]) == "AL-C-0101-FEE")
        # A rule the gate refuses, over HTTP: the field path tells what to fix.
        with allow_network():
            bad = api.post(
                "/v1/cortex/records",
                headers={"Authorization": f"Bearer {AGENT}", "X-End-User-Id": seed.people["R-101"]},
                json={
                    "domain": "cash_allocation",
                    "kind": "rule",
                    "schema_version": "cash_allocation.rule/1",
                    "body": {
                        "text_ja": "手数料 990 円。",
                        "targets": ["allocation.bank_fee"],
                        "condition": 'payment.customer_id == "CUST-0101"',
                        "exception": "bank_fee",
                        "engine": {"expected_fee": 990},
                    },
                    "scope": scope,
                    "entities": ["customer:CUST-0101"],
                    "enforcement": "default",
                    "authority": 10,
                    "sensitivity": {"class": "internal"},
                    "purpose": "cash_matching",
                    "valid_from": "2026-11-01",
                },
            )
    finally:
        app_engine().dispose()

    assert loaded["cash_allocation"].added + loaded["cash_allocation"].present == 6
    assert mismatches == []
    assert fee_rule is not None
    assert fee_rule["body"]["engine"] == {
        "expected_fee": 660,
        "max_fee": 880,
        "treatment": "fee_expense",
    }
    assert fee_rule["body"]["text_ja"].startswith("山田商事は振込手数料を差し引いて支払う")
    assert bad.status_code == 422
    assert "engine.expected_fee" in json.dumps(bad.json()["detail"], ensure_ascii=False)
