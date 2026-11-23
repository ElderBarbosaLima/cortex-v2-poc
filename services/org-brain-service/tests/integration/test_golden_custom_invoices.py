"""Golden set for custom_invoices (issue 25): 100% through the real API, with
real OPA and Postgres, including scenarios 2, 3 and 4 and the learned rule."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from fastapi.testclient import TestClient

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine
from app.domain_packs.base import register_pack
from app.domain_packs.custom_invoices import PACK
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.seed.rules import load_pack_rules
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate
from tests.domain_packs.test_custom_invoices_pack import ideal_output
from tests.golden.harness import AGENT_KEY, DESK_KEY, GoldenRun

pytestmark = pytest.mark.integration


def test_the_custom_invoices_golden_set_passes_in_full(capsys: pytest.CaptureFixture[str]) -> None:
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
        run = GoldenRun("custom_invoices", Path(cfg.seed_dir), api, seed, ideal_output).run()
    finally:
        app_engine().dispose()

    with capsys.disabled():
        print(f"\nseed: {[(x.pack, x.added, x.present) for x in loaded]}\n{run.summary()}")
    assert run.mismatches == []
    assert run.checked["packs"] == 40 and run.checked["corrections"] == 3
