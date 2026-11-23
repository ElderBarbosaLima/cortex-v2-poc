"""`make seed`: register the seed's tenants, (re)load their data into OPA and
load each domain pack's initial rules into the ledger (idempotent).

    python -m app.cli.seed

The service registers tenants and loads OPA at startup too; rules are loaded
only here.
"""

from __future__ import annotations

from pathlib import Path

from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.domain_packs import discover as discover_packs
from app.keys import install as install_keys
from app.main import register_tenants
from app.seed.rules import load_pack_rules
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate


def main() -> None:
    cfg = OrgBrainConfig()
    seed = load_seed(Path(cfg.seed_dir))
    register_tenants(seed)
    install_keys(t.tenant_id for t in seed.tenants)
    PolicyGate(OpaClient(cfg.opa_url, timeout_s=cfg.opa_timeout_s), seed).load_all()
    for tenant in seed.tenants:
        grants = len(tenant.opa_data["grants"])
        nodes = len(tenant.opa_data["nodes"])
        print(f"tenant {tenant.key} ({tenant.tenant_id}): {nodes} nodes, {grants} grants → OPA")
    print(f"consumers: {', '.join(sorted(seed.consumers))}")
    discover_packs()
    for loaded in load_pack_rules(seed, Path(cfg.seed_dir)):
        print(f"pack {loaded.pack}: {loaded.added} rules added, {loaded.present} already present")


if __name__ == "__main__":
    main()
