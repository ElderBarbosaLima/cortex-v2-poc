"""Rule conditions: "to which cases does this rule apply" (D22).

A condition is data: a CEL expression stored in the rule's body and versioned
with it. It is compiled when the rule is admitted (an invalid condition is never
admitted) and evaluated on the access gate's applicability path. The engine sits
behind `ConditionEvaluator`, so cel-expr-python can be swapped (for example for
common-expression-language) without touching admission or the gates.

Context rules, enforced before any engine sees a value:
- JPY amounts are int (never float); anything outside int64 is refused.
- Dates are `datetime.date` and reach CEL as an int: days since 1970-01-01.
  `date("YYYY-MM-DD")` in an expression returns the same int, so
  `invoice.issue_date >= date("2023-10-01")` compares calendar dates and
  `payment.value_date - invoice.issue_date <= 30` is a difference in days.
  A calendar date has no time zone; evaluation uses the business event's date,
  never the server clock (Q8).
- A condition must be boolean.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ConditionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConditionCompileError(ConditionError):
    """The expression cannot be admitted (syntax, unknown name, wrong type)."""


class ConditionEvalError(ConditionError):
    """The expression could not be evaluated for this context (missing field,
    overflow, unsupported value). The caller decides what that means; the
    access gate never lets it silently drop a constraint."""


@dataclass(frozen=True)
class CompiledCondition:
    source: str
    variables: tuple[str, ...]
    handle: Any  # the engine's compiled program


class ConditionEvaluator(Protocol):
    def compile(self, source: str, *, variables: Sequence[str]) -> CompiledCondition: ...

    def evaluate(self, condition: CompiledCondition, context: Mapping[str, Any]) -> bool: ...
