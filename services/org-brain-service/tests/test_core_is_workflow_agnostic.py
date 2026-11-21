"""D18 guard: the core never knows which workflow is running.

Outside the packs themselves (and the CLIs that generate their data), no module
may import a real pack, name a workflow, or branch on one. If this fails, the
fix is in the design (move the logic into the pack), not in this test.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PACK_DIRS = {"custom_invoices", "cash_application"}
FORBIDDEN = re.compile(
    r"domain_packs\.(custom_invoices|cash_application)"
    r"|[\"'](custom_invoices|cash_application)[\"']"
    r"|\bworkflow\s*(==|!=|in)\s"
)


def _is_exempt(path: Path) -> bool:
    parts: tuple[str, ...] = path.relative_to(APP).parts
    top = parts[0] if parts else ""
    in_a_pack = top == "domain_packs" and len(parts) > 1 and parts[1] in PACK_DIRS
    return in_a_pack or top == "cli"


def test_no_core_module_imports_names_or_branches_on_a_workflow() -> None:
    offenders = [
        f"{p.relative_to(APP)}: {m.group(0)}"
        for p in sorted(APP.rglob("*.py"))
        if not _is_exempt(p)
        for m in FORBIDDEN.finditer(p.read_text())
    ]

    assert offenders == []


def test_the_guard_does_catch_an_offence(tmp_path: Path) -> None:
    sample = (
        'from app.domain_packs.cash_application import synthetic\nif workflow == "x":\n    pass\n'
    )

    assert len(FORBIDDEN.findall(sample)) == 2
