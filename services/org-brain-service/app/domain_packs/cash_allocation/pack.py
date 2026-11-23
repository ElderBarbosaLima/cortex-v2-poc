"""The cash_allocation domain pack: IBM Japan's workflow (Dev Brief, 2026-11-22).

IBM's Cash Application AI does the matching: their rule engine clears what
approved rules cover, an LLM works on the rest, a specialist reviews in CAT.
Cortex is the memory behind it. This pack differs from `cash_application`
(the PoC's first payment pack, kept to prove D18) in two ways:

- **The input is what IBM's AI sends** (issue 41, slide 22): the customer
  number (CAT already maps payer names), the payment, the gap, the candidate
  invoices and any remittance details. No payer-name resolution here.
- **A rule has two layers** (issue 40, slides 15–16): `engine`, the exact
  fields IBM's rule engine runs, typed per exception type; and `text_ja`, the
  plain guidance their LLM reads. One copy, kept in Cortex; `condition` (CEL)
  says when it applies.

The gap is the candidates' total minus the amount paid: positive when the
customer paid short (a bank fee, a partial payment), negative when they paid
more (a duplicate). JPY integers.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain_packs.base import EntityMatch, RuleBody, RuleDraft

Exception_ = Literal[
    "combined_payment", "bank_fee", "tax_difference", "duplicate_payment", "partial_payment"
]
EXCEPTIONS: tuple[str, ...] = (
    "combined_payment",
    "bank_fee",
    "tax_difference",
    "duplicate_payment",
    "partial_payment",
)
FEE_LIMIT = 880  # the largest bank transfer fee a customer deducts (JPY)
TARGETS = frozenset({f"allocation.{e}" for e in EXCEPTIONS} | {"allocation.review"})
Yen = Annotated[int, Field(ge=0, le=10_000_000)]


# ── Layer 1: what the rule engine runs, one schema per exception type ─────────


class _Engine(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CombinedPayment(_Engine):
    """One transfer pays several invoices: how they are picked."""

    pick: Literal["oldest_first", "po_number", "billing_period", "remittance_list"]
    max_invoices: Annotated[int, Field(ge=2, le=200)] | None = None


class BankFee(_Engine):
    """The payment arrives short by the transfer fee the customer deducted."""

    expected_fee: Annotated[int, Field(ge=1, le=FEE_LIMIT)]
    max_fee: Annotated[int, Field(ge=1, le=FEE_LIMIT)] = FEE_LIMIT
    treatment: Literal["fee_expense", "hold_for_review"] = "fee_expense"

    @model_validator(mode="after")
    def _range(self) -> BankFee:
        if self.max_fee < self.expected_fee:
            raise ValueError("max_fee must be at least expected_fee")
        return self


class TaxDifference(_Engine):
    """The total is off by consumption-tax rounding."""

    rounding: Literal["floor", "round_half_up", "ceil"]
    tolerance: Annotated[int, Field(ge=1, le=100)]
    treatment: Literal["writeoff", "hold_for_review"] = "writeoff"


class DuplicatePayment(_Engine):
    """The same invoice is paid twice."""

    action: Literal["hold", "refund", "apply_to_next_invoice"]


class PartialPayment(_Engine):
    """Part now, the rest later."""

    action: Literal["leave_balance_open", "instalments"]
    instalments: Annotated[int, Field(ge=2, le=24)] | None = None

    @model_validator(mode="after")
    def _count(self) -> PartialPayment:
        if (self.action == "instalments") != (self.instalments is not None):
            raise ValueError("instalments is set exactly when action is instalments")
        return self


ENGINE_SCHEMAS: Mapping[str, type[_Engine]] = MappingProxyType(
    {
        "combined_payment": CombinedPayment,
        "bank_fee": BankFee,
        "tax_difference": TaxDifference,
        "duplicate_payment": DuplicatePayment,
        "partial_payment": PartialPayment,
    }
)


class AllocationRuleBody(RuleBody):
    """`text_ja` is layer 2: the guidance the LLM reads, in plain words. A rule
    with no `exception` is guidance only (e.g. "send to review"): no engine."""

    exception: Exception_ | None = None
    engine: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _layers(self) -> AllocationRuleBody:
        if self.exception is None:
            if self.engine is not None:
                raise ValueError("engine needs an exception type")
            return self
        if self.engine is None:
            raise ValueError(f"a {self.exception} rule needs its engine fields")
        try:
            ENGINE_SCHEMAS[self.exception].model_validate(self.engine)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(["engine", *(str(p) for p in first.get("loc", ()))])
            raise ValueError(f"{where}: {first.get('msg')}") from None
        if f"allocation.{self.exception}" not in self.targets:
            raise ValueError(f"targets must include allocation.{self.exception}")
        return self


# ── The case IBM's AI sends (issue 41) ───────────────────────────────────────


class CandidateInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_id: str
    total: Yen
    issue_date: dt.date
    po_number: str | None = None


class Remittance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_refs: list[str] = Field(default_factory=list[str])
    note: str = ""


class Payment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment_id: str
    amount: Yen
    value_date: dt.date
    currency: Literal["JPY"] = "JPY"


class AllocationCase(BaseModel):
    """Slide 22: customer number, payment amount, date and gap, candidate
    invoices, key remittance details if any."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    customer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    payment: Payment
    candidate_invoices: list[CandidateInvoice] = Field(default_factory=list[CandidateInvoice])
    gap: int | None = Field(default=None, description="Sent by IBM; computed when absent")
    remittance: Remittance | None = None

    @property
    def effective_gap(self) -> int:
        if self.gap is not None:
            return self.gap
        if not self.candidate_invoices:
            return 0
        return sum(i.total for i in self.candidate_invoices) - self.payment.amount


