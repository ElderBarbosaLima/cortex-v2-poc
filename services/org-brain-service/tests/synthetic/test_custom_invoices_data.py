"""The custom_invoices synthetic data and golden set (issue 6).

Generated into tmp_path; also compared with the committed data/synthetic so a
hand edit or a generator change without `make gen-data` fails here.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.cli.gen_synthetic import generate_all
from app.domain_packs.custom_invoices import synthetic as ci
from app.synthetic.common import FICTITIOUS_MARKERS, is_valid_corporate_number, tax_by_rate

COMMITTED = Path(os.environ.get("CORTEX_SYNTHETIC_DIR", "/data/synthetic"))

# Real companies that must never appear (spot list; every name must also carry
# a fictitious marker, which is the real guard).
REAL_NAMES = (
    "IBM",
    "アイ・ビー・エム",
    "トヨタ",
    "ソニー",
    "日立",
    "富士通",
    "NEC",
    "パナソニック",
    "三菱",
)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("synthetic")
    generate_all(out)
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _load(root: Path) -> dict[str, Any]:
    d = root / ci.WORKFLOW
    return {
        "cases": [json.loads(line) for line in (d / "cases.jsonl").read_text().splitlines()],
        "rules": json.loads((d / "rules.json").read_text()),
        "corrections": [
            json.loads(line) for line in (d / "corrections.jsonl").read_text().splitlines()
        ],
        "expected": json.loads((d / "expected.json").read_text()),
        "timeline": json.loads((d / "timeline.json").read_text()),
        "customers": json.loads((d / "customers.json").read_text()),
    }


def _walk(value: object) -> list[object]:
    if isinstance(value, dict):
        return [v for item in value.values() for v in _walk(item)]  # type: ignore[union-attr]
    if isinstance(value, list):
        return [v for item in value for v in _walk(item)]  # type: ignore[union-attr]
    return [value]


def test_generation_is_deterministic(generated: Path, tmp_path: Path) -> None:
    generate_all(tmp_path)

    assert _files(tmp_path) == _files(generated)


def test_committed_data_matches_the_generator(generated: Path) -> None:
    assert _files(COMMITTED / ci.WORKFLOW) == _files(generated / ci.WORKFLOW), "run make gen-data"
    assert (COMMITTED / "tenant.json").read_bytes() == (generated / "tenant.json").read_bytes()


def test_no_floats_anywhere(generated: Path) -> None:
    data = _load(generated)

    assert not [v for v in _walk(data) if isinstance(v, float)]


def test_amounts_add_up_with_one_rounding_per_tax_rate(generated: Path) -> None:
    for case in _load(generated)["cases"]:
        taxes = tax_by_rate(case["lines"])
        subtotal = sum(line["amount_excl_tax"] for line in case["lines"])
        assert case["tax_by_rate"] == {str(r): t for r, t in taxes.items()}, case["case_id"]
        assert case["tax_total"] == sum(taxes.values()), case["case_id"]
        assert case["total"] == subtotal + case["tax_total"], case["case_id"]


def test_every_company_is_clearly_fictitious(generated: Path) -> None:
    data = _load(generated)
    names = [c["customer_name"] for c in data["cases"]]
    names += [c["customer_name"] for c in data["customers"]["customers"]]
    names.append(data["customers"]["issuer"]["name"])

    assert all(any(m in n for m in FICTITIOUS_MARKERS) for n in names)
    text = (generated / ci.WORKFLOW / "cases.jsonl").read_text()
    assert not [real for real in REAL_NAMES if real in text]


def test_issuer_registration_number_is_well_formed(generated: Path) -> None:
    regno = _load(generated)["customers"]["issuer"]["registration_number"]

    assert re.fullmatch(r"T\d{13}", regno)
    assert is_valid_corporate_number(regno[1:])


def test_brief_case_inv_0007_is_verbatim(generated: Path) -> None:
    case = next(c for c in _load(generated)["cases"] if c["case_id"] == "INV-0007")

    assert {k: v for k, v in case.items() if k != "tax_by_rate"} == {
        "case_id": "INV-0007", "customer_id": "CUST-0042", "customer_name": "架空電機株式会社",
        "channel": "coupa", "issue_date": "2026-12-03", "currency": "JPY",
        "lines": [{"desc": "保守サービス 12月分", "amount_excl_tax": 900000, "tax_rate": 10}],
        "tax_total": 90000, "total": 990000, "po_number": "PO-88231", "attachments": [],
    }  # fmt: skip


def test_golden_set_covers_every_required_group(generated: Path) -> None:
    groups = Counter(e["group"] for e in _load(generated)["expected"])

    assert 38 <= sum(groups.values()) <= 42
    for group in ("happy", "learned_rule", "unrelated", "scenario_2", "scenario_3", "scenario_4"):
        assert groups[group] >= 4, group


def test_learned_rule_appears_only_after_approval_and_only_where_it_applies(
    generated: Path,
) -> None:
    data = _load(generated)
    cases = {c["case_id"]: c for c in data["cases"]}
    with_rule = {e["case_id"] for e in data["expected"] if ci.LEARNED.key in e["expected_rules"]}

    assert with_rule
    for e in data["expected"]:
        case = cases[e["case_id"]]
        applies = case["customer_id"] == "CUST-0042" and case["channel"] == "coupa"
        assert (e["case_id"] in with_rule) == (applies and e["batch"] in ("B", "C")), e["case_id"]
    unrelated = [e for e in data["expected"] if e["group"] == "unrelated"]
    assert unrelated and not any(ci.LEARNED.key in e["expected_rules"] for e in unrelated)


def test_scenario_2_prohibition_beats_the_local_instruction(generated: Path) -> None:
    data = _load(generated)
    totals = {c["case_id"]: c["total"] for c in data["cases"]}
    s2 = [e for e in data["expected"] if e["group"] == "scenario_2"]

    for e in s2:
        if totals[e["case_id"]] < 30000:
            assert e["overridden"] == {"CI-L-SMALL-OMIT-REGNO": "CI-G-NO-REGNO-OMIT"}
        else:
            assert e["overridden"] == {}, (
                "the boundary case (30,000) must not trigger the local rule"
            )
        assert "CI-L-SMALL-OMIT-REGNO" not in e["expected_rules"]
        assert "CI-G-NO-REGNO-OMIT" in e["expected_rules"]
    assert any(totals[e["case_id"]] == 30000 for e in s2)
    assert all(e["group"] == "scenario_2" for e in data["expected"] if e["overridden"])


def test_scenario_3_mandatory_rule_shares_no_words_with_the_case(generated: Path) -> None:
    data = _load(generated)
    rule = next(r for r in data["rules"] if r["key"] == "CI-C-0063-PAYTERMS")
    cases = {c["case_id"]: c for c in data["cases"]}

    for e in (e for e in data["expected"] if e["group"] == "scenario_3"):
        assert rule["key"] in e["expected_rules"]
        case_text = " ".join(line["desc"] for line in cases[e["case_id"]]["lines"])
        assert not {"支払条件", "月末締め", "契約"} & set(re.findall(r"\w+", case_text))


def test_scenario_4_retired_rule_is_held_then_absent(generated: Path) -> None:
    data = _load(generated)
    s4 = [e for e in data["expected"] if e["group"] == "scenario_4"]

    before = [e for e in s4 if e["batch"] in ("A", "B")]
    after = [e for e in s4 if e["batch"] == "C"]
    assert before and after
    for e in before:
        assert ci.RETIRED_KEY in e["expected_rules"]
        assert {"after": "retire-0017-dept", "result": "hold", "reason": "rule_no_longer_approved",
                "rule": ci.RETIRED_KEY} in e["verify"]  # fmt: skip
    for e in after:
        assert ci.RETIRED_KEY not in e["expected_rules"]
    retire = next(s for s in data["timeline"]["steps"] if s["step"] == "retire-0017-dept")
    assert retire["projection"] == "lagging"


def test_other_tenant_rule_never_reaches_this_tenant(generated: Path) -> None:
    data = _load(generated)

    assert any(r["tenant"] == "other" for r in data["rules"])
    assert not [
        e for e in data["expected"] if any(k.startswith("OT-") for k in e["expected_rules"])
    ]


def test_customer_conditions_match_their_entities(generated: Path) -> None:
    """Q6: a CEL condition on customer_id must name a customer that is in entities[]."""
    data = _load(generated)
    rules = [*data["rules"], data["corrections"][0]["expected_rule"]]

    for rule in rules:
        named = set(re.findall(r'customer_id == "([^"]+)"', rule["body"]["condition"]))
        assert {f"customer:{c}" for c in named} <= set(rule["entities"]), rule["key"]


def test_corrections_fit_the_strict_threshold_window(generated: Path) -> None:
    corrections = _load(generated)["corrections"]
    dates = sorted(dt.date.fromisoformat(c["corrected_at"][:10]) for c in corrections)

    assert len(corrections) == 3
    assert len({c["reviewer"] for c in corrections}) == 2
    assert (dates[-1] - dates[0]).days < 30  # inside the strict variant's window_days=30


def test_timeline_references_only_known_cases_and_corrections(generated: Path) -> None:
    data = _load(generated)
    case_ids = {c["case_id"] for c in data["cases"]}
    correction_ids = {c["correction_id"] for c in data["corrections"]}
    steps = data["timeline"]["steps"]

    processed = [cid for s in steps if s["step"].startswith("process-") for cid in s["cases"]]
    assert sorted(processed) == sorted(case_ids)
    assert {s["correction"] for s in steps if "correction" in s} == correction_ids
    step_names = {s["step"] for s in steps}
    assert {v["after"] for e in data["expected"] for v in e["verify"]} <= step_names
