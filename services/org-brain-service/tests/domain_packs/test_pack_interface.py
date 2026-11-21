"""The pack interface, its helpers and registry, and the fake pack's contract."""

from __future__ import annotations

import pytest

from app.domain_packs.base import (
    UnknownPackError,
    entity_literals,
    get_pack,
    is_entity_key,
    referenced_fields,
    register_pack,
    registered_packs,
)
from tests.domain_packs.contract import assert_pack_contract
from tests.fakes.fake_pack import CASES, CORRECTION, PACK, FakeWidgetPack


def test_the_fake_pack_honours_the_contract() -> None:
    assert_pack_contract(PACK, CASES, CORRECTION, corrected_case=CASES[0])


def test_the_contract_rejects_a_correction_whose_rule_misses_its_entity() -> None:
    class Sloppy(FakeWidgetPack):
        def correction_to_rule(self, correction, *, case):  # type: ignore[no-untyped-def]
            draft = super().correction_to_rule(correction, case=case)
            return type(draft)(body=draft.body, entities=())  # condition names W-1, entities do not

    with pytest.raises(AssertionError):
        assert_pack_contract(Sloppy(), CASES, CORRECTION, corrected_case=CASES[0])


def test_the_contract_rejects_a_float_in_the_condition_context() -> None:
    class Floaty(FakeWidgetPack):
        def condition_context(self, case, entities):  # type: ignore[no-untyped-def]
            return {"order": {**super().condition_context(case, entities)["order"], "qty": 1.5}}

    with pytest.raises(Exception, match="float"):
        assert_pack_contract(Floaty(), CASES, CORRECTION, corrected_case=CASES[0])


def test_referenced_fields_ignore_string_literals() -> None:
    condition = 'order.widget_code == "order.secret" && order.qty > 2 && payment.amount > 0'

    assert referenced_fields(condition, ("order",)) == {"order.widget_code", "order.qty"}


def test_entity_literals_in_either_operand_order() -> None:
    fields = {"invoice.customer_id": "customer"}

    found = entity_literals(
        'invoice.customer_id == "CUST-0042" || "CUST-0017" == invoice.customer_id', fields
    )

    assert found == {"customer:CUST-0042", "customer:CUST-0017"}
    assert entity_literals('invoice.channel == "coupa"', fields) == set()


@pytest.mark.parametrize(
    ("key", "ok"),
    [("widget:W-1", True), ("customer:CUST-0042", False), ("widget:", False), ("架空電機", False)],
)
def test_entity_keys_are_canonical_and_typed(key: str, ok: bool) -> None:
    assert is_entity_key(key, frozenset({"widget"})) is ok


def test_registry() -> None:
    register_pack(PACK)
    register_pack(PACK)  # the same object twice is fine

    assert get_pack("widgets") is PACK
    assert "widgets" in registered_packs()
    with pytest.raises(ValueError, match="already registered"):
        register_pack(FakeWidgetPack())
    with pytest.raises(UnknownPackError):
        get_pack("nope")
