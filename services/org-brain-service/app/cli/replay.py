"""`make replay`: replay resolved payments through Cortex and score them (issue 49).

    python -m app.cli.replay --file <replay.jsonl> --api <the API's base URL>

Each line: {"case": <IBM's payment>, "specialist": {"exception", "engine"}}.
Every payment is asked for as IBM's AI would (ibm-pr-agent, purpose
payment_reconciliation, as of its value date); suggestion acceptance comes
from the review queue (the Desk's key, as the scope's owner). Prints the
measures against slide 26's targets, as JSON and as a Markdown table.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from app.config import OrgBrainConfig
from app.seed.tenants import load_seed
from app.services.replay import TARGETS, Replayed, score

SCOPE = "jp/finance/q2c-cash-application"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replay")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--api", required=True)
    args = parser.parse_args(argv)

    seed = load_seed(Path(OrgBrainConfig().seed_dir))
    scope = seed.node_ids[SCOPE]
    api = args.api.rstrip("/")
    ibm = {"Authorization": f"Bearer {os.environ['CORTEX_E2E_IBM_KEY']}"}
    desk = {
        "Authorization": f"Bearer {os.environ['CORTEX_DESK_API_KEY']}",
        "X-End-User-Id": seed.people["OWN-001"],
    }
    replayed: list[Replayed] = []
    with httpx.Client(timeout=30) as http:
        for line in args.file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            case = row["case"]
            resp = http.post(
                f"{api}/v1/cortex/context-packs",
                headers=ibm,
                json={
                    "domain": "cash_allocation",
                    "case": case,
                    "scope": scope,
                    "purpose": "payment_reconciliation",
                    "as_of": case["payment"]["value_date"],
                },
            )
            resp.raise_for_status()
            replayed.append(
                Replayed(
                    case_ref=case["case_id"],
                    gap=int(case.get("gap") or 0),
                    candidates=len(case.get("candidate_invoices") or []),
                    specialist_exception=row["specialist"].get("exception"),
                    specialist_engine=row["specialist"].get("engine"),
                    rules=[r for r in resp.json()["records"] if r["kind"] == "rule"],
                )
            )
        metrics = http.get(
            f"{api}/v1/cortex/metrics/suggestions",
            headers=desk,
            params={"scope": scope, "purpose": "rule_review"},
        ).json()
    result = score(replayed).as_json() | {
        "suggestions_accepted": metrics.get("acceptance"),
        "suggestions": metrics,
    }
    print(json.dumps(result, ensure_ascii=False))
    rows = [
        ("Payments the rules would have covered", "covered", "74% (CAT today)"),
        ("Matches with rule evidence (ID, version, approver)", "rule_evidence", "not recorded"),
        ("Agreement with the specialist, where a rule applied", "agreement", "not measured"),
        ("Suggested rules owners accepted", "suggestions_accepted", "none"),
    ]
    print("\n| Measure | Today | Target | Replay |\n|---|---|---|---|")
    for label, key, today in rows:
        value = result.get(key)
        shown = "—" if value is None else f"{value:.0%}"
        print(f"| {label} | {today} | {TARGETS[key]:.0%} | {shown} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
