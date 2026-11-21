"""The domain pack contract, as a reusable check. Every pack, fake or real
(issues 25–26), must pass `assert_pack_contract`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.conditions.cel import CelExprEvaluator, to_cel_value
from app.domain_packs.base import (
    CONFIDENCE_BANDS,
    DomainPack,
    entity_literals,
    is_entity_key,
    referenced_fields,
)
from app.services.ledger import MODEL_AUTHORITY_CEILING

cel = CelExprEvaluator()


def _check_declarations(pack: DomainPack) -> None:
    assert pack.name and pack.entity_types and pack.targets and pack.condition_variables
    assert set(pack.condition_fields) == set(pack.condition_variables)
    for path, entity_type in pack.entity_fields.items():
        variable, _, field_name = path.partition(".")
        assert variable in pack.condition_variables, path
        assert field_name in pack.condition_fields[variable], path
        assert entity_type in pack.entity_types, path


def _check_case(pack: DomainPack, case: Mapping[str, Any]) -> dict[str, Any]:
    matches = pack.resolve_entities(case)
    for m in matches:
        assert m.confidence in CONFIDENCE_BANDS, m
        assert m.key is None or is_entity_key(m.key, pack.entity_types), m
        assert (m.confidence == "high") == (m.key is not None), (
            m
        )  # only a confident match names an entity
    context = pack.condition_context(case, matches)
    assert set(context) == set(pack.condition_variables)
    to_cel_value(context)  # no floats, datetimes or out-of-range ints reach CEL
    assert pack.search_text(case).strip()
    return context


def assert_pack_contract(
    pack: DomainPack,
    cases: Sequence[Mapping[str, Any]],
    correction: Mapping[str, Any],
    corrected_case: Mapping[str, Any],
) -> None:
    _check_declarations(pack)
    for case in cases:
        _check_case(pack, case)

    draft = pack.correction_to_rule(
        pack.correction_schema.model_validate(correction), case=corrected_case
    )
    body = pack.rule_body_schema.model_validate(dict(draft.body))
    assert body.targets and set(body.targets) <= pack.targets, body.targets
    compiled = cel.compile(body.condition, variables=pack.condition_variables)
    allowed = {f"{v}.{f}" for v, fields in pack.condition_fields.items() for f in fields}
    assert referenced_fields(body.condition, pack.condition_variables) <= allowed
    assert entity_literals(body.condition, pack.entity_fields) <= set(draft.entities)  # Q6
    assert all(is_entity_key(e, pack.entity_types) for e in draft.entities)
    assert draft.authority <= MODEL_AUTHORITY_CEILING
    assert draft.enforcement in ("mandatory", "prohibition", "default")
    # The rule proposed from a correction applies to the case it came from.
    assert cel.evaluate(compiled, _check_case(pack, corrected_case))
