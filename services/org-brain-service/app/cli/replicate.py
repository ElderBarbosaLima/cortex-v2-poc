"""`make replicate`: copy the HQ region's published Global records into this
region's ledger (ADR 0002 option b, issue 32).

    python -m app.cli.replicate --from-db org_brain_hq --region hq

Idempotent. Prints one report per tenant (ids and counts, never body text).
"""

from __future__ import annotations

import argparse
import json

from app.db import app_engine, region_engine, tenant_session
from app.keys import install as install_keys
from app.services import replication
from app.workers.outbox import tenant_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replicate")
    parser.add_argument("--from-db", required=True, help="the home region's ledger database")
    parser.add_argument("--region", required=True, help="the home region's name")
    args = parser.parse_args(argv)

    here, home = app_engine(), region_engine(args.from_db)
    tenants = sorted(set(tenant_ids(here)) & set(tenant_ids(home)))
    install_keys(tenants)
    for tenant in tenants:
        with tenant_session(tenant, home) as src, tenant_session(tenant, here) as dst:
            # The PoC publishes the tenant root: its scope id is the tenant id.
            report = replication.replicate(
                src, dst, tenant_id=tenant, region=args.region, src_scope=tenant, dst_scope=tenant
            )
        print(json.dumps({"tenant": tenant, **report.as_json()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
