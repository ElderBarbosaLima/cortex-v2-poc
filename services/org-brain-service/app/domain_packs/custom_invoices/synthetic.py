"""Synthetic data for the custom_invoices pack (D18; docs/tasks.md, issue 6).

The ~1% of invoices that fall out of automation at a (fictitious) Japanese
seller. Writes into data/synthetic/custom_invoices/:

  customers.json      fictitious customers and the issuer
  cases.jsonl         40 invoice cases (the brief's format, extended)
  rules.json          initial rules, each with its CEL condition
  corrections.jsonl   reviewer corrections, with the rule they should become
  timeline.json       the ordered steps a harness replays
  expected.json       golden set: per case, the exact rules a correct pack holds,
                      which local rules are overridden, and verify outcomes

The golden set is computed from hand-written Python predicates, one per rule,
kept next to the CEL text but independent of any CEL engine; issue 11 adds a
test that both agree on every case.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.synthetic.common import (
    JsonObj,
    RuleSpec,
    corporate_number,
    expected_rules,
    rng_for,
    rule_document,
    tax_by_rate,
    write_json,
    write_jsonl,
    write_manifest,
)

WORKFLOW = "custom_invoices"
REQUEST_SCOPE = "jp/finance/q2c-invoicing"
QIS_START = dt.date(2023, 10, 1)

CUSTOMERS: tuple[JsonObj, ...] = (
    {"customer_id": "CUST-0017", "customer_name": "サンプル商事株式会社", "channel": "ariba"},
    {"customer_id": "CUST-0042", "customer_name": "架空電機株式会社", "channel": "coupa"},
    {"customer_id": "CUST-0063", "customer_name": "テスト物産株式会社", "channel": "email"},
    {"customer_id": "CUST-0088", "customer_name": "架空ソフトウェア合同会社", "channel": "coupa"},
    {"customer_id": "CUST-0105", "customer_name": "サンプル建設株式会社", "channel": "postal"},
    {"customer_id": "CUST-0121", "customer_name": "テスト食品株式会社", "channel": "email"},
)
ISSUER_NAME = "架空情報システム株式会社"

CATALOG_10 = (
    "保守サービス",
    "ソフトウェアライセンス",
    "クラウド利用料",
    "導入支援 作業費",
    "保守部品",
)
CATALOG_8 = ("飲料（軽減税率対象）",)

Invoice = dict[str, Any]


def _date(inv: Invoice) -> dt.date:
    return dt.date.fromisoformat(str(inv["issue_date"]))


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        "CI-G-QIS-REGNO",
        "jp",
        "mandatory",
        30,
        "適格請求書発行事業者の登録番号（T＋13桁）を必ず記載する。",
        ("invoice.registration_number",),
        'invoice.issue_date >= date("2023-10-01")',
        lambda inv: _date(inv) >= QIS_START,
    ),
    RuleSpec(
        "CI-G-QIS-TAX",
        "jp",
        "mandatory",
        30,
        "税率ごとに区分した消費税額の合計を記載する。",
        ("invoice.tax_breakdown",),
        'invoice.issue_date >= date("2023-10-01")',
        lambda inv: _date(inv) >= QIS_START,
    ),
    RuleSpec(
        "CI-G-NO-REGNO-OMIT",
        "jp",
        "prohibition",
        30,
        "登録番号を記載しない請求書は発行しない。",
        ("invoice.registration_number",),
        "true",
        lambda inv: True,
    ),
    # Scenario 2: the local instruction that must lose to the global prohibition.
    RuleSpec(
        "CI-L-SMALL-OMIT-REGNO",
        REQUEST_SCOPE,
        "default",
        20,
        "3万円未満の請求書では登録番号の記載を省略する。",
        ("invoice.registration_number",),
        "invoice.total < 30000",
        lambda inv: int(inv["total"]) < 30000,
    ),
    # Scenario 3: a contract clause no similarity search would surface for these cases.
    RuleSpec(
        "CI-C-0063-PAYTERMS",
        REQUEST_SCOPE,
        "mandatory",
        30,
        "契約第12条に基づき、支払条件「月末締め翌月末払い」を備考に記載する。",
        ("invoice.remarks.payment_terms",),
        'invoice.customer_id == "CUST-0063"',
        lambda inv: inv["customer_id"] == "CUST-0063",
        entities=("customer:CUST-0063",),
    ),
    # Scenario 4: a customer requirement withdrawn mid-run (retired in the timeline).
    RuleSpec(
        "CI-C-0017-DEPT",
        REQUEST_SCOPE,
        "mandatory",
        30,
        "Ariba 請求書には顧客指定の部門コード「D-77」を記載する。",
        ("invoice.remarks.department_code",),
        'invoice.customer_id == "CUST-0017" && invoice.channel == "ariba"',
        lambda inv: inv["customer_id"] == "CUST-0017" and inv["channel"] == "ariba",
        entities=("customer:CUST-0017",),
    ),
    # Never visible to tenant jp: proves tenant isolation in the golden set.
    RuleSpec(
        "OT-G-QIS-REGNO",
        "other",
        "mandatory",
        30,
        "（別テナント）登録番号を記載する。",
        ("invoice.registration_number",),
        "true",
        lambda inv: True,
        tenant="other",
    ),
)

# The rule the reviewer's correction should become (approved at step approve-0042).
LEARNED = RuleSpec(
    "CI-L-0042-COUPA",
    REQUEST_SCOPE,
    "mandatory",
    30,
    "架空電機（CUST-0042）の Coupa 請求書では、注文番号を備考欄に記載し、検収書を添付する。",
    ("invoice.remarks.po_number", "invoice.attachments.kenshusho"),
    'invoice.customer_id == "CUST-0042" && invoice.channel == "coupa"',
    lambda inv: inv["customer_id"] == "CUST-0042" and inv["channel"] == "coupa",
    entities=("customer:CUST-0042",),
)
RETIRED_KEY = "CI-C-0017-DEPT"


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    batch: str  # A: before learning · B: after approval · C: after retirement
    group: str
    customer_id: str
    channel: str | None = None  # default: the customer's usual channel
    size: str = "regular"  # regular | small | boundary | multi-rate
    issue_date: str | None = None
    pinned: Invoice | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


# The case from the brief, verbatim.
INV_0007: Invoice = {
    "case_id": "INV-0007",
    "customer_id": "CUST-0042",
    "customer_name": "架空電機株式会社",
    "channel": "coupa",
    "issue_date": "2026-12-03",
    "currency": "JPY",
    "lines": [{"desc": "保守サービス 12月分", "amount_excl_tax": 900000, "tax_rate": 10}],
    "tax_by_rate": {"10": 90000},
    "tax_total": 90000,
    "total": 990000,
    "po_number": "PO-88231",
    "attachments": [],
}

CASES: tuple[CaseSpec, ...] = (
    CaseSpec("INV-0001", "A", "happy", "CUST-0105"),
    CaseSpec("INV-0002", "A", "happy", "CUST-0121", size="multi-rate"),
    CaseSpec("INV-0003", "A", "happy", "CUST-0088"),
    CaseSpec("INV-0004", "A", "happy", "CUST-0105"),
    CaseSpec("INV-0005", "A", "happy", "CUST-0121", size="multi-rate"),
    CaseSpec("INV-0006", "A", "happy", "CUST-0088"),
    CaseSpec("INV-0007", "A", "learned_rule", "CUST-0042", pinned=INV_0007, tags=("correction",)),
    CaseSpec("INV-0008", "A", "scenario_2", "CUST-0105", size="small"),
    CaseSpec("INV-0009", "A", "scenario_2", "CUST-0121", size="small"),
    CaseSpec("INV-0010", "A", "scenario_3", "CUST-0063"),
    CaseSpec("INV-0011", "A", "scenario_4", "CUST-0017"),
    CaseSpec("INV-0012", "A", "learned_rule", "CUST-0042", tags=("correction",)),
    CaseSpec("INV-0013", "A", "unrelated", "CUST-0042", channel="email"),
    CaseSpec("INV-0014", "A", "scenario_3", "CUST-0063", size="multi-rate"),
    CaseSpec("INV-0015", "A", "scenario_4", "CUST-0017"),
    CaseSpec("INV-0016", "A", "scenario_2", "CUST-0105", size="small"),
    CaseSpec(
        "INV-0017", "A", "scenario_2", "CUST-0105", size="boundary", tags=("total_exactly_30000",)
    ),
    CaseSpec("INV-0018", "A", "happy", "CUST-0088"),
    CaseSpec("INV-0019", "A", "learned_rule", "CUST-0042", tags=("correction",)),
    CaseSpec(
        "INV-0020",
        "A",
        "unrelated",
        "CUST-0105",
        issue_date="2023-09-28",
        tags=("pre_qis_reissue",),
    ),
    CaseSpec("INV-0021", "B", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0022", "B", "unrelated", "CUST-0088"),
    CaseSpec("INV-0023", "B", "learned_rule", "CUST-0042", size="multi-rate"),
    CaseSpec("INV-0024", "B", "unrelated", "CUST-0042", channel="email"),
    CaseSpec("INV-0025", "B", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0026", "B", "unrelated", "CUST-0121"),
    CaseSpec("INV-0027", "B", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0028", "B", "unrelated", "CUST-0088"),
    CaseSpec("INV-0029", "B", "scenario_3", "CUST-0063"),
    CaseSpec("INV-0030", "B", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0031", "B", "unrelated", "CUST-0105"),
    CaseSpec("INV-0032", "B", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0033", "B", "scenario_4", "CUST-0017"),
    CaseSpec("INV-0034", "B", "unrelated", "CUST-0088"),
    CaseSpec("INV-0035", "C", "scenario_4", "CUST-0017", tags=("after_retirement",)),
    CaseSpec("INV-0036", "C", "scenario_4", "CUST-0017", tags=("after_retirement",)),
    CaseSpec("INV-0037", "C", "scenario_3", "CUST-0063"),
    CaseSpec("INV-0038", "C", "learned_rule", "CUST-0042"),
    CaseSpec("INV-0039", "C", "unrelated", "CUST-0121"),
    CaseSpec("INV-0040", "C", "scenario_2", "CUST-0121", size="small"),
)

# Issue dates per batch (business-event dates; packs are built "as of" them).
BATCH_DATES = {
    "A": (dt.date(2026, 11, 16), dt.date(2026, 12, 4)),
    "B": (dt.date(2026, 12, 7), dt.date(2026, 12, 18)),
    "C": (dt.date(2026, 12, 21), dt.date(2026, 12, 28)),
}
APPROVED_AT = "2026-12-05T10:00:00+09:00"
RETIRED_AT = "2026-12-19T10:00:00+09:00"
SEEDED_AT = "2026-11-01T09:00:00+09:00"

CORRECTIONS: tuple[tuple[str, str, str], ...] = (
    ("C-001", "INV-0007", "R-001"),
    ("C-002", "INV-0012", "R-002"),
    ("C-003", "INV-0019", "R-001"),
)


def _build_case(spec: CaseSpec, rng: random.Random) -> Invoice:
    if spec.pinned is not None:
        return dict(spec.pinned)
    customer = next(c for c in CUSTOMERS if c["customer_id"] == spec.customer_id)
    lo, hi = BATCH_DATES[spec.batch]
    issue = (
        spec.issue_date or (lo + dt.timedelta(days=rng.randrange((hi - lo).days + 1))).isoformat()
    )
    month = int(issue[5:7])
    if spec.size == "small":
        lines = [
            {
                "desc": f"{rng.choice(CATALOG_10)} {month}月分",
                "amount_excl_tax": rng.randrange(8, 25) * 1000,
                "tax_rate": 10,
            }
        ]
    elif spec.size == "boundary":  # 27,273 + floor(10%) 2,727 = 30,000: not below the threshold
        lines = [{"desc": f"保守部品 {month}月分", "amount_excl_tax": 27273, "tax_rate": 10}]
    else:
        lines = [
            {
                "desc": f"{rng.choice(CATALOG_10)} {month}月分",
                "amount_excl_tax": rng.randrange(80, 2000) * 1000,
                "tax_rate": 10,
            }
        ]
        if spec.size == "multi-rate":
            lines.append(
                {
                    "desc": CATALOG_8[0],
                    "amount_excl_tax": rng.randrange(5, 60) * 1000,
                    "tax_rate": 8,
                }
            )
    taxes = tax_by_rate(lines)
    channel = spec.channel or str(customer["channel"])
    return {
        "case_id": spec.case_id,
        "customer_id": spec.customer_id,
        "customer_name": customer["customer_name"],
        "channel": channel,
        "issue_date": issue,
        "currency": "JPY",
        "lines": lines,
        "tax_by_rate": {str(rate): amount for rate, amount in taxes.items()},
        "tax_total": sum(taxes.values()),
        "total": sum(int(line["amount_excl_tax"]) for line in lines) + sum(taxes.values()),
        "po_number": f"PO-{rng.randrange(10000, 99999)}" if channel in ("coupa", "ariba") else None,
        "attachments": [],
    }


def _active_rules(batch: str) -> list[RuleSpec]:
    active = [r for r in RULES if not (batch == "C" and r.key == RETIRED_KEY)]
    return [*active, LEARNED] if batch in ("B", "C") else active


def _resolve(case: Invoice, batch: str) -> tuple[list[str], dict[str, str]]:
    return expected_rules(
        _active_rules(batch),
        case,
        request_scope=REQUEST_SCOPE,
        entities={f"customer:{case['customer_id']}"},
    )


def _verify_expectations(spec: CaseSpec) -> list[JsonObj]:
    expectations: list[JsonObj] = [{"after": f"process-{spec.batch}", "result": "pass"}]
    if spec.batch == "A" and spec.customer_id == "CUST-0042" and spec.channel is None:
        expectations.append(
            {
                "after": "approve-0042",
                "result": "hold",
                "reason": "new_mandatory_rule",
                "rule": LEARNED.key,
            }
        )
    if spec.group == "scenario_4" and spec.batch in ("A", "B"):
        expectations.append(
            {
                "after": "retire-0017-dept",
                "result": "hold",
                "reason": "rule_no_longer_approved",
                "rule": RETIRED_KEY,
            }
        )
    return expectations


def generate(out_dir: Path) -> dict[str, int]:
    rng = rng_for(WORKFLOW)
    directory = out_dir / WORKFLOW
    cases = [_build_case(spec, rng) for spec in CASES]
    by_id = {c["case_id"]: c for c in cases}

    issuer_regno = f"T{corporate_number(rng)}"
    write_json(
        directory / "customers.json",
        {
            "issuer": {"name": ISSUER_NAME, "registration_number": issuer_regno},
            "customers": list(CUSTOMERS),
        },
    )
    write_jsonl(directory / "cases.jsonl", cases)
    write_json(directory / "rules.json", [rule_document(r, WORKFLOW) for r in RULES])

    corrections = [
        {
            "correction_id": cid,
            "case_id": case_id,
            "reviewer": reviewer,
            "corrected_at": f"{(dt.date.fromisoformat(by_id[case_id]['issue_date']) + dt.timedelta(days=1)).isoformat()}T15:00:00+09:00",
            "changes": [
                {
                    "target": "invoice.remarks.po_number",
                    "action": "set",
                    "before": None,
                    "after": f"注文番号: {by_id[case_id]['po_number']}",
                },
                {
                    "target": "invoice.attachments.kenshusho",
                    "action": "require",
                    "before": None,
                    "after": "検収書（Coupa に添付）",
                },
            ],
            "note_ja": "Coupa では注文番号を備考に入れ、検収書を添付しないと差し戻される。",
            "expected_rule": {
                **rule_document(LEARNED, WORKFLOW, status="proposed"),
                "enforcement": "default",
                "authority": 10,
                "asserted_by": {"type": "policy", "id": "correction-to-rule"},
                "approved_by": None,
                "derived_from_corrections": [c[0] for c in CORRECTIONS],
            },
        }
        for cid, case_id, reviewer in CORRECTIONS
    ]
    write_jsonl(directory / "corrections.jsonl", corrections)

    batches = {b: [s.case_id for s in CASES if s.batch == b] for b in ("A", "B", "C")}
    timeline: list[JsonObj] = [
        {"step": "seed", "at": SEEDED_AT, "rules": [r.key for r in RULES]},
        {"step": "process-A", "cases": batches["A"]},
        *({"step": f"correct-{cid}", "correction": cid} for cid, _, _ in CORRECTIONS),
        {
            "step": "approve-0042",
            "at": APPROVED_AT,
            "rule": LEARNED.key,
            "approver": "OWN-001",
            "enforcement": "mandatory",
            "authority": 30,
        },
        {"step": "process-B", "cases": batches["B"]},
        {
            "step": "retire-0017-dept",
            "at": RETIRED_AT,
            "rule": RETIRED_KEY,
            "by": "OWN-001",
            "reason_ja": "顧客の部門統合により部門コードの指定が廃止された。",
            "projection": "lagging",
        },
        {"step": "process-C", "cases": batches["C"]},
    ]
    write_json(
        directory / "timeline.json",
        {
            "request_scope": REQUEST_SCOPE,
            "consumer": "askq2c-agent",
            "purpose": "invoice_drafting",
            "steps": timeline,
            "thresholds": {"demo": [1, 1, 30], "strict": [3, 2, 30]},
        },
    )

    expected: list[JsonObj] = []
    for spec in CASES:
        rules, overridden = _resolve(by_id[spec.case_id], spec.batch)
        expected.append(
            {
                "case_id": spec.case_id,
                "batch": spec.batch,
                "group": spec.group,
                "tags": list(spec.tags),
                "as_of": by_id[spec.case_id]["issue_date"],
                "expected_rules": rules,
                "overridden": overridden,
                "verify": _verify_expectations(spec),
            }
        )
    write_json(directory / "expected.json", expected)

    counts = {"cases": len(cases), "rules": len(RULES), "corrections": len(corrections)}
    for spec in CASES:
        counts[f"group:{spec.group}"] = counts.get(f"group:{spec.group}", 0) + 1
    write_manifest(directory, WORKFLOW, counts)
    return counts