# ── The reviewed outcome (the learning loop; issue 42 stores it as a case) ───


class AllocationOutcome(BaseModel):
    """What the specialist (or IBM's AI) decided, and why."""

    model_config = ConfigDict(extra="ignore")

    correction_id: str
    case_id: str
    reviewer: str
    corrected_at: dt.datetime
    result: Literal["confirmed", "corrected"] = "corrected"
    exception: Exception_ | None = None
    engine: dict[str, Any] | None = None
    selected_invoices: list[str] = Field(default_factory=list[str])
    note_ja: str = ""


def parse_case(case: Mapping[str, Any]) -> AllocationCase:
    return AllocationCase.model_validate(case)


class CashAllocationPack:
    name = "cash_allocation"
    entity_types = frozenset({"customer"})
    condition_variables = ("payment",)
    condition_fields = MappingProxyType(
        {
            "payment": frozenset(
                {
                    "payment_id",
                    "customer_id",
                    "amount",
                    "value_date",
                    "gap",
                    "candidate_count",
                    "candidates_total",
                    "has_remittance",
                }
            )
        }
    )
    entity_fields = MappingProxyType({"payment.customer_id": "customer"})
    targets = TARGETS
    rule_body_schema = AllocationRuleBody
    correction_schema = AllocationOutcome

    def resolve_entities(self, case: Mapping[str, Any]) -> list[EntityMatch]:
        c = parse_case(case)
        # CAT maps the payer to the customer before IBM's AI asks: taken as given.
        return [EntityMatch(f"customer:{c.customer_id}", "high", {"source": "cat"})]

    def condition_context(
        self, case: Mapping[str, Any], entities: Sequence[EntityMatch]
    ) -> dict[str, Any]:
        c = parse_case(case)
        return {
            "payment": {
                "payment_id": c.payment.payment_id,
                "customer_id": c.customer_id,
                "amount": c.payment.amount,
                "value_date": c.payment.value_date,
                "gap": c.effective_gap,
                "candidate_count": len(c.candidate_invoices),
                "candidates_total": sum(i.total for i in c.candidate_invoices),
                "has_remittance": c.remittance is not None,
            }
        }

    def correction_to_rule(self, correction: BaseModel, *, case: Mapping[str, Any]) -> RuleDraft:
        o = AllocationOutcome.model_validate(correction.model_dump())
        c = parse_case(case)
        if o.exception is None:
            raise ValueError("a rule is learned only from an outcome with an exception type")
        gap = c.effective_gap
        engine = o.engine or _engine_from_gap(o.exception, gap)
        note = o.note_ja or f"{o.exception} ({gap} 円)"
        english = note.isascii()
        body = {
            "text_ja": f"For {c.customer_id}: {note}"
            if english
            else f"{c.customer_id} について、{note}",
            "targets": [f"allocation.{o.exception}"],
            "condition": f'payment.customer_id == "{c.customer_id}"'
            + _gap_condition(o.exception, gap),
            "exception": o.exception,
            "engine": engine,
        }
        return RuleDraft(body=body, entities=(f"customer:{c.customer_id}",))

    def search_text(self, case: Mapping[str, Any]) -> str:
        c = parse_case(case)
        note = c.remittance.note if c.remittance else ""
        return f"入金 消込 {c.customer_id} {note}".strip()

    def check_output(
        self, output: Mapping[str, Any], rules: Sequence[RuleBody], case: Mapping[str, Any]
    ) -> list[str]:
        """Verify is not part of IBM's Phase 1 (D3); kept minimal: an output that
        names an exception must treat it the way an applied rule's engine says."""
        problems: list[str] = []
        exception = output.get("exception")
        treatment = output.get("treatment")
        for rule in rules:
            if not isinstance(rule, AllocationRuleBody) or rule.exception != exception:
                continue
            expected = (rule.engine or {}).get("treatment") or (rule.engine or {}).get("action")
            if expected and treatment and treatment != expected:
                problems.append(
                    f"allocation.{exception}: the rule says {expected}, not {treatment}"
                )
        return problems


def _engine_from_gap(exception: str, gap: int) -> dict[str, Any]:
    if exception == "bank_fee":
        fee = min(max(gap, 1), FEE_LIMIT)
        return {"expected_fee": fee, "max_fee": FEE_LIMIT, "treatment": "fee_expense"}
    if exception == "tax_difference":
        return {
            "rounding": "floor",
            "tolerance": max(1, min(abs(gap), 100)),
            "treatment": "writeoff",
        }
    if exception == "combined_payment":
        return {"pick": "oldest_first"}
    if exception == "duplicate_payment":
        return {"action": "hold"}
    return {"action": "leave_balance_open"}


def _gap_condition(exception: str, gap: int) -> str:
    if exception == "bank_fee":
        return f" && payment.gap > 0 && payment.gap <= {FEE_LIMIT}"
    if exception == "tax_difference":
        tol = max(1, min(abs(gap), 100))
        return f" && payment.gap != 0 && payment.gap >= -{tol} && payment.gap <= {tol}"
    if exception == "combined_payment":
        return " && payment.candidate_count > 1"
    if exception == "duplicate_payment":
        return " && payment.candidates_total > 0 && payment.amount >= 2 * payment.candidates_total"
    return " && payment.gap > 0"


PACK = CashAllocationPack()
