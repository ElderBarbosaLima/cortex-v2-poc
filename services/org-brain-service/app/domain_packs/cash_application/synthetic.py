"""Synthetic data for the cash_application pack (D18; docs/tasks.md, issue 7).

Matching incoming bank payments to open invoices at a (fictitious) Japanese
seller. Writes into data/synthetic/cash_application/ the same files as the
custom_invoices pack, plus invoices.json (open receivables). Customers carry
their registered bank names in half-width kana, and each case states, by hand,
which customer its payer name should resolve to and how confidently: the
resolver (issue 26) is tested against that, not against itself.

Rule conditions see two variables: `payment` (with the resolved customer_id and
the account's garnishment flag filled in) and `invoice` (the open invoice the
payment is matched against). JPY amounts are integers.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

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

WORKFLOW = "cash_application"
REQUEST_SCOPE = "jp/finance/q2c-cash-application"
BANKS = ("サンプル銀行", "テスト信用金庫", "架空銀行")

# bank_names: what each customer is registered under at the bank (全銀 kana).
CUSTOMERS: tuple[JsonObj, ...] = (
    {
        "customer_id": "CUST-0017",
        "customer_name": "サンプル商事株式会社",
        "bank_names": ["ｶ)ｻﾝﾌﾟﾙｼﾖｳｼﾞ"],
        "garnishment_registered": False,
    },
    {
        "customer_id": "CUST-0042",
        "customer_name": "架空電機株式会社",
        "bank_names": ["ｶ)ｶｸｳﾃﾞﾝｷ"],
        "garnishment_registered": False,
    },
    {
        "customer_id": "CUST-0063",
        "customer_name": "テスト物産株式会社",
        "bank_names": ["ｶ)ﾃｽﾄﾌﾞﾂｻﾝ"],
        "garnishment_registered": False,
    },
    {
        "customer_id": "CUST-0088",
        "customer_name": "架空ソフトウェア合同会社",
        "bank_names": ["ﾄﾞ)ｶｸｳｿﾌﾄｳｴｱ"],
        "garnishment_registered": False,
    },
    {
        "customer_id": "CUST-0105",
        "customer_name": "サンプル建設株式会社",
        "bank_names": ["ｶ)ｻﾝﾌﾟﾙｹﾝｾﾂ"],
        "garnishment_registered": True,
    },
    {
        "customer_id": "CUST-0121",
        "customer_name": "テスト食品株式会社",
        "bank_names": ["ｶ)ﾃｽﾄｼﾖｸﾋﾝ"],
        "garnishment_registered": False,
    },
)
BY_CUSTOMER = {str(c["customer_id"]): c for c in CUSTOMERS}


def _diff(ctx: Context) -> int:
    return int(ctx["invoice"]["total"]) - int(ctx["payment"]["amount"])


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        "CA-G-EVIDENCE",
        "jp/finance",
        "mandatory",
        30,
        "消込の根拠として、入金明細と請求書番号を記録する。",
        ("match.evidence",),
        "true",
        lambda ctx: True,
    ),
    RuleSpec(
        "CA-G-GARNISH",
        "jp/finance",
        "mandatory",
        30,
        "差押登録のある口座からの入金は自動消込せず、担当者が確認する。",
        ("match.mode",),
        "payment.garnishment_registered == true",
        lambda ctx: bool(ctx["payment"]["garnishment_registered"]),
    ),
    RuleSpec(
        "CA-G-EXACT-AMOUNT",
        "jp/finance",
        "default",
        10,
        "請求額と入金額に差額がある場合は担当者が確認する。",
        ("match.difference",),
        "invoice.total != payment.amount",
        lambda ctx: _diff(ctx) != 0,
    ),
    # Scenario 2: a local shortcut that must lose to the global prohibition.
    RuleSpec(
        "CA-G-NO-WRITEOFF",
        "jp/finance",
        "prohibition",
        30,
        "差額を担当者の確認なしに雑損失として処理しない。",
        ("match.difference.writeoff",),
        "invoice.total != payment.amount",
        lambda ctx: _diff(ctx) != 0,
    ),
    RuleSpec(
        "CA-L-SMALL-WRITEOFF",
        REQUEST_SCOPE,
        "default",
        20,
        "1,000円未満の差額は雑損失として自動処理し、消込する。",
        ("match.difference.writeoff",),
        "invoice.total - payment.amount > 0 && invoice.total - payment.amount < 1000",
        lambda ctx: 0 < _diff(ctx) < 1000,
    ),
    # Scenario 3: a contract clause that shares nothing with a payment's text.
    RuleSpec(
        "CA-C-0063-ALLOC",
        REQUEST_SCOPE,
        "mandatory",
        30,
        "契約第8条に基づき、前受金残高から先に充当する。",
        ("match.allocation_order",),
        'payment.customer_id == "CUST-0063"',
        lambda ctx: ctx["payment"]["customer_id"] == "CUST-0063",
        entities=("customer:CUST-0063",),
    ),
    # Scenario 4: a credit hold lifted mid-run (retired in the timeline).
    RuleSpec(
        "CA-C-0017-HOLD",
        REQUEST_SCOPE,
        "mandatory",
        30,
        "与信管理部の承認があるまで、サンプル商事の入金は消込しない。",
        ("match.mode",),
        'payment.customer_id == "CUST-0017"',
        lambda ctx: ctx["payment"]["customer_id"] == "CUST-0017",
        entities=("customer:CUST-0017",),
    ),
    RuleSpec(
        "OT-CA-GARNISH",
        "other",
        "mandatory",
        30,
        "（別テナント）差押口座の入金は確認する。",
        ("match.mode",),
        "true",
        lambda ctx: True,
        tenant="other",
    ),
)

# The brief's rule: CUST-0042 deducts the bank transfer fee (振込手数料) up to ¥880.
# `invoice.total > payment.amount` is added to the brief's condition so an
# overpayment (negative difference) is never booked as a fee.
LEARNED = RuleSpec(
    "CA-L-0042-FEE",
    REQUEST_SCOPE,
    "default",
    20,
    "架空電機（CUST-0042）は振込手数料（880円以下）を差し引いて入金する。全額入金として扱い、差額を支払手数料に計上する。",
    ("match.difference",),
    'payment.customer_id == "CUST-0042" && invoice.total > payment.amount && invoice.total - payment.amount <= 880',
    lambda ctx: ctx["payment"]["customer_id"] == "CUST-0042" and 0 < _diff(ctx) <= 880,
    entities=("customer:CUST-0042",),
)
RETIRED_KEY = "CA-C-0017-HOLD"


@dataclass(frozen=True)
class CaseSpec:
    payment_id: str
    batch: str
    group: str
    customer_id: str | None  # whose invoice is open (the truth); None = unknown payer
    payer_name_raw: str
    resolves_to: str | None  # expected resolver output
    band: str  # high | low | ambiguous | none
    evidence: str
    difference: int = 0  # invoice.total - payment.amount (negative = overpayment)
    pinned: JsonObj | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def _hi(
    pid: str,
    batch: str,
    group: str,
    cust: str,
    diff: int = 0,
    *,
    name: str | None = None,
    evidence: str = "exact",
    tags: tuple[str, ...] = (),
) -> CaseSpec:
    raw = name or str(BY_CUSTOMER[cust]["bank_names"][0])
    return CaseSpec(pid, batch, group, cust, raw, cust, "high", evidence, diff, tags=tags)


PAY_0113: JsonObj = {
    "payment_id": "PAY-0113", "value_date": "2026-12-10", "amount": 989340, "currency": "JPY",
    "payer_name_raw": "ｶ)ｶｸｳﾃﾞﾝｷ", "bank": "サンプル銀行", "open_invoices": ["INV-0007"],
}  # fmt: skip

CASES: tuple[CaseSpec, ...] = (
    _hi("PAY-0101", "A", "happy", "CUST-0042"),
    _hi("PAY-0102", "A", "happy", "CUST-0088"),
    _hi("PAY-0103", "A", "happy", "CUST-0121"),
    _hi("PAY-0104", "A", "happy", "CUST-0042", name="ｶｸｳﾃﾞﾝｷ(ｶ", evidence="corporate_suffix_form"),
    _hi("PAY-0105", "A", "happy", "CUST-0088"),
    _hi("PAY-0106", "A", "happy", "CUST-0121"),
    _hi("PAY-0107", "A", "happy", "CUST-0042"),
    _hi("PAY-0108", "A", "garnishment", "CUST-0105"),
    _hi("PAY-0109", "A", "garnishment", "CUST-0105", diff=500),
    _hi("PAY-0110", "A", "scenario_2", "CUST-0121", diff=300),
    _hi("PAY-0111", "A", "scenario_2", "CUST-0088", diff=999),
    _hi("PAY-0112", "A", "scenario_2", "CUST-0121", diff=1000, tags=("difference_exactly_1000",)),
    CaseSpec("PAY-0113", "A", "learned_rule", "CUST-0042", "ｶ)ｶｸｳﾃﾞﾝｷ", "CUST-0042", "high", "exact", 660, pinned=PAY_0113, tags=("correction",)),
    _hi("PAY-0114", "A", "learned_rule", "CUST-0042", diff=440, tags=("correction",)),
    _hi("PAY-0115", "A", "learned_rule", "CUST-0042", diff=880, tags=("correction", "fee_exactly_880")),
    _hi("PAY-0116", "A", "resolution", "CUST-0042", name="ｶ)ｶｸｳﾃﾞﾝ", evidence="truncated_prefix"),
    CaseSpec("PAY-0117", "A", "resolution", "CUST-0017", "ｶ)ｻﾝﾌﾟﾙ", None, "ambiguous", "prefix_matches_several"),
    CaseSpec("PAY-0118", "A", "resolution", "CUST-0121", "ｶ)ﾃｽﾄｼﾖｸﾋﾑ", "CUST-0121", "low", "edit_distance_1"),
    CaseSpec("PAY-0119", "A", "resolution", None, "ｶ)ﾐﾄｳﾛｸｼﾔ", None, "none", "no_registered_name"),
    _hi("PAY-0120", "A", "scenario_3", "CUST-0063"),
    _hi("PAY-0121", "A", "scenario_3", "CUST-0063", diff=200),
    _hi("PAY-0122", "A", "scenario_4", "CUST-0017"),
    _hi("PAY-0123", "A", "scenario_4", "CUST-0017"),
    _hi("PAY-0124", "A", "garnishment", "CUST-0105"),
    _hi("PAY-0125", "B", "learned_rule", "CUST-0042", diff=660),
    _hi("PAY-0126", "B", "learned_rule", "CUST-0042", diff=880, tags=("fee_exactly_880",)),
    _hi("PAY-0127", "B", "learned_rule", "CUST-0042", diff=881, tags=("fee_over_limit",)),
    _hi("PAY-0128", "B", "learned_rule", "CUST-0042", diff=0, tags=("no_difference",)),
    _hi("PAY-0129", "B", "unrelated", "CUST-0088", diff=660, tags=("same_fee_other_customer",)),
    _hi("PAY-0130", "B", "unrelated", "CUST-0088", diff=440, tags=("same_fee_other_customer",)),
    _hi("PAY-0131", "B", "unrelated", "CUST-0042", diff=-1000, tags=("overpayment",)),
    _hi("PAY-0132", "B", "scenario_3", "CUST-0063"),
    _hi("PAY-0133", "B", "scenario_4", "CUST-0017"),
    _hi("PAY-0134", "B", "garnishment", "CUST-0105", diff=660),
    _hi("PAY-0135", "C", "scenario_4", "CUST-0017", tags=("after_retirement",)),
    _hi("PAY-0136", "C", "scenario_4", "CUST-0017", tags=("after_retirement",)),
    _hi("PAY-0137", "C", "learned_rule", "CUST-0042", diff=660, name="ｶ)ｶｸｳﾃﾞﾝ", evidence="truncated_prefix"),
    _hi("PAY-0138", "C", "scenario_2", "CUST-0088", diff=120),
    _hi("PAY-0139", "C", "unrelated", "CUST-0121", diff=660, tags=("same_fee_other_customer",)),
    _hi("PAY-0140", "C", "unrelated", "CUST-0063", diff=0),
)  # fmt: skip

BATCH_DATES = {
    "A": (dt.date(2026, 12, 8), dt.date(2026, 12, 12)),
    "B": (dt.date(2026, 12, 15), dt.date(2026, 12, 18)),
    "C": (dt.date(2026, 12, 21), dt.date(2026, 12, 24)),
}
SEEDED_AT = "2026-11-01T09:00:00+09:00"
APPROVED_AT = "2026-12-14T10:00:00+09:00"
RETIRED_AT = "2026-12-19T10:00:00+09:00"
CORRECTIONS = (
    ("C-101", "PAY-0113", "R-101"),
    ("C-102", "PAY-0114", "R-102"),
    ("C-103", "PAY-0115", "R-101"),
)


def _active_rules(batch: str) -> list[RuleSpec]:
    active = [r for r in RULES if not (batch == "C" and r.key == RETIRED_KEY)]
    return [*active, LEARNED] if batch in ("B", "C") else active


def _route(spec: CaseSpec, rules: list[str]) -> str:
    """auto only when the payer is confidently known, nothing forces review, and
    any difference is explained by an applied rule."""
    if spec.band != "high" or {"CA-G-GARNISH", RETIRED_KEY} & set(rules):
        return "review"
    if spec.difference != 0 and LEARNED.key not in rules:
        return "review"
    return "auto"


def generate(out_dir: Path) -> dict[str, int]:
    rng = rng_for(WORKFLOW)
    directory = out_dir / WORKFLOW
    payments: list[JsonObj] = []
    invoices: list[JsonObj] = []
    expected: list[JsonObj] = []

    for n, spec in enumerate(CASES):
        if spec.pinned is not None:
            payment, invoice = (
                dict(spec.pinned),
                {
                    "invoice_id": "INV-0007",
                    "customer_id": "CUST-0042",
                    "total": 990000,
                    "issue_date": "2026-12-03",
                },
            )
        else:
            lo, hi = BATCH_DATES[spec.batch]
            value_date = (lo + dt.timedelta(days=rng.randrange((hi - lo).days + 1))).isoformat()
            total = rng.randrange(50, 3000) * 1100  # tax-inclusive totals, like the invoices
            invoice = {
                "invoice_id": f"INV-{1001 + n}",
                "customer_id": spec.customer_id,
                "total": total,
                "issue_date": "2026-11-30",
            }
            payment = {
                "payment_id": spec.payment_id, "value_date": value_date, "amount": total - spec.difference,
                "currency": "JPY", "payer_name_raw": spec.payer_name_raw, "bank": rng.choice(BANKS),
                "open_invoices": [invoice["invoice_id"]],
            }  # fmt: skip
        payments.append(payment)
        invoices.append(invoice)

        resolved = spec.resolves_to if spec.band == "high" else None
        account = BY_CUSTOMER.get(resolved or "", {})
        ctx: Context = {
            "payment": {
                **payment,
                "customer_id": resolved,
                "garnishment_registered": bool(account.get("garnishment_registered", False)),
            },
            "invoice": invoice,
        }
        rules, overridden = expected_rules(
            _active_rules(spec.batch), ctx, request_scope=REQUEST_SCOPE,
            entities={f"customer:{resolved}"} if resolved else set(),
        )  # fmt: skip
        verify: list[JsonObj] = [{"after": f"process-{spec.batch}", "result": "pass"}]
        if RETIRED_KEY in rules and spec.batch in ("A", "B"):
            verify.append(
                {
                    "after": "retire-0017-hold",
                    "result": "hold",
                    "reason": "rule_no_longer_approved",
                    "rule": RETIRED_KEY,
                }
            )
        expected.append({
            "case_id": spec.payment_id, "batch": spec.batch, "group": spec.group, "tags": list(spec.tags),
            "as_of": payment["value_date"],
            "resolution": {"payer_name_raw": spec.payer_name_raw, "expected_entity": f"customer:{spec.resolves_to}" if spec.resolves_to else None,
                           "band": spec.band, "evidence": spec.evidence},
            "expected_rules": rules, "overridden": overridden, "route": _route(spec, rules), "verify": verify,
        })  # fmt: skip

    write_json(directory / "customers.json", {"customers": list(CUSTOMERS)})
    write_json(directory / "invoices.json", invoices)
    write_jsonl(directory / "cases.jsonl", payments)
    write_json(directory / "rules.json", [rule_document(r, WORKFLOW) for r in RULES])
    by_id = {p["payment_id"]: p for p in payments}
    corrections = [
        {
            "correction_id": cid, "case_id": pid, "reviewer": reviewer,
            "corrected_at": f"{(dt.date.fromisoformat(by_id[pid]['value_date']) + dt.timedelta(days=1)).isoformat()}T15:00:00+09:00",
            "changes": [{"target": "match.difference", "action": "set", "before": "担当者確認（差額あり）",
                         "after": f"全額入金として消込、差額 {next(c.difference for c in CASES if c.payment_id == pid)} 円を支払手数料に計上"}],
            "note_ja": "架空電機は毎回振込手数料を差し引いて入金してくる。",
            "expected_rule": {**rule_document(LEARNED, WORKFLOW, status="proposed"), "authority": 10,
                              "asserted_by": {"type": "policy", "id": "correction-to-rule"}, "approved_by": None,
                              "derived_from_corrections": [c[0] for c in CORRECTIONS]},
        }
        for cid, pid, reviewer in CORRECTIONS
    ]  # fmt: skip
    write_jsonl(directory / "corrections.jsonl", corrections)
    batches = {b: [s.payment_id for s in CASES if s.batch == b] for b in ("A", "B", "C")}
    steps: list[JsonObj] = [
        {"step": "seed", "at": SEEDED_AT, "rules": [r.key for r in RULES]},
        {"step": "process-A", "cases": batches["A"]},
        *({"step": f"correct-{cid}", "correction": cid} for cid, _, _ in CORRECTIONS),
        {"step": "approve-0042-fee", "at": APPROVED_AT, "rule": LEARNED.key, "approver": "OWN-001", "enforcement": "default", "authority": 20},
        {"step": "process-B", "cases": batches["B"]},
        {"step": "retire-0017-hold", "at": RETIRED_AT, "rule": RETIRED_KEY, "by": "OWN-001",
         "reason_ja": "与信管理部が与信保留を解除した。", "projection": "lagging"},
        {"step": "process-C", "cases": batches["C"]},
    ]  # fmt: skip
    write_json(directory / "timeline.json", {"request_scope": REQUEST_SCOPE, "consumer": "askq2c-agent", "purpose": "cash_matching",
                                            "steps": steps, "thresholds": {"demo": [1, 1, 30], "strict": [3, 2, 30]}})  # fmt: skip
    write_json(directory / "expected.json", expected)

    counts = {"cases": len(payments), "rules": len(RULES), "corrections": len(corrections)}
    for spec in CASES:
        counts[f"group:{spec.group}"] = counts.get(f"group:{spec.group}", 0) + 1
    write_manifest(directory, WORKFLOW, counts)
    return counts
