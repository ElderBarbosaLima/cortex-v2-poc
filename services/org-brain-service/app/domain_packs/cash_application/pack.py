"""The cash_application domain pack (D18, second possibility; issue 26).

A case is a bank payment and the open invoice the agent proposes to match it
with: `{"payment": {...}, "invoice": {...}}`.
- entities: `payer:<opaque id>` (from the normalised bank name; never the name
  itself) and `customer:<id>`, only when the payer resolves with `high`
  confidence (resolver.py). Ambiguous, low or unknown payers name no customer:
  their rules for "any payment" still apply, customer rules do not, and the
  route is review;
- CEL variables `payment` (amount, value date, the resolved customer and the
  account's garnishment flag) and `invoice` (total, issue date); int JPY;
- the bank transfer fee (振込手数料) a customer deducts is at most ¥880, the
  limit a learned fee rule uses;
- `check_output` checks the proposed match against the rules: a review rule
  (target `match.mode`, e.g. 差押 garnishment) forbids an automatic match, a
  prohibition on write-offs forbids writing the difference off, the evidence
  must name the payment and the invoice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain_packs.base import EntityMatch, RuleBody, RuleDraft
from app.domain_packs.cash_application.resolver import Resolution, resolve
from app.domain_packs.cash_application.synthetic import CUSTOMERS

FEE_LIMIT = 880  # the largest bank transfer fee a payer deducts (JPY)
TARGETS = frozenset(
    {
        "match.evidence",
        "match.mode",
        "match.difference",
        "match.difference.writeoff",
        "match.allocation_order",
    }
)
_REGISTERED = MappingProxyType(
    {str(c["customer_id"]): tuple(str(n) for n in c["bank_names"]) for c in CUSTOMERS}
)
_GARNISHMENT = MappingProxyType(
    {str(c["customer_id"]): bool(c["garnishment_registered"]) for c in CUSTOMERS}
)


class MatchRuleBody(RuleBody):
    """A rule about matching payments. `requirements` names how a target must
    be treated (e.g. book the difference as a fee), keyed by target."""

    requirements: dict[str, str] = Field(default_factory=dict[str, str])


class MatchChange(BaseModel):
    target: str
    action: Literal["set", "require", "remove"]
    before: str | None = None
    after: str | None = None


class MatchCorrection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    correction_id: str
    case_id: str
    reviewer: str
    corrected_at: dt.datetime
    changes: list[MatchChange] = Field(min_length=1)
    note_ja: str = ""


def payer_key(normalised: str) -> str:
    return "payer:" + hashlib.sha256(normalised.encode()).hexdigest()[:16]


def resolution(case: Mapping[str, Any]) -> Resolution:
    return resolve(str(case["payment"]["payer_name_raw"]), _REGISTERED)


def difference(case: Mapping[str, Any]) -> int:
    return int(case["invoice"]["total"]) - int(case["payment"]["amount"])


class CashApplicationPack:
    name = "cash_application"
    entity_types = frozenset({"customer", "payer"})
    condition_variables = ("payment", "invoice")
    condition_fields = MappingProxyType(
        {
            "payment": frozenset(
                {
                    "payment_id",
                    "amount",
                    "value_date",
                    "currency",
                    "bank",
                    "customer_id",
                    "garnishment_registered",
                }
            ),
            "invoice": frozenset({"invoice_id", "customer_id", "total", "issue_date"}),
        }
    )
    entity_fields = MappingProxyType(
        {"payment.customer_id": "customer", "invoice.customer_id": "customer"}
    )
    targets = TARGETS
    rule_body_schema = MatchRuleBody
    correction_schema = MatchCorrection

    def resolve_entities(self, case: Mapping[str, Any]) -> list[EntityMatch]:
        r = resolution(case)
        evidence = {"payer_name": "normalised", "rule": r.evidence, "candidates": r.candidates}
        return [
            EntityMatch(payer_key(r.normalised), "high", {"source": "payer_name_raw"}),
            EntityMatch(
                f"customer:{r.customer_id}" if r.customer_id else None,
                r.band,
                evidence,
                tuple(f"customer:{c}" for c in r.candidates),
            ),
        ]

    def condition_context(
        self, case: Mapping[str, Any], entities: Sequence[EntityMatch]
    ) -> dict[str, Any]:
        payment, invoice = case["payment"], case["invoice"]
        customer = next(
            (m.key.split(":", 1)[1] for m in entities if m.key and m.key.startswith("customer:")),
            "",
        )
        return {
            "payment": {
                "payment_id": str(payment["payment_id"]),
                "amount": int(payment["amount"]),
                "value_date": dt.date.fromisoformat(str(payment["value_date"])),
                "currency": str(payment.get("currency", "JPY")),
                "bank": str(payment.get("bank", "")),
                "customer_id": customer,  # "" when the payer is not confidently known
                "garnishment_registered": _GARNISHMENT.get(customer, False),
            },
            "invoice": {
                "invoice_id": str(invoice["invoice_id"]),
                "customer_id": str(invoice.get("customer_id") or ""),
                "total": int(invoice["total"]),
                "issue_date": dt.date.fromisoformat(str(invoice["issue_date"])),
            },
        }

    def correction_to_rule(self, correction: BaseModel, *, case: Mapping[str, Any]) -> RuleDraft:
        c = MatchCorrection.model_validate(correction.model_dump())
        customer = resolution(case).customer_id
        if customer is None:
            raise ValueError("a rule is learned only for a payer resolved with high confidence")
        targets = sorted({ch.target for ch in c.changes if ch.target in TARGETS})
        body = {
            "text_ja": f"{customer} について、{c.note_ja}"
            if c.note_ja
            else f"{customer} の消込ルール",
            "targets": targets,
            # The difference a fee explains: a shortfall up to the fee limit,
            # never an overpayment.
            "condition": (
                f'payment.customer_id == "{customer}" && invoice.total > payment.amount'
                f" && invoice.total - payment.amount <= {FEE_LIMIT}"
            ),
            "requirements": {
                ch.target: ch.after for ch in c.changes if ch.target in TARGETS and ch.after
            },
        }
        return RuleDraft(body=body, entities=(f"customer:{customer}",))

    def search_text(self, case: Mapping[str, Any]) -> str:
        payment = case["payment"]
        return f"入金 消込 {payment.get('bank', '')} {payment.get('payer_name_raw', '')}"

    def check_output(self, output: Mapping[str, Any], rules: Sequence[RuleBody]) -> list[str]:
        targets = {t for rule in rules for t in rule.targets}
        problems: list[str] = []
        if "match.mode" in targets and output.get("mode") != "review":
            problems.append(
                "match.mode: this payment must be reviewed by a person, not auto-matched"
            )
        if (
            "match.difference.writeoff" in targets
            and output.get("difference_treatment") == "writeoff"
        ):
            problems.append("match.difference.writeoff: the difference may not be written off")
        if "match.evidence" in targets:
            evidence: Mapping[str, Any] = output.get("evidence") or {}
            if not (evidence.get("payment_id") and evidence.get("invoice_id")):
                problems.append("match.evidence: record the payment and the invoice matched")
        return problems


def route(case: Mapping[str, Any], applied: Sequence[RuleBody]) -> str:
    """auto only when the payer is confidently known, no rule asks for review,
    and any difference is explained by a rule that says how to treat it."""
    if resolution(case).band != "high":
        return "review"
    if any("match.mode" in r.targets for r in applied):
        return "review"
    if difference(case) != 0 and not any(
        "match.difference" in r.targets and getattr(r, "requirements", {}) for r in applied
    ):
        return "review"
    return "auto"


PACK = CashApplicationPack()
