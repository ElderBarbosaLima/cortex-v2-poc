"""The cash_application synthetic data and golden set (issue 7)."""

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
from app.domain_packs.cash_application import synthetic as ca
from app.synthetic.common import FICTITIOUS_MARKERS

COMMITTED = Path(os.environ.get("CORTEX_SYNTHETIC_DIR", "/data/synthetic"))
FEE_RULE = ca.LEARNED.key


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("synthetic")
    generate_all(out)
    return out


@pytest.fixture(scope="module")
def data(generated: Path) -> dict[str, Any]:
    d = generated / ca.WORKFLOW
    return {
        "payments": {
            p["payment_id"]: p
            for p in (json.loads(line) for line in (d / "cases.jsonl").read_text().splitlines())
        },
        "invoices": {i["invoice_id"]: i for i in json.loads((d / "invoices.json").read_text())},
        "rules": json.loads((d / "rules.json").read_text()),
        "corrections": [
            json.loads(line) for line in (d / "corrections.jsonl").read_text().splitlines()
        ],
        "expected": json.loads((d / "expected.json").read_text()),
        "timeline": json.loads((d / "timeline.json").read_text()),
        "customers": json.loads((d / "customers.json").read_text())["customers"],
    }


def _difference(data: dict[str, Any], payment_id: str) -> int:
    payment = data["payments"][payment_id]
    invoice = data["invoices"][payment["open_invoices"][0]]
    return int(invoice["total"]) - int(payment["amount"])


def _walk(value: object) -> list[object]:
    if isinstance(value, dict):
        return [v for item in value.values() for v in _walk(item)]  # type: ignore[union-attr]
    if isinstance(value, list):
        return [v for item in value for v in _walk(item)]  # type: ignore[union-attr]
    return [value]


def test_committed_data_matches_the_generator(generated: Path) -> None:
    def files(root: Path) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(root.iterdir())}

    assert files(COMMITTED / ca.WORKFLOW) == files(generated / ca.WORKFLOW), "run make gen-data"


def test_no_floats_and_amounts_are_yen_integers(data: dict[str, Any]) -> None:
    assert not [v for v in _walk(data) if isinstance(v, float)]
    assert all(
        isinstance(p["amount"], int) and p["currency"] == "JPY" for p in data["payments"].values()
    )


def test_brief_case_pay_0113_is_verbatim_and_matches_inv_0007(data: dict[str, Any]) -> None:
    assert data["payments"]["PAY-0113"] == {
        "payment_id": "PAY-0113",
        "value_date": "2026-12-10",
        "amount": 989340,
        "currency": "JPY",
        "payer_name_raw": "ｶ)ｶｸｳﾃﾞﾝｷ",
        "bank": "サンプル銀行",
        "open_invoices": ["INV-0007"],
    }
    # Same invoice as the custom_invoices brief case: ¥990,000, ¥660 fee deducted.
    assert data["invoices"]["INV-0007"]["total"] == 990000
    assert _difference(data, "PAY-0113") == 660


def test_every_customer_is_fictitious(data: dict[str, Any]) -> None:
    assert all(any(m in c["customer_name"] for m in FICTITIOUS_MARKERS) for c in data["customers"])
    assert all("差押" not in c["customer_name"] for c in data["customers"])


def test_three_spellings_resolve_to_cust_0042_with_high_confidence(data: dict[str, Any]) -> None:
    resolutions = {
        e["resolution"]["payer_name_raw"]: e["resolution"]
        for e in data["expected"]
        if e["resolution"]["expected_entity"] == "customer:CUST-0042"
    }

    for spelling in ("ｶ)ｶｸｳﾃﾞﾝｷ", "ｶｸｳﾃﾞﾝｷ(ｶ", "ｶ)ｶｸｳﾃﾞﾝ"):
        assert resolutions[spelling]["band"] == "high", spelling


def test_ambiguous_low_and_unknown_payers_go_to_review(data: dict[str, Any]) -> None:
    unsure = [e for e in data["expected"] if e["resolution"]["band"] != "high"]

    assert {e["resolution"]["band"] for e in unsure} == {"ambiguous", "low", "none"}
    for e in unsure:
        assert e["route"] == "review", e["case_id"]
        assert not any(r.startswith("CA-C-") or r == FEE_RULE for r in e["expected_rules"])
    ambiguous = next(e for e in unsure if e["resolution"]["band"] == "ambiguous")
    assert ambiguous["resolution"]["expected_entity"] is None


def test_garnishment_always_forces_review(data: dict[str, Any]) -> None:
    garnished = [e for e in data["expected"] if e["group"] == "garnishment"]

    assert len(garnished) >= 3
    for e in garnished:
        assert "CA-G-GARNISH" in e["expected_rules"]
        assert e["route"] == "review"


