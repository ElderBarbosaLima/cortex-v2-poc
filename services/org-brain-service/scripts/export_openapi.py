"""`make contract`: write the single API spec (D15) from the code.

    python -m scripts.export_openapi --out /libraries/cortex-contract/openapi.json

Code-first, as the monorepo's arbitr-sdk (FastAPI spec → pinned snapshot →
generated models) and `make docs-drift`. `make contract-check` fails when the
committed spec differs from what the code produces.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.main import create_app


def build_spec() -> dict[str, Any]:
    # Building the schema runs no lifespan: no OPA, KMS or database is touched.
    return create_app().openapi()


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the /v1/cortex OpenAPI spec")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="fail if --out differs, write nothing")
    args = parser.parse_args()
    spec = render(build_spec())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != spec:
            raise SystemExit(f"{args.out} is out of date with the code: run make contract")
        print(f"{args.out} matches the code")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(spec, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
