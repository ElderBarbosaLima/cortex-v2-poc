"""cel-expr-python, characterised (issue 11 acceptance 1–5).

These pin the behaviours the PoC relies on but the library's README does not
document. If a version bump changes one of them, this file fails first.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.conditions import ConditionCompileError, ConditionEvalError
from app.conditions.cel import CelExprEvaluator

cel = CelExprEvaluator()
VARS = ("invoice", "payment")
INVOICE: dict[str, Any] = {
    "customer_id": "CUST-0042",
    "channel": "coupa",
    "issue_date": dt.date(2026, 12, 3),
    "total": 990000,
    "lines": [{"desc": "保守サービス 12月分", "amount_excl_tax": 900000, "tax_rate": 10}],
}


def check(
    expr: str, invoice: dict[str, Any] | None = None, payment: dict[str, Any] | None = None
) -> bool:
    return cel.evaluate(
        cel.compile(expr, variables=VARS),
        {"invoice": INVOICE if invoice is None else invoice, "payment": payment or {}},
    )


def test_1_nested_dicts() -> None:
    expr = 'invoice.customer_id == "CUST-0042" && invoice.channel == "coupa"'

    assert check(expr)
    assert not check(expr, {**INVOICE, "channel": "email"})
    assert check('invoice.lines[0].desc == "保守サービス 12月分"')


@pytest.mark.parametrize(
    ("issue_date", "expected"),
    [(dt.date(2023, 9, 30), False), (dt.date(2023, 10, 1), True), (dt.date(2026, 12, 3), True)],
)
def test_2_date_comparison(issue_date: dt.date, expected: bool) -> None:
    assert (
        check('invoice.issue_date >= date("2023-10-01")', {**INVOICE, "issue_date": issue_date})
        is expected
    )


def test_2_date_difference_is_in_days() -> None:
    assert check(
        "payment.value_date - invoice.issue_date == 7",
        payment={"value_date": dt.date(2026, 12, 10)},
    )


@pytest.mark.parametrize(("amount", "expected"), [(989340, True), (989119, False)])
def test_3_fee_condition(amount: int, expected: bool) -> None:
    expr = 'payment.customer_id == "CUST-0042" && invoice.total - payment.amount <= 880'

    assert check(expr, payment={"customer_id": "CUST-0042", "amount": amount}) is expected


def test_3_int64_overflow_is_an_error_never_a_wrap() -> None:
    with pytest.raises(ConditionEvalError) as err:
        check("invoice.total + 1 > 0", {**INVOICE, "total": 2**63 - 1})

    assert err.value.code == "OUT_OF_RANGE"


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("invoicex.total > 0", "undeclared reference"),
        ("nosuchfn(invoice.total)", "undeclared reference"),
        ("invoice.total >", "Syntax error"),
        ('invoice.issue_date >= date("2023-13-01")', "is not a YYYY-MM-DD date"),
        ("date(1) > 0", "no matching overload"),
        ("invoice.total", "must be boolean"),
    ],
)
def test_4_bad_conditions_fail_at_compile_time(expr: str, fragment: str) -> None:
    with pytest.raises(ConditionCompileError, match=fragment):
        cel.compile(expr, variables=VARS)


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (2.5, "is a float"),
        (dt.datetime(2026, 12, 3, tzinfo=dt.UTC), "is a datetime"),
        (2**63, "does not fit in int64"),
    ],
)
def test_5_unsafe_values_are_refused_before_evaluation(value: Any, fragment: str) -> None:
    with pytest.raises(ConditionEvalError, match=fragment):
        check("invoice.total > 0", {**INVOICE, "total": value})


def test_missing_field_null_and_missing_variable() -> None:
    with pytest.raises(ConditionEvalError) as missing:
        check('invoice.department == "D-77"')
    assert missing.value.code == "NOT_FOUND"

    assert not check('payment.customer_id == "CUST-0063"', payment={"customer_id": None})

    compiled = cel.compile("invoice.total > 0", variables=VARS)
    with pytest.raises(ConditionEvalError, match="context lacks"):
        cel.evaluate(compiled, {"invoice": INVOICE})


def test_compiled_conditions_are_reusable() -> None:
    compiled = cel.compile("invoice.total >= 30000", variables=VARS)

    results = [
        cel.evaluate(compiled, {"invoice": {"total": t}, "payment": {}}) for t in (29999, 30000)
    ]

    assert results == [False, True]
    assert compiled.variables == ("invoice", "payment")
