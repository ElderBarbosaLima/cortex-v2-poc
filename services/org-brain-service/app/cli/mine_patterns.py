"""`make mine-patterns`: suggest rules from a history of resolved payments (issue 46).

    python -m app.cli.mine_patterns --tenant jp --domain cash_allocation \
        --scope jp/finance/q2c-cash-application --history <file.jsonl> [--now 2026-11-23]

Each line: {"case": ..., "outcome": ..., "decided_at": "..."}. Prints one line
per pattern (entities, exception, cases, outcome); never body text.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs import discover
from app.domain_packs.base import get_pack
from app.keys import install as install_keys
from app.seed.tenants import load_seed
from app.services import patterns
from app.services.writeback import RuleSuggestionPolicy, policies_by_domain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mine_patterns")
    parser.add_argument("--tenant", required=True, help="tenant key, e.g. jp")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--scope", required=True, help="scope key the rules are proposed at")
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--now", default=None, help="end of the window (date); default today")
    args = parser.parse_args(argv)

    cfg = OrgBrainConfig()
    discover()
    seed = load_seed(Path(cfg.seed_dir))
    tenant = next(t for t in seed.tenants if t.key == args.tenant)
    policy = policies_by_domain(cfg.rule_suggestion_by_domain).get(
        args.domain, RuleSuggestionPolicy.from_spec(cfg.rule_suggestion)
    )
    now = (
        dt.datetime.combine(dt.date.fromisoformat(args.now), dt.time(23, 59), dt.UTC)
        if args.now
        else dt.datetime.now(dt.UTC)
    )
    history = [json.loads(line) for line in args.history.read_text().splitlines() if line.strip()]
    install_keys([tenant.tenant_id])
    with tenant_session(tenant.tenant_id, app_engine()) as session:
        found = patterns.mine(
            session,
            tenant_id=tenant.tenant_id,
            pack=get_pack(args.domain),
            scope=seed.node_ids[args.scope],
            history=history,
            policy=policy,
            now=now,
        )
    print(
        json.dumps(
            {
                "policy": f"{policy.min_distinct_cases}/{policy.min_distinct_correctors}/"
                f"{policy.window_days}",
                "rows": len(history),
            }
        )
    )
    for f in found:
        print(json.dumps(f.as_json(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
