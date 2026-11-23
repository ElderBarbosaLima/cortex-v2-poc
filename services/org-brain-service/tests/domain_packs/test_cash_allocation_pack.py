"""The cash_allocation pack (issues 40–41): IBM's workflow. Two-layer rules,
typed per exception type; the payment input IBM's AI sends; CEL vs the oracle."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.domain_packs.cash_allocation.pack import AllocationRuleBody
from app.domain_packs.cash_allocation.synthetic import RULES
from app.models import Kind
from app.services.admission import Proposal, validate_proposal
from tests.domain_packs.contract import assert_pack_contract
from tests.test_identity_and_seed import SEED_DIR

register_pack(PACK)
DATA = SEED_DIR / "cash_allocation"
cel = CelExprEvaluator()
CASES = [json.loads(line) for line in (DATA / "cases.jsonl").read_text().splitlines()]
BY_ID = {c["case_id"]: c for c in CASES}

# Slide 15: the Excel row "Yamada Shoji · bank fee: usually ¥660 short, sometimes
# more. Check tax if bigger. Ask Tanaka." as one Cortex rule, both layers.
SLIDE_15 = {
    "text_ja": "山田商事は振込手数料を差し引いて支払う。通常は 660 円不足。"
    "880 円を超える不足は手数料ではない可能性があるため、"
    "消費税の差額を確認し、不明なら担当者へ回す。",
    "targets": ["allocation.bank_fee"],
    "condition": 'payment.customer_id == "CUST-0101" && payment.gap > 0 && payment.gap <= 880',
    "exception": "bank_fee",
    "engine": {"expected_fee": 660, "max_fee": 880, "treatment": "fee_expense"},
}


def _proposal(
    body: dict[str, Any], entities: tuple[str, ...] = ("customer:CUST-0101",)
) -> Proposal:
    return Proposal(
        domain="cash_allocation",
        kind=Kind.RULE,
        schema_version="cash_allocation.rule/1",
        body=body,
        scope="00000000-0000-0000-0000-000000000001",
        entities=entities,
        enforcement="default",
        authority=10,
        sensitivity={"class": "internal"},
        valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )


def test_the_pack_meets_the_contract() -> None:
    outcome = {
        "correction_id": "O-1",
        "case_id": "PAY-A001",
        "reviewer": "R-101",
        "corrected_at": "2026-12-04T15:00:00+09:00",
        "result": "corrected",
        "exception": "bank_fee",
        "selected_invoices": ["INV-A100"],
        "note_ja": "振込手数料 660 円を差し引いた入金。",
    }
    assert_pack_contract(PACK, CASES, outcome, BY_ID["PAY-A001"])


def test_the_slide_15_example_is_admissible() -> None:
    assert validate_proposal(_proposal(SLIDE_15))[1] == []


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"engine": {"expected_fee": 990, "max_fee": 880}}, "engine.expected_fee"),
        ({"engine": {"expected_fee": 660, "max_fee": 500}}, "max_fee must be at least"),
        ({"engine": {"expected_fee": 660, "treatment": "ignore"}}, "engine.treatment"),
        ({"engine": {"expected_fee": 660, "colour": "red"}}, "engine.colour"),
        ({"engine": None}, "needs its engine fields"),
        ({"targets": ["allocation.combined_payment"]}, "targets must include allocation.bank_fee"),
        ({"exception": "lottery"}, "exception"),
    ],
)
def test_a_wrong_engine_field_is_refused_with_its_path(change: dict[str, Any], where: str) -> None:
    problems = validate_proposal(_proposal({**SLIDE_15, **change}))[1]

    assert [p.code for p in problems][:1] == ["invalid_body"]
    assert where in problems[0].detail


@pytest.mark.parametrize(
    "engine",
    [
        {"exception": "combined_payment", "engine": {"pick": "po_number", "max_invoices": 10}},
        {"exception": "tax_difference", "engine": {"rounding": "floor", "tolerance": 1}},
        {"exception": "duplicate_payment", "engine": {"action": "apply_to_next_invoice"}},
        {"exception": "partial_payment", "engine": {"action": "instalments", "instalments": 3}},
        {"exception": "partial_payment", "engine": {"action": "leave_balance_open"}},
    ],
    ids=lambda e: e["exception"],
)
def test_every_exception_type_has_its_engine_schema(engine: dict[str, Any]) -> None:
    body = {**SLIDE_15, **engine, "targets": [f"allocation.{engine['exception']}"]}
    assert validate_proposal(_proposal(body))[1] == []


def test_guidance_only_rules_have_no_engine() -> None:
    body = {
        "text_ja": "判断に迷う入金は担当者へ回す。",
        "targets": ["allocation.review"],
        "condition": "true",
    }
    assert validate_proposal(_proposal(body, entities=()))[1] == []
    assert validate_proposal(_proposal({**body, "engine": {"action": "hold"}}, entities=()))[1]


def test_the_case_is_what_ibm_sends_and_the_gap_is_theirs_or_computed() -> None:
    combined = BY_ID["PAY-A004"]
    ctx = PACK.condition_context(combined, PACK.resolve_entities(combined))["payment"]
    without_gap = {k: v for k, v in combined.items() if k != "gap"}

    assert PACK.resolve_entities(combined)[0].key == "customer:CUST-0102"  # CAT's mapping, as given
    assert (ctx["candidate_count"], ctx["candidates_total"], ctx["has_remittance"]) == (
        3,
        385000,
        True,
    )
    assert ctx["gap"] == 0
    assert PACK.condition_context(without_gap, [])["payment"]["gap"] == 0


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.key)
def test_each_condition_agrees_with_its_oracle_on_every_case(rule: Any) -> None:
    compiled = cel.compile(rule.condition, variables=PACK.condition_variables)
    for case in CASES:
        payment = PACK.condition_context(case, PACK.resolve_entities(case))["payment"]
        assert cel.evaluate(compiled, {"payment": payment}) is rule.predicate(
            {"payment": payment}
        ), case["case_id"]


@pytest.mark.parametrize(
    "doc", json.loads((DATA / "rules.json").read_text()), ids=lambda d: d["key"]
)
def test_every_seed_rule_is_admissible(doc: dict[str, Any]) -> None:
    proposal = _proposal(doc["body"], entities=tuple(doc["entities"]))
    assert validate_proposal(proposal)[1] == []
    assert AllocationRuleBody.model_validate(doc["body"]).engine is not None


def test_an_outcome_becomes_a_rule_with_both_layers() -> None:
    outcome = PACK.correction_schema.model_validate(
        {
            "correction_id": "O-2",
            "case_id": "PAY-A007",
            "reviewer": "R-101",
            "corrected_at": "2026-12-04T15:00:00+09:00",
            "exception": "bank_fee",
            "note_ja": "Short by the bank fee.",
        }
    )

    draft = PACK.correction_to_rule(outcome, case=BY_ID["PAY-A007"])

    assert draft.body["engine"] == {"expected_fee": 440, "max_fee": 880, "treatment": "fee_expense"}
    assert draft.body["text_ja"] == "For CUST-0104: Short by the bank fee."  # the note's language
    assert draft.entities == ("customer:CUST-0104",)
    assert validate_proposal(_proposal(dict(draft.body), entities=draft.entities))[1] == []


def test_a_subsidiary_carries_its_groups_entity_and_group_id() -> None:
    sub = BY_ID["PAY-A011"]
    entities = PACK.resolve_entities(sub)
    payment = PACK.condition_context(sub, entities)["payment"]

    assert [m.key for m in entities] == ["customer:CUST-0106", "customer:CUST-0104"]
    assert (payment["customer_id"], payment["group_id"]) == ("CUST-0106", "CUST-0104")
    assert PACK.condition_context(BY_ID["PAY-A001"], [])["payment"]["group_id"] == "CUST-0101"
