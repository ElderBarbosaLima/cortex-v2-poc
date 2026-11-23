"""`make load-rules`: load customer rules from a spreadsheet (issue 47).

    python -m app.cli.load_rules --sheet rules.csv --guide column-guide.json \
        --scope jp/finance/q2c-cash-application --by CS-101 --api <the API's base URL>

Reads the CSV through the column guide, reports every row it cannot read
(with its row number), and sends the rest through `POST /v1/cortex/imports`
as the agent, on behalf of the person loading them (`--by`). Nothing is
approved here: each rule waits for its owner, who can approve them in bulk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from app.config import OrgBrainConfig
from app.domain_packs.cash_allocation.sheet import read_sheet
from app.seed.tenants import load_seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_rules")
    parser.add_argument("--sheet", required=True, type=Path)
    parser.add_argument("--guide", required=True, type=Path)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--by", required=True, help="person key of whoever loads the sheet")
    parser.add_argument("--api", required=True)
    parser.add_argument("--source", default="customer-rules-sheet")
    args = parser.parse_args(argv)

    seed = load_seed(Path(OrgBrainConfig().seed_dir))
    sheet = read_sheet(
        args.sheet.read_text(encoding="utf-8-sig"),
        json.loads(args.guide.read_text(encoding="utf-8")),
        scope=seed.node_ids[args.scope],
    )
    for problem in sheet.problems:
        print(
            json.dumps(
                {"row": problem["row"], "status": "not_read", "detail": problem["detail"]},
                ensure_ascii=False,
            )
        )
    if not sheet.items:
        return 1
    key = os.environ["CORTEX_E2E_AGENT_KEY"]
    resp = httpx.post(
        f"{args.api.rstrip('/')}/v1/cortex/imports",
        json={"source_system": args.source, "items": sheet.items},
        headers={"Authorization": f"Bearer {key}", "X-End-User-Id": seed.people[args.by]},
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"import failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        return 1
    for row, item in zip(sheet.rows, resp.json()["items"], strict=True):
        print(
            json.dumps(
                {
                    "row": row,
                    "status": item["status"],
                    "record_id": item["record_id"],
                    "problems": item["problems"],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
