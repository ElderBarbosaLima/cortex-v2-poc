"""Every synthetic rule's CEL condition agrees with its hand-written Python
predicate on every case of both workflows (promised in issue 6).

The golden sets were computed from the predicates; this proves the CEL text the
system will actually evaluate says the same thing, for all 80 cases.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.conditions.cel import CelExprEvaluator
from app.domain_packs.cash_application import synthetic as ca
from app.domain_packs.custom_invoices import synthetic as ci
from app.synthetic.common import RuleSpec

DATA = Path(os.environ.get("CORTEX_SYNTHETIC_DIR", "/data/synthetic"))
cel = CelExprEvaluator()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _with_dates(obj: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {k: (dt.date.fromisoformat(v) if k in fields and v else v) for k, v in obj.items()}


def _invoice_contexts() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(case id, context for the predicate, context for CEL)."""
    cases = _jsonl(DATA / ci.WORKFLOW / "cases.jsonl")
    return [(c["case_id"], c, {"invoice": _with_dates(c, "issue_date")}) for c in cases]


def _payment_contexts() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    d = DATA / ca.WORKFLOW
    invoices = {i["invoice_id"]: i for i in json.loads((d / "invoices.json").read_text())}
    expected = {e["case_id"]: e for e in json.loads((d / "expected.json").read_text())}
    accounts = {
        c["customer_id"]: c for c in json.loads((d / "customers.json").read_text())["customers"]
    }
    out: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for p in _jsonl(d / "cases.jsonl"):
        res = expected[p["payment_id"]]["resolution"]
        customer = (
            res["expected_entity"].removeprefix("customer:") if res["band"] == "high" else None
        )
        payment = {
            **p,
            "customer_id": customer,
            "garnishment_registered": bool(
                accounts.get(customer or "", {}).get("garnishment_registered")
            ),
        }
        invoice = invoices[p["open_invoices"][0]]
        raw = {"payment": payment, "invoice": invoice}
        out.append(
            (
                p["payment_id"],
                raw,
                {
                    "payment": _with_dates(payment, "value_date"),
                    "invoice": _with_dates(invoice, "issue_date"),
                },
            )
        )
    return out


WORKFLOWS: list[tuple[str, list[RuleSpec], Any, tuple[str, ...]]] = [
    (ci.WORKFLOW, [*ci.RULES, ci.LEARNED], _invoice_contexts, ("invoice",)),
    (ca.WORKFLOW, [*ca.RULES, ca.LEARNED], _payment_contexts, ("invoice", "payment")),
]


@pytest.mark.parametrize(
    ("workflow", "rules", "contexts", "variables"), WORKFLOWS, ids=[w[0] for w in WORKFLOWS]
)
def test_cel_and_predicate_agree_on_every_case(
    workflow: str, rules: list[RuleSpec], contexts: Any, variables: tuple[str, ...]
) -> None:
    compiled = {r.key: cel.compile(r.condition, variables=variables) for r in rules}
    disagreements: list[str] = []
    evaluated = 0

    for case_id, raw, cel_ctx in contexts():
        for rule in rules:
            want = rule.predicate(raw)
            got = cel.evaluate(compiled[rule.key], cel_ctx)
            evaluated += 1
            if got != want:
                disagreements.append(f"{case_id} {rule.key}: CEL={got} predicate={want}")

    assert disagreements == []
    assert evaluated == 40 * len(rules)


def test_every_committed_rule_condition_compiles() -> None:
    for workflow, variables in ((ci.WORKFLOW, ("invoice",)), (ca.WORKFLOW, ("invoice", "payment"))):
        rules = json.loads((DATA / workflow / "rules.json").read_text())
        learned = _jsonl(DATA / workflow / "corrections.jsonl")[0]["expected_rule"]
        for rule in [*rules, learned]:
            cel.compile(rule["body"]["condition"], variables=variables)


def test_the_agreement_check_does_catch_a_wrong_condition() -> None:
    """Guard against a vacuous agreement test: an off-by-one fee limit must disagree."""
    wrong = cel.compile(
        ca.LEARNED.condition.replace("<= 880", "<= 881"), variables=("invoice", "payment")
    )

    disagree = [
        case_id
        for case_id, raw, cel_ctx in _payment_contexts()
        if cel.evaluate(wrong, cel_ctx) != ca.LEARNED.predicate(raw)
    ]

    assert disagree == ["PAY-0127"]  # the 881-yen case
