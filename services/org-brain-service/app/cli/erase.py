"""`make erase-record` / `make erase-tenant`: crypto-shredding (issue 24).

    python -m app.cli.erase record --tenant <uuid> --record <uuid> --by <who> --reason <why>
    python -m app.cli.erase tenant --tenant <uuid> --by <who> --reason <why> --yes

Prints the erasure id and its report (ids and counts, never body text). A
tenant erasure cannot be undone and asks for --yes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]

from app.db import app_engine, tenant_session
from app.keys import install as install_keys
from app.services import erasure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erase")
    sub = parser.add_subparsers(dest="scope", required=True)
    rec = sub.add_parser("record", help="a record and everything derived from it")
    rec.add_argument("--record", required=True)
    ten = sub.add_parser("tenant", help="a whole tenant (cannot be undone)")
    ten.add_argument("--yes", action="store_true", help="confirm a tenant erasure")
    for p in (rec, ten):
        p.add_argument("--tenant", required=True)
        p.add_argument("--by", required=True, help="who asked (ticket or person)")
        p.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    install_keys([args.tenant])
    with tenant_session(args.tenant, app_engine()) as session:
        if args.scope == "record":
            try:
                erasure_id, report = erasure.erase_record(
                    session,
                    tenant_id=args.tenant,
                    record_id=args.record,
                    requested_by=args.by,
                    reason=args.reason,
                )
            except erasure.NothingToEraseError:
                print(f"nothing to erase: no record {args.record} in this tenant", file=sys.stderr)
                return 1
        else:
            if not args.yes:
                print("refusing: a tenant erasure cannot be undone; pass --yes", file=sys.stderr)
                return 2
            erasure_id, report = erasure.erase_tenant(
                session,
                tenant_id=args.tenant,
                kms=cast(Any, boto3).client("kms"),
                requested_by=args.by,
                reason=args.reason,
            )
    print(
        json.dumps(
            {
                "erasure_id": erasure_id,
                "scope": report.scope,
                "subject": report.subject,
                **report.as_json(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
