"""ConditionEvaluator on cel-expr-python (the CEL team's wrapper of cel-cpp).

Characterised in issue 11 (tests/conditions/test_cel_characterisation.py):
- compile() rejects syntax errors and undeclared variables/functions, and type-
  checks against the declared variables (each a map(string, dyn));
- int arithmetic overflow is an OUT_OF_RANGE error, never a wrap;
- a missing map key is NOT_FOUND; `null == "x"` is false;
- a Python datetime is accepted only as a top-level variable, never inside a
  map, hence dates as day numbers (see app/conditions/base.py);
- evaluation errors come back as a CEL error value, not an exception.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from cel_expr_python import cel  # pyright: ignore[reportMissingTypeStubs]

from app.conditions.base import (
    CompiledCondition,
    ConditionCompileError,
    ConditionEvalError,
)

EPOCH = dt.date(1970, 1, 1)
INT64 = (-(2**63), 2**63 - 1)
_DATE_LITERAL = re.compile(r'\bdate\(\s*"([^"]*)"\s*\)')
_ERROR_CODE = re.compile(r"^([A-Z_]+):\s*")

_cel: Any = cel


def to_day_number(value: dt.date) -> int:
    return (value - EPOCH).days


def _date_fn(text: str) -> int:
    return to_day_number(dt.date.fromisoformat(text))


def _split_error(raw: str) -> tuple[str, str]:
    first = raw.strip().splitlines()[0] if raw.strip() else raw
    match = _ERROR_CODE.match(first)
    if not match:
        return "UNKNOWN", first
    return match.group(1), first[match.end() :]


def to_cel_value(value: Any, path: str = "") -> Any:
    """Convert one context value, refusing what conditions must never see."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        if not INT64[0] <= value <= INT64[1]:
            raise ConditionEvalError("OUT_OF_RANGE", f"{path} does not fit in int64")
        return value
    if isinstance(value, float):
        raise ConditionEvalError("INVALID_ARGUMENT", f"{path} is a float; amounts are int (JPY)")
    if isinstance(value, dt.datetime):
        raise ConditionEvalError("INVALID_ARGUMENT", f"{path} is a datetime; conditions take dates")
    if isinstance(value, dt.date):
        return to_day_number(value)
    if isinstance(value, Mapping):
        items = cast("Mapping[Any, Any]", value)
        return {str(k): to_cel_value(v, f"{path}.{k}") for k, v in items.items()}
    if isinstance(value, list | tuple):
        seq = cast("Sequence[Any]", value)
        return [to_cel_value(v, f"{path}[{i}]") for i, v in enumerate(seq)]
    raise ConditionEvalError(
        "INVALID_ARGUMENT", f"{path} has unsupported type {type(value).__name__}"
    )


class CelExprEvaluator:
    @staticmethod
    @functools.cache
    def _env(variables: tuple[str, ...]) -> Any:
        map_type = _cel.Type.Map(_cel.Type.STRING, _cel.Type.DYN)
        date_decl = _cel.FunctionDecl(
            "date",
            [
                _cel.Overload(
                    "date_string",
                    return_type=_cel.Type.INT,
                    parameters=[_cel.Type.STRING],
                    impl=_date_fn,
                )
            ],
        )
        return _cel.NewEnv(variables={v: map_type for v in variables}, functions=[date_decl])

    def compile(self, source: str, *, variables: Sequence[str]) -> CompiledCondition:
        declared = tuple(sorted(variables))
        for literal in _DATE_LITERAL.findall(source):
            try:
                dt.date.fromisoformat(literal)
            except ValueError:
                raise ConditionCompileError(
                    "INVALID_ARGUMENT", f'date("{literal}") is not a YYYY-MM-DD date'
                ) from None
        try:
            program = self._env(declared).compile(source)
        except RuntimeError as exc:
            code, message = _split_error(str(exc))
            raise ConditionCompileError(code, message.removeprefix("ERROR: ")) from None
        if str(program.return_type()) != "BOOL":
            raise ConditionCompileError(
                "INVALID_ARGUMENT",
                f"a condition must be boolean, this one is {program.return_type()}",
            )
        return CompiledCondition(source=source, variables=declared, handle=program)

    def evaluate(self, condition: CompiledCondition, context: Mapping[str, Any]) -> bool:
        missing = [v for v in condition.variables if v not in context]
        if missing:
            raise ConditionEvalError("INVALID_ARGUMENT", f"context lacks {missing}")
        data = {v: to_cel_value(context[v], v) for v in condition.variables}
        result = condition.handle.eval(data=data)
        if str(result.type()) == "ERROR":
            code, message = _split_error(str(result.value()))
            raise ConditionEvalError(code, message)
        value = result.value()
        if not isinstance(value, bool):
            raise ConditionEvalError(
                "INVALID_ARGUMENT", f"result is {type(value).__name__}, not bool"
            )
        return value
