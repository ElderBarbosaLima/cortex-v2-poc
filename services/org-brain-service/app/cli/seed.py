"""`make seed`: register the seed's tenants and (re)load their data into OPA.

    python -m app.cli.seed

The service does the same at startup; this is for after an OPA restart, or to
check what the seed contains.
"""

from __future__ import annotations

from pathlib import Path

from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.main import register_tenants
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate


def main() -> None:
    cfg = OrgBrainConfig()
    seed = load_seed(Path(cfg.seed_dir))
    register_tenants(seed)
    PolicyGate(OpaClient(cfg.opa_url, timeout_s=cfg.opa_timeout_s), seed).load_all()
    for tenant in seed.tenants:
        grants = len(tenant.opa_data["grants"])
        nodes = len(tenant.opa_data["nodes"])
        print(f"tenant {tenant.key} ({tenant.tenant_id}): {nodes} nodes, {grants} grants → OPA")
    print(f"consumers: {', '.join(sorted(seed.consumers))}")


if __name__ == "__main__":
    main()
