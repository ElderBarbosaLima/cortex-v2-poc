"""Generate data/synthetic/ (make gen-data). Deterministic: same seed, same bytes.

    python -m app.cli.gen_synthetic --out /data/synthetic

Writes the shared tenant (scope tree, people, consumers) and one directory per
workflow. Running it twice must leave `git diff` empty.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from app.domain_packs.custom_invoices import synthetic as custom_invoices
from app.synthetic.common import tenant_document, write_json

GENERATORS: dict[str, Callable[[Path], dict[str, int]]] = {
    custom_invoices.WORKFLOW: custom_invoices.generate,
}


def generate_all(out_dir: Path) -> dict[str, dict[str, int]]:
    write_json(out_dir / "tenant.json", tenant_document())
    return {name: gen(out_dir) for name, gen in GENERATORS.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the PoC's synthetic data")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for workflow, counts in generate_all(args.out).items():
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"{workflow}: {summary}")


if __name__ == "__main__":
    main()
