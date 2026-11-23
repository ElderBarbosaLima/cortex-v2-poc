"""Synthetic data and golden set for cash_allocation (issues 40–41).

Fictitious customers, one rule per exception type IBM listed (slide 16), and
payments in the shape IBM's AI sends (slide 22). Deterministic. The golden set
states, for each payment, exactly which rules a correct access gate returns,
computed by an independent Python predicate per rule (common.expected_rules).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from app.synthetic.common import (
    Context,
    JsonObj,
    RuleSpec,
    expected_rules,
    rng_for,
    rule_document,
    write_json,
    write_jsonl,
    write_manifest,
)

WORKFLOW = "cash_allocation"
REQUEST_SCOPE = "jp/finance/q2c-cash-application"
FEE_LIMIT = 880

CUSTOMERS: tuple[JsonObj, ...] = (
    {"customer_id": "CUST-0101", "name": "山田商事株式会社（架空）"},
    {"customer_id": "CUST-0102", "name": "サンプル電機株式会社（架空）"},
    {"customer_id": "CUST-0103", "name": "テスト工業株式会社（架空）"},
    {"customer_id": "CUST-0104", "name": "架空物産株式会社"},
    {"customer_id": "CUST-0105", "name": "サンプル建設株式会社（架空）"},
)


def _gap(ctx: Context) -> int:
    return int(ctx["payment"]["gap"])


def _is(ctx: Context, customer: str) -> bool:
    return ctx["payment"]["customer_id"] == customer


RULES: tuple[RuleSpec, ...] = (
    # Group-wide (the Japan tenant root's finance node): apply to every customer.
    RuleSpec(
        "AL-G-TAX-ROUNDING",
        "jp/finance",
        "default",
        20,
        "合計が消費税の端数処理で ±1 円ずれる場合は、端数として処理してよい（切り捨て）。",
        ("allocation.tax_difference",),
        "payment.gap != 0 && payment.gap >= -1 && payment.gap <= 1",
        lambda ctx: _gap(ctx) != 0 and -1 <= _gap(ctx) <= 1,
        body_extra={
            "exception": "tax_difference",
            "engine": {"rounding": "floor", "tolerance": 1, "treatment": "writeoff"},
        },
    ),
    RuleSpec(
        "AL-G-DUPLICATE-HOLD",
        "jp/finance",
        "mandatory",
        30,
        "同じ請求書への二重入金は自動で消し込まず、保留して担当者が返金か次回請求への充当を決める。",
        ("allocation.duplicate_payment",),
        "payment.candidates_total > 0 && payment.amount >= 2 * payment.candidates_total",
        lambda ctx: (
            int(ctx["payment"]["candidates_total"]) > 0
            and int(ctx["payment"]["amount"]) >= 2 * int(ctx["payment"]["candidates_total"])
        ),
        body_extra={"exception": "duplicate_payment", "engine": {"action": "hold"}},
    ),
    # Customer rules (one owner per customer later: issue 45).
    RuleSpec(
        "AL-C-0101-FEE",
        "jp/finance/q2c-cash-application",
        "default",
        20,
        "山田商事は振込手数料を差し引いて支払う。通常は 660 円不足。"
        "880 円を超える不足は手数料ではない可能性があるため、消費税の差額を確認し、不明なら担当者へ回す。",
        ("allocation.bank_fee",),
        f'payment.customer_id == "CUST-0101" && payment.gap > 0 && payment.gap <= {FEE_LIMIT}',
        lambda ctx: _is(ctx, "CUST-0101") and 0 < _gap(ctx) <= FEE_LIMIT,
        entities=("customer:CUST-0101",),
        body_extra={
            "exception": "bank_fee",
            "engine": {"expected_fee": 660, "max_fee": 880, "treatment": "fee_expense"},
        },
    ),
    RuleSpec(
        "AL-C-0102-COMBINED",
        "jp/finance/q2c-cash-application",
        "default",
        20,
        "サンプル電機は複数の請求書を一括で支払う。送金明細の注文番号（PO）で請求書を選ぶ。",
        ("allocation.combined_payment",),
        'payment.customer_id == "CUST-0102" && payment.candidate_count > 1',
        lambda ctx: _is(ctx, "CUST-0102") and int(ctx["payment"]["candidate_count"]) > 1,
        entities=("customer:CUST-0102",),
        body_extra={"exception": "combined_payment", "engine": {"pick": "po_number"}},
    ),
    RuleSpec(
        "AL-C-0103-PARTIAL",
        "jp/finance/q2c-cash-application",
        "default",
        20,
        "テスト工業は大口請求を 3 回の分割で支払う契約。不足分は残高として残し、督促しない。",
        ("allocation.partial_payment",),
        'payment.customer_id == "CUST-0103" && payment.gap > 0',
        lambda ctx: _is(ctx, "CUST-0103") and _gap(ctx) > 0,
        entities=("customer:CUST-0103",),
        body_extra={
            "exception": "partial_payment",
            "engine": {"action": "instalments", "instalments": 3},
        },
    ),
    RuleSpec(
        "AL-C-0104-FEE-HOLD",
        "jp/finance/q2c-cash-application",
        "default",
        20,
        "架空物産の不足額は手数料として処理せず、担当者が確認する（過去に手数料と誤認した例がある）。",
        ("allocation.bank_fee",),
        f'payment.customer_id == "CUST-0104" && payment.gap > 0 && payment.gap <= {FEE_LIMIT}',
        lambda ctx: _is(ctx, "CUST-0104") and 0 < _gap(ctx) <= FEE_LIMIT,
        entities=("customer:CUST-0104",),
        body_extra={
            "exception": "bank_fee",
            "engine": {"expected_fee": 440, "max_fee": 880, "treatment": "hold_for_review"},
        },
    ),
)

# (payment id, customer, candidate totals, amount paid, remittance note or None)
_SHAPES: tuple[tuple[str, str, int, tuple[int, ...], int], ...] = (
    ("PAY-A001", "CUST-0101", 1, (550000,), 549340),  # ¥660 short: the fee rule
    ("PAY-A002", "CUST-0101", 1, (220000,), 220000),  # exact: nothing special
    ("PAY-A003", "CUST-0101", 1, (330000,), 328900),  # ¥1,100 short: beyond a fee
    ("PAY-A004", "CUST-0102", 3, (110000, 220000, 55000), 385000),  # one transfer, three invoices
    ("PAY-A005", "CUST-0102", 1, (99000,), 99001),  # ¥1 over: tax rounding
    ("PAY-A006", "CUST-0103", 1, (3300000,), 1100000),  # a third now
    ("PAY-A007", "CUST-0104", 1, (440000,), 439560),  # ¥440 short, but hold
    ("PAY-A008", "CUST-0105", 1, (88000,), 176000),  # paid twice
    ("PAY-A009", "CUST-0105", 1, (77000,), 76999),  # ¥1 short: tax rounding
    ("PAY-A010", "CUST-0103", 1, (66000,), 66000),  # exact
)


def _case(n: int, shape: tuple[str, str, int, tuple[int, ...], int], rng: Any) -> JsonObj:
    payment_id, customer, _, totals, amount = shape
    day = dt.date(2026, 12, 1) + dt.timedelta(days=rng.randrange(20))
    invoices = [
        {
            "invoice_id": f"INV-A{100 + n * 10 + i}",
            "total": total,
            "issue_date": (day - dt.timedelta(days=30 + i * 10)).isoformat(),
            "po_number": f"PO-{7000 + n * 10 + i}",
        }
        for i, total in enumerate(totals)
    ]
    return {
        "case_id": payment_id,
        "customer_id": customer,
        "payment": {
            "payment_id": payment_id,
            "amount": amount,
            "value_date": day.isoformat(),
            "currency": "JPY",
        },
        "candidate_invoices": invoices,
        "gap": sum(totals) - amount,
        "remittance": {"invoice_refs": [i["invoice_id"] for i in invoices], "note": ""}
        if len(invoices) > 1
        else None,
    }


def generate(out_dir: Path) -> dict[str, int]:
    rng = rng_for(WORKFLOW)
    directory = out_dir / WORKFLOW
    cases = [_case(n, shape, rng) for n, shape in enumerate(_SHAPES)]
    expected: list[JsonObj] = []
    for case in cases:
        ctx: Context = {
            "payment": {
                "customer_id": case["customer_id"],
                "gap": case["gap"],
                "candidate_count": len(case["candidate_invoices"]),
                "candidates_total": sum(i["total"] for i in case["candidate_invoices"]),
                "amount": case["payment"]["amount"],
            }
        }
        rules, overridden = expected_rules(
            RULES, ctx, request_scope=REQUEST_SCOPE, entities={f"customer:{case['customer_id']}"}
        )
        expected.append(
            {
                "case_id": case["case_id"],
                "as_of": case["payment"]["value_date"],
                "expected_rules": rules,
                "overridden": overridden,
            }
        )
    write_json(directory / "customers.json", {"customers": list(CUSTOMERS)})
    write_jsonl(directory / "cases.jsonl", cases)
    write_json(directory / "rules.json", [rule_document(r, WORKFLOW) for r in RULES])
    write_json(directory / "expected.json", expected)
    counts = {"customers": len(CUSTOMERS), "cases": len(cases), "rules": len(RULES)}
    write_manifest(directory, WORKFLOW, counts)
    return counts
