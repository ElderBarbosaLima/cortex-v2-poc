"""Golden set for cash_application (issue 26): 100% through the real API, with
payer resolution and routing checked for every payment."""

from __future__ import annotations

import hashlib
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
from app.domain_packs.cash_application import PACK
from app.domain_packs.cash_application.pack import resolution, route
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.seed.rules import load_pack_rules
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate
from tests.domain_packs.test_cash_application_pack import build_cases, ideal_output
from tests.golden.harness import AGENT_KEY, DESK_KEY, GoldenRun

pytestmark = pytest.mark.integration


def check_resolution_and_route(
    case_id: str, case: dict[str, Any], exp: dict[str, Any], pack: dict[str, Any]
) -> list[str]:
    problems: list[str] = []
    r, want = resolution(case), exp["resolution"]
    if (r.band, r.evidence) != (want["band"], want["evidence"]):
        problems.append(f"resolution {(r.band, r.evidence)} != {(want['band'], want['evidence'])}")
    applied = [
        PACK.rule_body_schema.model_validate(rec["body"])
        for rec in pack["records"]
        if rec["kind"] == "rule" and rec["role"] in ("constraint", "applied")
    ]
    if route(case, applied) != exp["route"]:
        problems.append(f"route {route(case, applied)} != {exp['route']}")
    return problems


def test_the_cash_application_golden_set_passes_in_full(
    capsys: pytest.CaptureFixture[str],
) -> None:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        loaded = load_pack_rules(seed, Path(cfg.seed_dir))
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
    keys = ConsumerKeyStore(
        {
            "askq2c-agent": hashlib.sha256(AGENT_KEY.encode()).hexdigest(),
            "decision-desk": hashlib.sha256(DESK_KEY.encode()).hexdigest(),
        }
    )
    api = TestClient(
        create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
    )
    try:
        run = GoldenRun(
            "cash_application",
            Path(cfg.seed_dir),
            api,
            seed,
            ideal_output,
            build_cases=build_cases,
            extra_check=check_resolution_and_route,
        ).run()
        case = run.cases["PAY-0117"]
        pack_id = run.packs["PAY-0117"]
        auto = {**ideal_output(case), "mode": "auto"}
        held_reply = run._call(  # pyright: ignore[reportPrivateUsage]
            "POST", "/v1/cortex/verify", {"pack_id": pack_id, "case": case, "output": auto}
        )
        passed_reply = run._call(  # pyright: ignore[reportPrivateUsage]
            "POST",
            "/v1/cortex/verify",
            {"pack_id": pack_id, "case": case, "output": ideal_output(case)},
        )
    finally:
        app_engine().dispose()

    with capsys.disabled():
        print(f"\nseed: {[(x.pack, x.added, x.present) for x in loaded]}\n{run.summary()}")
    assert run.mismatches == []
    assert run.checked["packs"] == 40 and run.checked["corrections"] == 3
    # The ambiguous payer: verify refuses an auto-match the rules alone allow.
    held = held_reply.json()
    assert held["result"] == "hold", held
    assert held["reasons"][0]["detail"].startswith("route: this match goes to review (ambiguous")
    assert passed_reply.json()["result"] == "pass"