def test_fee_rule_applies_only_after_approval_to_cust_0042_within_880(
    data: dict[str, Any],
) -> None:
    for e in data["expected"]:
        diff = _difference(data, e["case_id"])
        is_0042 = e["resolution"]["expected_entity"] == "customer:CUST-0042"
        should = (
            is_0042 and e["resolution"]["band"] == "high" and e["batch"] != "A" and 0 < diff <= 880
        )
        assert (FEE_RULE in e["expected_rules"]) == should, e["case_id"]
        if should:
            assert e["overridden"].get("CA-G-EXACT-AMOUNT") == FEE_RULE
            assert e["route"] == "auto"
    tags = {t for e in data["expected"] if e["group"] == "learned_rule" for t in e["tags"]}
    assert {"fee_exactly_880", "fee_over_limit", "no_difference"} <= tags


def test_same_fee_from_other_customers_is_never_booked_as_cust_0042s(data: dict[str, Any]) -> None:
    others = [e for e in data["expected"] if "same_fee_other_customer" in e["tags"]]

    assert others
    assert not any(FEE_RULE in e["expected_rules"] for e in others)


def test_scenario_2_prohibition_beats_the_local_write_off(data: dict[str, Any]) -> None:
    s2 = [e for e in data["expected"] if e["group"] == "scenario_2"]

    for e in s2:
        diff = _difference(data, e["case_id"])
        if 0 < diff < 1000:
            assert e["overridden"]["CA-L-SMALL-WRITEOFF"] == "CA-G-NO-WRITEOFF"
        else:
            assert "CA-L-SMALL-WRITEOFF" not in e["overridden"]
        assert "CA-L-SMALL-WRITEOFF" not in e["expected_rules"]
        assert "CA-G-NO-WRITEOFF" in e["expected_rules"]
    assert any(_difference(data, e["case_id"]) == 1000 for e in s2)


def test_scenario_3_contract_clause_shares_nothing_with_the_payment(data: dict[str, Any]) -> None:
    for e in (e for e in data["expected"] if e["group"] == "scenario_3"):
        assert "CA-C-0063-ALLOC" in e["expected_rules"]
        payment = data["payments"][e["case_id"]]
        assert not {"前受金", "充当", "契約"} & set(re.findall(r"\w+", payment["payer_name_raw"]))


def test_scenario_4_credit_hold_is_held_then_absent(data: dict[str, Any]) -> None:
    s4 = [e for e in data["expected"] if e["group"] == "scenario_4"]

    before = [e for e in s4 if e["batch"] in ("A", "B")]
    after = [e for e in s4 if e["batch"] == "C"]
    assert before and after
    for e in before:
        assert ca.RETIRED_KEY in e["expected_rules"]
        assert e["verify"][-1] == {
            "after": "retire-0017-hold",
            "result": "hold",
            "reason": "rule_no_longer_approved",
            "rule": ca.RETIRED_KEY,
        }
    for e in after:
        assert ca.RETIRED_KEY not in e["expected_rules"]


def test_global_evidence_rule_is_in_every_pack_of_this_tenant(data: dict[str, Any]) -> None:
    assert all("CA-G-EVIDENCE" in e["expected_rules"] for e in data["expected"])
    assert not [
        e for e in data["expected"] if any(k.startswith("OT-") for k in e["expected_rules"])
    ]


def test_golden_set_covers_every_required_group(data: dict[str, Any]) -> None:
    groups = Counter(e["group"] for e in data["expected"])

    assert 38 <= sum(groups.values()) <= 42
    for group in ("happy", "learned_rule", "unrelated", "scenario_2", "scenario_3", "scenario_4"):
        assert groups[group] >= 3, group


def test_customer_conditions_match_their_entities(data: dict[str, Any]) -> None:
    for rule in [*data["rules"], data["corrections"][0]["expected_rule"]]:
        named = set(re.findall(r'customer_id == "([^"]+)"', rule["body"]["condition"]))
        assert {f"customer:{c}" for c in named} <= set(rule["entities"]), rule["key"]


def test_corrections_and_timeline_are_consistent(data: dict[str, Any]) -> None:
    corrections = data["corrections"]
    dates = sorted(dt.date.fromisoformat(c["corrected_at"][:10]) for c in corrections)
    steps = data["timeline"]["steps"]

    assert len(corrections) == 3 and len({c["reviewer"] for c in corrections}) == 2
    assert (dates[-1] - dates[0]).days < 30
    processed = sorted(cid for s in steps if s["step"].startswith("process-") for cid in s["cases"])
    assert processed == sorted(data["payments"])
    assert {v["after"] for e in data["expected"] for v in e["verify"]} <= {s["step"] for s in steps}
