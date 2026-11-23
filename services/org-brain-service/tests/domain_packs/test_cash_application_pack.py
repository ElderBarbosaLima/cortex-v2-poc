"""The cash_application pack (issue 26): contract, payer resolution against the
hand-stated expectations, CEL vs the oracle, garnishment, correction → rule."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import register_pack
from app.domain_packs.cash_application import PACK
from app.domain_packs.cash_application.pack import route
from app.domain_packs.cash_application.resolver import resolve
from app.domain_packs.cash_application.synthetic import BY_CUSTOMER, LEARNED, RULES
from app.models import Kind
from app.services.admission import Proposal, validate_proposal
from tests.domain_packs.contract import assert_pack_contract
from tests.test_identity_and_seed import SEED_DIR

register_pack(PACK)
DATA = SEED_DIR / "cash_application"
cel = CelExprEvaluator()
REGISTERED = {c: tuple(v["bank_names"]) for c, v in BY_CUSTOMER.items()}


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


def build_cases(payments: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """A case is the payment plus the open invoice the agent matches it with."""
    invoices = {i["invoice_id"]: i for i in json.loads((DATA / "invoices.json").read_text())}
    return {
        pid: {"payment": p, "invoice": invoices[p["open_invoices"][0]]}
        for pid, p in payments.items()
    }


CASES = build_cases({p["payment_id"]: p for p in _jsonl("cases.jsonl")})
EXPECTED = {e["case_id"]: e for e in json.loads((DATA / "expected.json").read_text())}
CORRECTIONS = _jsonl("corrections.jsonl")


def ideal_output(case: dict[str, Any]) -> dict[str, Any]:
    """The match a careful person would post: the expected route, the evidence,
    a difference booked as a fee when auto, never written off."""
    pid = case["payment"]["payment_id"]
    auto = EXPECTED[pid]["route"] == "auto"
    diff = int(case["invoice"]["total"]) - int(case["payment"]["amount"])
    return {
        "mode": "auto" if auto else "review",
        "evidence": {"payment_id": pid, "invoice_id": case["invoice"]["invoice_id"]},
        "difference_treatment": ("fee" if diff else "none") if auto else "review",
    }


def test_the_pack_passes_the_contract() -> None:
    assert_pack_contract(PACK, list(CASES.values()), CORRECTIONS[0], CASES["PAY-0113"])


@pytest.mark.parametrize("pid", sorted(EXPECTED))
def test_every_payer_resolves_as_the_golden_set_states(pid: str) -> None:
    want = EXPECTED[pid]["resolution"]
    got = resolve(CASES[pid]["payment"]["payer_name_raw"], REGISTERED)

    assert (got.band, got.evidence) == (want["band"], want["evidence"])
    expected = want["expected_entity"]
    if got.band == "high":
        assert f"customer:{got.customer_id}" == expected
    else:
        assert got.customer_id is None  # only high confidence names a customer
        if expected:
            assert expected.removeprefix("customer:") in got.candidates


@pytest.mark.parametrize(
    "raw",
    ["ｶ)ｶｸｳﾃﾞﾝｷ", "ｶｸｳﾃﾞﾝｷ(ｶ", "ｶ)ｶｸｳﾃﾞﾝ", "ｶﾌﾞｼｷｶﾞｲｼｬｶｸｳﾃﾞﾝｷ", "カ）カクウデンキ", "ｶ) ｶｸｳﾃﾞﾝｷ"],
)
def test_the_spellings_of_cust_0042_resolve_with_high_confidence(raw: str) -> None:
    r = resolve(raw, REGISTERED)

    assert (r.customer_id, r.band) == ("CUST-0042", "high")


def test_more_than_one_candidate_is_ambiguous_and_never_auto_matched() -> None:
    case = CASES["PAY-0117"]
    matches = PACK.resolve_entities(case)
    customer = matches[1]

    assert (customer.key, customer.confidence) == (None, "ambiguous")
    assert set(customer.candidates) == {"customer:CUST-0017", "customer:CUST-0105"}
    assert route(case, []) == "review"


def test_the_payer_key_is_opaque() -> None:
    payer = PACK.resolve_entities(CASES["PAY-0113"])[0]

    assert payer.key and payer.key.startswith("payer:")
    assert "ｶｸｳ" not in payer.key and "カクウ" not in payer.key


@pytest.mark.parametrize("rule", [*RULES, LEARNED], ids=lambda r: r.key)
def test_each_cel_condition_agrees_with_the_oracle_on_every_case(rule: Any) -> None:
    compiled = cel.compile(rule.condition, variables=PACK.condition_variables)
    for pid, case in CASES.items():
        want = EXPECTED[pid]["resolution"]
        resolved = (
            want["expected_entity"].removeprefix("customer:") if want["band"] == "high" else None
        )
        oracle_ctx = {
            "payment": {
                **case["payment"],
                "customer_id": resolved,
                "garnishment_registered": bool(
                    BY_CUSTOMER.get(resolved or "", {}).get("garnishment_registered", False)
                ),
            },
            "invoice": case["invoice"],
        }
        context = PACK.condition_context(case, PACK.resolve_entities(case))
        assert cel.evaluate(compiled, context) is rule.predicate(oracle_ctx), pid


def test_garnishment_always_blocks_an_automatic_match() -> None:
    garnish = PACK.rule_body_schema.model_validate(
        {"text_ja": "差押", "targets": ["match.mode"], "condition": "true"}
    )
    for pid in (p for p, e in EXPECTED.items() if e["group"] == "garnishment"):
        assert "CA-G-GARNISH" in EXPECTED[pid]["expected_rules"]
        assert route(CASES[pid], [garnish]) == "review"
        auto = {**ideal_output(CASES[pid]), "mode": "auto"}
        problems = PACK.check_output(auto, [garnish], CASES[pid])
        assert "match.mode: this payment must be reviewed by a person, not auto-matched" in problems


@pytest.mark.parametrize(
    "doc", json.loads((DATA / "rules.json").read_text()), ids=lambda d: d["key"]
)
def test_every_seed_rule_is_admissible(doc: dict[str, Any]) -> None:
    proposal = Proposal(
        domain="cash_application",
        kind=Kind(doc["kind"]),
        schema_version=doc["schema_version"],
        body=doc["body"],
        scope="00000000-0000-0000-0000-000000000001",
        entities=tuple(doc["entities"]),
        enforcement=doc["enforcement"],
        authority=doc["authority"],
        sensitivity=doc["sensitivity"],
        valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )

    assert validate_proposal(proposal)[1] == []


def test_a_correction_becomes_the_fee_rule() -> None:
    correction = PACK.correction_schema.model_validate(CORRECTIONS[0])
    draft = PACK.correction_to_rule(correction, case=CASES["PAY-0113"])

    assert draft.body["condition"] == LEARNED.condition
    assert draft.body["targets"] == list(LEARNED.targets)
    assert draft.entities == LEARNED.entities
    assert "支払手数料" in draft.body["requirements"]["match.difference"]


def test_no_rule_is_learned_for_a_payer_not_confidently_known() -> None:
    correction = PACK.correction_schema.model_validate(CORRECTIONS[0])

    with pytest.raises(ValueError, match="high confidence"):
        PACK.correction_to_rule(correction, case=CASES["PAY-0117"])


def _diff(pid: str) -> int:
    return int(CASES[pid]["invoice"]["total"]) - int(CASES[pid]["payment"]["amount"])


# Review because of the payer or of an unexplained difference: nothing a rule
# in the pack says, only the case shows it.
CASE_ONLY_REVIEWS = sorted(
    p
    for p, e in EXPECTED.items()
    if e["route"] == "review" and (e["resolution"]["band"] != "high" or _diff(p) != 0)
)


@pytest.mark.parametrize("pid", CASE_ONLY_REVIEWS)
def test_an_auto_match_the_case_forbids_is_refused(pid: str) -> None:
    case = CASES[pid]
    evidence = PACK.rule_body_schema.model_validate(
        {"text_ja": "根拠", "targets": ["match.evidence"], "condition": "true"}
    )
    auto = {**ideal_output(case), "mode": "auto"}

    assert any(p.startswith("route:") for p in PACK.check_output(auto, [evidence], case))


def test_the_ambiguous_payer_cannot_be_auto_matched_even_with_only_the_evidence_rule() -> None:
    case = CASES["PAY-0117"]
    evidence = PACK.rule_body_schema.model_validate(
        {"text_ja": "根拠", "targets": ["match.evidence"], "condition": "true"}
    )
    auto = {**ideal_output(case), "mode": "auto"}

    assert PACK.check_output(auto, [evidence], case) == [
        "route: this match goes to review (ambiguous payer resolution or an unexplained "
        "difference), not auto"
    ]
    assert PACK.check_output(ideal_output(case), [evidence], case) == []
