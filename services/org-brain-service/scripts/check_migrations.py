"""`make migrate-check`: the two Alembic rules that otherwise fail only at deploy.

1. Every revision id is at most 32 characters (alembic_version.version_num is
   VARCHAR(32)).
2. The graph has exactly one head.

Same rules as the monorepo's CLAUDE.md ("Alembic migrations — ALWAYS keep
revision ids ≤ 32 chars and the graph single-headed"). Reads the files with
Alembic's own script loader, so merge revisions (tuple down_revision) count.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MAX_ID = 32


def problems(script_location: Path) -> list[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    scripts = ScriptDirectory.from_config(cfg)
    found: list[str] = []
    for rev in scripts.walk_revisions():
        if len(rev.revision) > MAX_ID:
            found.append(
                f"revision id {rev.revision!r} is {len(rev.revision)} chars (max {MAX_ID})"
            )
    heads = scripts.get_heads()
    if len(heads) != 1:
        found.append(f"expected exactly one head, found {len(heads)}: {sorted(heads)}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Alembic revision ids and heads")
    parser.add_argument("--script-location", type=Path, default=Path("alembic"))
    args = parser.parse_args()
    found = problems(args.script_location)
    for problem in found:
        print(f"migrate-check: {problem}", file=sys.stderr)
    if not found:
        print("migrate-check: ok (one head, every revision id <= 32 chars)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
