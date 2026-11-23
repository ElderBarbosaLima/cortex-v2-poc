"""Spotting patterns (issue 46): a history of manual resolutions becomes rule
suggestions once a customer's same fix reaches the domain's threshold (IBM:
"same fix 6 times"); below it, nothing; running it again proposes nothing new.
The live threshold per domain also governs the outcome loop."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network

from alembic import command
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.main import register_tenants
from app.seed.tenants import load_seed
from app.services import ledger, patterns
from app.services.writeback import RuleSuggestionPolicy, policies_by_domain

pytestmark = pytest.mark.integration


def test_a_repeated_fix_becomes_one_suggestion_and_only_once() -> None:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    seed = load_seed(Path(cfg.seed_dir))
    tenant = seed.consumers["askq2c-agent"].tenant_id
    history = [
        json.loads(x)
        for x in (Path(cfg.seed_dir) / "cash_allocation" / "history.jsonl").read_text().splitlines()
    ]
    policy = policies_by_domain(cfg.rule_suggestion_by_domain)["cash_allocation"]
    now = dt.datetime(2026, 11, 23, 23, 59, tzinfo=dt.UTC)

    def run() -> list[patterns.Found]:
        with allow_network(), tenant_session(tenant) as s:
            return patterns.mine(
                s,
                tenant_id=tenant,
                pack=PACK,
                scope=seed.node_ids["jp/finance/q2c-cash-application"],
                history=history,
                policy=policy,
                now=now,
            )

    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        register_tenants(seed)
    try:
        first = {f.entities: f for f in run()}
        again = {f.entities: f for f in run()}
        with allow_network(), tenant_session(tenant) as s:
            proposed = ledger.latest(s, str(first[("customer:CUST-0105",)].record_id))
            body = ledger.open_body(s, proposed)
        with allow_network(), tenant_session(tenant) as s:
            outside = patterns.mine(
                s,
                tenant_id=tenant,
                pack=PACK,
                scope=seed.node_ids["jp/finance/q2c-cash-application"],
                history=history,
                policy=policy,
                now=now + dt.timedelta(days=200),
            )
    finally:
        app_engine().dispose()

    assert (policy.min_distinct_cases, policy.window_days) == (6, 90)
    fee = first[("customer:CUST-0105",)]
    assert (fee.cases, fee.outcome) == (7, "proposed")
    assert first[("customer:CUST-0102",)].outcome == "below_threshold"
    assert (proposed.status, proposed.asserted_by, proposed.authority) == (
        "proposed",
        {"type": "policy", "id": "pattern-mining"},
        10,
    )
    assert body["engine"] == {
        "expected_fee": 330,
        "max_fee": 880,
        "treatment": "fee_expense",
    }  # the typical gap
    assert proposed.source["pattern"] == {"cases": 7, "window_days": 90, "threshold": 6}
    assert again[("customer:CUST-0105",)].outcome == "already_waiting"
    assert again[("customer:CUST-0105",)].record_id == fee.record_id
    assert outside == []  # history older than the window counts for nothing


def test_the_threshold_is_per_domain() -> None:
    by_domain = policies_by_domain('{"cash_allocation": "6/1/90"}')
    assert by_domain["cash_allocation"] == RuleSuggestionPolicy(6, 1, 90)
    assert "custom_invoices" not in by_domain  # the default (1/1/30) stays for the others
