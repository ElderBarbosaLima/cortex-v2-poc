"""The custom_invoices domain pack (D18, first possibility; issue 25).

Everything the core needs to govern the ~1% of invoices that fall out of
automation, and nothing the core has to know about invoices:
- entities: `customer:<id>` resolved by the customer id against the customer
  master (never by name), and `channel:<name>` for the delivery channel;
- one CEL variable, `invoice`, with the fields a condition may read; dates are
  `datetime.date` (reach CEL as day numbers), amounts are int JPY;
- the targets rules govern, the rule and correction schemas;
- how a reviewer's correction becomes a rule draft (a person still approves);
- `check_output`: whether a drafted invoice carries what its rules require.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain_packs.base import EntityMatch, RuleBody, RuleDraft
from app.domain_packs.custom_invoices.synthetic import CUSTOMERS

_CUSTOMER_ID = re.compile(r"^CUST-\d{4}$")
_REGISTRATION_NUMBER = re.compile(r"^T\d{13}$")
_CHANNELS = frozenset({"ariba", "coupa", "email", "postal"})

TARGETS = frozenset(
    {
        "invoice.registration_number",
        "invoice.tax_breakdown",
        "invoice.remarks.payment_terms",
        "invoice.remarks.department_code",
        "invoice.remarks.po_number",
        "invoice.attachments.kenshusho",
    }
)


class InvoiceRuleBody(RuleBody):
    """A rule about invoices. `requirements` optionally names the value a
    target must carry (e.g. the department code), keyed by target."""

    requirements: dict[str, str] = Field(default_factory=dict[str, str])


class InvoiceChange(BaseModel):
    target: str
    action: Literal["set", "require", "remove"]
    before: str | None = None
    after: str | None = None


class InvoiceCorrection(BaseModel):
    """A reviewer's correction of a drafted invoice (corrections.jsonl shape)."""

    model_config = ConfigDict(extra="ignore")

    correction_id: str
    case_id: str
    reviewer: str
    corrected_at: dt.datetime
    changes: list[InvoiceChange] = Field(min_length=1)
    note_ja: str = ""


def _present(output: Mapping[str, Any], target: str) -> bool:
    """Whether a drafted invoice carries `target` (e.g. invoice.remarks.po_number)."""
    path = target.removeprefix("invoice.").split(".")
    if path[0] == "attachments":
        return path[1] in [str(a) for a in output.get("attachments", [])]
    node: Any = output
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return False
        node = node[part]  # pyright: ignore[reportUnknownVariableType]
    return node not in (None, "", [], {})


class CustomInvoicesPack:
    name = "custom_invoices"
    entity_types = frozenset({"customer", "channel"})
    condition_variables = ("invoice",)
    condition_fields = MappingProxyType(
        {
            "invoice": frozenset(
                {
                    "case_id",
                    "customer_id",
                    "channel",
                    "issue_date",
                    "currency",
                    "total",
                    "tax_total",
                    "tax_rates",
                    "line_count",
                    "has_po_number",
                }
            )
        }
    )
    # Only the customer id names an entity in a condition: the channel is a
    # property of the case, so `invoice.channel == "coupa"` needs no entity.
    entity_fields = MappingProxyType({"invoice.customer_id": "customer"})
    targets = TARGETS
    rule_body_schema = InvoiceRuleBody
    correction_schema = InvoiceCorrection

    _known_customers = frozenset(str(c["customer_id"]) for c in CUSTOMERS)

    def resolve_entities(self, case: Mapping[str, Any]) -> list[EntityMatch]:
        matches: list[EntityMatch] = []
        customer_id = str(case.get("customer_id") or "")
        if _CUSTOMER_ID.match(customer_id) and customer_id in self._known_customers:
            matches.append(
                EntityMatch(
                    f"customer:{customer_id}",
                    "high",
                    {"source": "customer_id", "value": customer_id, "master": "customers.json"},
                )
            )
        else:
            # Never fall back to the customer's name: names are not identities.
            matches.append(
                EntityMatch(None, "none", {"source": "customer_id", "value": customer_id})
            )
        channel = str(case.get("channel") or "")
        if channel in _CHANNELS:
            matches.append(EntityMatch(f"channel:{channel}", "high", {"source": "channel"}))
        return matches

    def condition_context(
        self, case: Mapping[str, Any], entities: Sequence[EntityMatch]
    ) -> dict[str, Any]:
        lines: list[Mapping[str, Any]] = list(case.get("lines") or [])
        return {
            "invoice": {
                "case_id": str(case["case_id"]),
                "customer_id": str(case["customer_id"]),
                "channel": str(case["channel"]),
                "issue_date": dt.date.fromisoformat(str(case["issue_date"])),
                "currency": str(case.get("currency", "JPY")),
                "total": int(case["total"]),
                "tax_total": int(case.get("tax_total", 0)),
                "tax_rates": sorted({int(line["tax_rate"]) for line in lines}),
                "line_count": len(lines),
                "has_po_number": bool(case.get("po_number")),
            }
        }

    def correction_to_rule(self, correction: BaseModel, *, case: Mapping[str, Any]) -> RuleDraft:
        c = InvoiceCorrection.model_validate(correction.model_dump())
        customer_id, channel = str(case["customer_id"]), str(case["channel"])
        targets = sorted({ch.target for ch in c.changes if ch.target in TARGETS})
        requirements = {
            ch.target: ch.after for ch in c.changes if ch.target in TARGETS and ch.after
        }
        text = c.note_ja or "、".join(f"{t} を必須とする" for t in targets)
        # The English example set (issue 39) writes its notes in English; the
        # draft follows the note's language. The field keeps its name.
        sentence = (
            f"For {customer_id} invoices sent via {channel}: {text}"
            if text.isascii()
            else f"{customer_id} の {channel} 請求書では、{text}"
        )
        body = {
            "text_ja": sentence,
            "targets": targets,
            "condition": (
                f'invoice.customer_id == "{customer_id}" && invoice.channel == "{channel}"'
            ),
            "requirements": requirements,
        }
        # A suggestion: default enforcement, authority at the model ceiling;
        # the named owner sets the final ones on approval.
        return RuleDraft(body=body, entities=(f"customer:{customer_id}",))

    def case_summary(self, case: Mapping[str, Any], correction: BaseModel | None) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "customer_id": str(case["customer_id"]),
            "channel": str(case["channel"]),
            "total": int(case["total"]),
        }
        if correction is not None:
            c = InvoiceCorrection.model_validate(correction.model_dump())
            summary |= {"targets": sorted({ch.target for ch in c.changes}), "reason": c.note_ja}
        return summary

    def search_text(self, case: Mapping[str, Any]) -> str:
        lines: list[Mapping[str, Any]] = list(case.get("lines") or [])
        descriptions = " ".join(str(line.get("desc", "")) for line in lines)
        return f"請求書 {case.get('customer_id', '')} {case.get('channel', '')} {descriptions}"

    def check_output(
        self, output: Mapping[str, Any], rules: Sequence[RuleBody], case: Mapping[str, Any]
    ) -> list[str]:
        problems: list[str] = []
        for rule in rules:
            for target in rule.targets:
                if not _present(output, target):
                    problems.append(f"{target} is missing ({rule.text_ja})")
        regno = output.get("registration_number")
        if regno is not None and not _REGISTRATION_NUMBER.match(str(regno)):
            problems.append("invoice.registration_number must be T followed by 13 digits")
        return sorted(set(problems))


PACK = CustomInvoicesPack()
