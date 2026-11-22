"""Admission validation (pure): every problem, at once, with a code. Fake pack only."""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import pytest

from app.domain_packs.base import register_pack
from app.models import Kind
from app.services.admission import Proposal, conflict_key, validate_proposal
from tests.factories import SCOPE
from tests.fakes.fake_pack import PACK

register_pack(PACK)


def proposal(**overrides: Any) -> Proposal:
    base = Proposal(
        domain="widgets",
        kind=Kind.RULE,
        schema_version="widgets.rule/1",
        body={
            "text_ja": "W-1 の注文には取扱注意ラベルを付ける。",
            "targets": ["order.label"],
            "condition": 'order.widget_code == "W-1"',
            "label": "取扱注意",
        },
        scope=SCOPE,
        entities=("widget:W-1",),
        enforcement="default",
        authority=10,
        sensitivity={"class": "internal", "personal_data": False},
        valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )
    return dataclasses.replace(base, **overrides)


def codes(p: Proposal) -> list[str]:
    return [problem.code for problem in validate_proposal(p)[1]]


def body(**changes: Any) -> dict[str, Any]:
    return {**proposal().body, **changes}


def test_a_good_proposal_has_no_problems() -> None:
    assert codes(proposal()) == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"domain": "nope"}, "unknown_domain"),
        ({"schema_version": "widgets.rule/9"}, "unknown_schema_version"),
        ({"entities": ("架空電機株式会社",)}, "non_canonical_entity"),
        (
            {"entities": ("widget:W-1", "customer:CUST-0042")},
            "non_canonical_entity",
        ),  # not a widget pack type
        ({"enforcement": None}, "missing_enforcement"),
        ({"body": {"text_ja": "x"}}, "invalid_body"),
        ({"body": body(targets=["order.price"])}, "unknown_target"),
        ({"body": body(condition='order.widget_code == "W-1" &&')}, "invalid_condition"),
        ({"body": body(condition="nosuch.total > 1")}, "invalid_condition"),
        ({"body": body(condition='order.widget_colour == "赤"')}, "unknown_field"),
        ({"entities": ()}, "entity_mismatch"),  # Q6: the condition names W-1
        ({"sensitivity": {"class": "secret"}}, "invalid_sensitivity"),
    ],
)
def test_each_problem_has_its_code(overrides: dict[str, Any], code: str) -> None:
    assert code in codes(proposal(**overrides))


def test_all_problems_are_reported_together() -> None:
    bad = proposal(
        schema_version="x",
        entities=("Tanaka",),
        body=body(condition='order.widget_code == "W-1" &&'),
    )

    assert set(codes(bad)) >= {
        "unknown_schema_version",
        "non_canonical_entity",
        "invalid_condition",
    }


def test_facts_are_not_checked_against_the_rule_schema() -> None:
    fact = proposal(
        kind=Kind.FACT,
        schema_version="widgets.fact/1",
        body={"text_ja": "W-2 は青のみ"},
        enforcement=None,
    )

    assert codes(fact) == []


def test_the_conflict_key_ignores_order_and_separates_conflicts() -> None:
    a = proposal(
        body=body(targets=["order.label", "order.colour_check"]),
        entities=("widget:W-1", "widget:W-2"),
    )
    b = proposal(
        body=body(targets=["order.colour_check", "order.label"]),
        entities=("widget:W-2", "widget:W-1"),
    )
    other = proposal(entities=("widget:W-3",))

    assert conflict_key(a) == conflict_key(b)
    assert conflict_key(a) != conflict_key(other)
