"""The custom_invoices pack (issue 25): contract, CEL vs the golden-set oracle,
seed rules admissible, resolver, correction → rule, output check."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import register_pack
from app.domain_packs.custom_invoices import PACK
from app.domain_packs.custom_invoices.synthetic import LEARNED, RULES
from app.models import Kind
from app.services.admission import Proposal, validate_proposal
from tests.domain_packs.contract import assert_pack_contract
from tests.test_identity_and_seed import SEED_DIR

register_pack(PACK)
DATA = SEED_DIR / "custom_invoices"
cel = CelExprEvaluator()


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


CASES = {c["case_id"]: c for c in _jsonl("cases.jsonl")}
CORRECTIONS = _jsonl("corrections.jsonl")
ISSUER = json.loads((DATA / "customers.json").read_text(encoding="utf-8"))["issuer"]


def ideal_output(case: dict[str, Any]) -> dict[str, Any]:
    """A drafted invoice that carries everything any rule can ask for."""
    return {
        "registration_number": ISSUER["registration_number"],
        "tax_breakdown": case["tax_by_rate"],
        "remarks": {
            "payment_terms": "月末締め翌月末払い",
            "department_code": "D-77",
            "po_number": f"注文番号: {case['po_number']}",
        },
        "attachments": ["kenshusho"],
    }


def test_the_pack_passes_the_contract() -> None:
    assert_pack_contract(PACK, list(CASES.values()), CORRECTIONS[0], CASES["INV-0007"])


@pytest.mark.parametrize("rule", [*RULES, LEARNED], ids=lambda r: r.key)
def test_each_cel_condition_agrees_with_the_oracle_on_every_case(rule: Any) -> None:
    compiled = cel.compile(rule.condition, variables=PACK.condition_variables)
    for case in CASES.values():
        context = PACK.condition_context(case, PACK.resolve_entities(case))
        assert cel.evaluate(compiled, context) is rule.predicate(case), case["case_id"]


@pytest.mark.parametrize(
    "doc", json.loads((DATA / "rules.json").read_text()), ids=lambda d: d["key"]
)
def test_every_seed_rule_is_admissible(doc: dict[str, Any]) -> None:
    proposal = Proposal(
        domain="custom_invoices",
        kind=Kind(doc["kind"]),
        schema_version=doc["schema_version"],
        body=doc["body"],
        scope="00000000-0000-0000-0000-000000000001",
        entities=tuple(doc["entities"]),
        enforcement=doc["enforcement"],
        authority=doc["authority"],
        sensitivity=doc["sensitivity"],
        valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )

    assert validate_proposal(proposal)[1] == []


def test_customers_resolve_by_id_never_by_name() -> None:
    known = PACK.resolve_entities(CASES["INV-0007"])
    unknown = PACK.resolve_entities({**CASES["INV-0007"], "customer_id": "CUST-9999"})
    name_only = PACK.resolve_entities({**CASES["INV-0007"], "customer_id": None})

    assert [(m.key, m.confidence) for m in known] == [
        ("customer:CUST-0042", "high"),
        ("channel:coupa", "high"),
    ]
    assert unknown[0].key is None and name_only[0].key is None
    assert all("架空電機" not in str(m.key) for m in [*known, *unknown, *name_only])


def test_a_correction_becomes_the_learned_rule() -> None:
    correction = PACK.correction_schema.model_validate(CORRECTIONS[0])
    draft = PACK.correction_to_rule(correction, case=CASES["INV-0007"])

    assert draft.entities == LEARNED.entities
    assert draft.body["condition"] == LEARNED.condition
    assert sorted(draft.body["targets"]) == sorted(LEARNED.targets)
    assert (draft.enforcement, draft.authority) == ("default", 10)
    assert draft.body["requirements"]["invoice.remarks.po_number"] == "注文番号: PO-88231"


def test_check_output_names_what_a_rule_requires() -> None:
    case = CASES["INV-0007"]
    learned = PACK.rule_body_schema.model_validate(
        {"text_ja": "x", "targets": list(LEARNED.targets), "condition": LEARNED.condition}
    )
    regno = PACK.rule_body_schema.model_validate(
        {"text_ja": "y", "targets": ["invoice.registration_number"], "condition": "true"}
    )
    full = ideal_output(case)
    missing = {**full, "remarks": {}, "attachments": []}

    assert PACK.check_output(full, [learned, regno]) == []
    assert PACK.check_output(missing, [learned]) == [
        "invoice.attachments.kenshusho is missing (x)",
        "invoice.remarks.po_number is missing (x)",
    ]
    assert PACK.check_output({**full, "registration_number": "123"}, [regno]) == [
        "invoice.registration_number must be T followed by 13 digits"
    ]
