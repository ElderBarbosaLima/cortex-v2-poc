"""Customer rules from a spreadsheet (issue 47, slide 13: "Customer rules
(Excel …), with a column guide").

IBM keeps 192 rules for 164 customers in Excel. Exported as CSV, each row is
mapped to one rule in the two-layer format (issue 40) through a column guide:
which column holds the customer, the exception type, each engine field, the
guidance and the dates, and how their exception names map to ours. Every row
is validated here, with its row number, before anything is imported; valid
rows become items for `POST /v1/cortex/imports` (proposed, capped at authority
10, each still decided by its owner).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.domain_packs.cash_allocation.pack import (
    ENGINE_SCHEMAS,
    FEE_LIMIT,
    AllocationRuleBody,
)

_INTS = {"expected_fee", "max_fee", "tolerance", "max_invoices", "instalments"}


def _condition(customer: str, exception: str, engine: Mapping[str, Any]) -> str:
    base = f'payment.customer_id == "{customer}"'
    if exception == "bank_fee":
        return f"{base} && payment.gap > 0 && payment.gap <= {engine.get('max_fee', FEE_LIMIT)}"
    if exception == "tax_difference":
        tol = int(engine.get("tolerance", 1))
        return f"{base} && payment.gap != 0 && payment.gap >= -{tol} && payment.gap <= {tol}"
    if exception == "combined_payment":
        return f"{base} && payment.candidate_count > 1"
    if exception == "duplicate_payment":
        twice = "payment.amount >= 2 * payment.candidates_total"
        return f"{base} && payment.candidates_total > 0 && {twice}"
    return f"{base} && payment.gap > 0"


def _reader(row: Mapping[str, str | None], cols: Mapping[str, str]) -> Callable[[str], str]:
    def cell(key: str) -> str:
        return (row.get(cols.get(key, ""), "") or "").strip()

    return cell


@dataclass
class SheetResult:
    items: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    rows: list[int] = field(default_factory=list[int])  # the sheet row of each item
    problems: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


def read_sheet(
    text: str, guide: Mapping[str, Any], *, scope: str, purpose: str = "cash_matching"
) -> SheetResult:
    """`guide`: {"columns": {"customer_id": "<header>", "exception": …, "guidance": …,
    "valid_from": …, "valid_to": …, "<engine field>": …}, "exceptions": {"<their
    name>": "<our type>"}, "authority": 20, "enforcement": "default"}."""
    cols: Mapping[str, str] = guide["columns"]
    names: Mapping[str, str] = guide.get("exceptions", {})
    result = SheetResult()
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):  # row 1 is the header
        cell = _reader(row, cols)

        customer, raw_exception = cell("customer_id"), cell("exception")
        exception = names.get(raw_exception, raw_exception)
        if not customer or exception not in ENGINE_SCHEMAS:
            result.problems.append(
                {
                    "row": n,
                    "detail": f"customer {customer!r} / exception {raw_exception!r} not recognised",
                }
            )
            continue
        engine: dict[str, Any] = {}
        for name in ENGINE_SCHEMAS[exception].model_fields:
            value = cell(name)
            if value:
                engine[name] = (
                    int(value.replace(",", "").replace("¥", "")) if name in _INTS else value
                )
        body = {
            "text_ja": cell("guidance") or f"{customer}: {exception}",
            "targets": [f"allocation.{exception}"],
            "condition": _condition(customer, exception, engine),
            "exception": exception,
            "engine": engine,
        }
        try:
            AllocationRuleBody.model_validate(body)
        except (ValidationError, ValueError) as exc:
            msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            result.problems.append({"row": n, "detail": str(msg)})
            continue
        result.items.append(
            {
                "domain": "cash_allocation",
                "kind": "rule",
                "schema_version": "cash_allocation.rule/1",
                "body": body,
                "scope": scope,
                "entities": [f"customer:{customer}"],
                "enforcement": guide.get("enforcement", "default"),
                "authority": int(guide.get("authority", 20)),
                "sensitivity": {"class": "internal"},
                "purpose": purpose,
                "valid_from": cell("valid_from") or "2026-04-01",
                "valid_to": cell("valid_to") or None,
            }
        )
        result.rows.append(n)
    return result
