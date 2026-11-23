"""Replay scoring (issue 49): the measures slide 26 asks for, from replayed
packs and what the specialist did. Pure, no services."""

from __future__ import annotations

from typing import Any

from app.services.replay import Replayed, score


def _rule(
    exception: str, engine: dict[str, Any], role: str = "applied", approver: str | None = "u"
) -> dict[str, Any]:
    return {
        "role": role,
        "approved_by": approver,
        "body": {"exception": exception, "engine": engine},
    }


FEE = _rule("bank_fee", {"expected_fee": 660, "treatment": "fee_expense"})


def test_the_measures() -> None:
    s = score(
        [
            Replayed("plain", 0, 1, None, None, []),  # covered, no evidence
            Replayed(
                "fee", 660, 1, "bank_fee", {"treatment": "fee_expense"}, [FEE]
            ),  # covered, agrees
            Replayed(
                "hold", 440, 1, "bank_fee", {"treatment": "hold_for_review"}, [FEE]
            ),  # covered, differs
            Replayed("tax", 1, 1, "tax_difference", None, [FEE]),  # not covered
            Replayed(
                "lost", 660, 1, "bank_fee", None, [_rule("bank_fee", {}, role="overridden")]
            ),  # not covered
        ]
    ).as_json()

    assert s["payments"] == 5
    assert s["rule_evidence"] == 1.0  # both rule-settled payments cite their rule and approver
    assert s["covered"] == 0.6  # plain, fee, hold
    assert s["agreement"] == 0.5  # of fee and hold, only fee
    assert [m["case"] for m in s["misses"]] == ["hold", "tax", "lost"]


def test_nothing_to_score_is_null_not_zero() -> None:
    assert score([]).as_json() | {"misses": []} == {
        "payments": 0,
        "rule_evidence": None,
        "covered": None,
        "agreement": None,
        "misses": [],
    }
