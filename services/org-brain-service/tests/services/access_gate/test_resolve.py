"""Resolution (pure): constraints never lose; defaults by authority, then the
nearer scope. Cross-checked against the golden-set oracle, written separately."""

from __future__ import annotations

import random

import pytest

from app.domain_packs.base import RuleBody
from app.models import MemoryRecord
from app.services.access_gate.applicability import ApplicableRule
from app.services.access_gate.resolve import resolve
from app.synthetic.common import RuleSpec, expected_rules, path_up


def rule(rid: str, enforcement: str, authority: int, depth: int, *targets: str) -> ApplicableRule:
    row = MemoryRecord(record_id=rid, enforcement=enforcement, authority=authority)
    body = RuleBody(text_ja="x", targets=list(targets or ["t"]), condition="true")
    return ApplicableRule(row, body, depth)


def roles(rules: list[ApplicableRule]) -> dict[str, tuple[str, str | None]]:
    return {r.rule.record_id: (r.role, r.overridden_by) for r in resolve(rules)}


@pytest.mark.parametrize(("local_authority", "global_authority"), [(30, 10), (10, 30), (20, 20)])
def test_a_global_prohibition_beats_a_local_default_whatever_the_rank(
    local_authority: int, global_authority: int
) -> None:
    local = rule("local", "default", local_authority, 0)
    ban = rule("ban", "prohibition", global_authority, 2)

    assert roles([local, ban]) == {"local": ("overridden", "ban"), "ban": ("constraint", None)}
    assert roles([ban, local]) == roles([local, ban])  # order does not matter


def test_constraints_are_never_overridden_not_even_by_each_other() -> None:
    a, b = rule("a", "mandatory", 5, 2), rule("b", "prohibition", 40, 0)

    assert roles([a, b]) == {"a": ("constraint", None), "b": ("constraint", None)}


def test_a_prohibition_is_credited_before_a_mandatory_rule() -> None:
    d, m, p = (
        rule("d", "default", 40, 0),
        rule("m", "mandatory", 1, 0),
        rule("p", "prohibition", 1, 3),
    )

    assert roles([d, m, p])["d"] == ("overridden", "p")


def test_between_defaults_authority_then_the_nearer_scope() -> None:
    high_far, low_near = rule("hf", "default", 30, 2), rule("ln", "default", 10, 0)
    near, far = rule("n", "default", 20, 0), rule("f", "default", 20, 2)
    tie_a, tie_b = rule("ta", "default", 20, 1), rule("tb", "default", 20, 1)

    assert roles([high_far, low_near])["ln"] == ("overridden", "hf")
    assert roles([near, far])["f"] == ("overridden", "n")
    assert roles([tie_a, tie_b]) == {"ta": ("applied", None), "tb": ("applied", None)}


def test_rules_on_different_targets_do_not_compete() -> None:
    ban = rule("ban", "prohibition", 40, 0, "invoice.note")
    local = rule("local", "default", 10, 0, "invoice.due_date")

    assert roles([ban, local])["local"] == ("applied", None)


def test_resolution_agrees_with_the_golden_set_oracle() -> None:
    request = "jp/finance/q2c-invoicing"
    scopes = [s for s in path_up(request) if s != "org"]  # nearest first, as OPA answers
    rng = random.Random(19)
    for _ in range(300):
        specs: list[RuleSpec] = []
        for i in range(rng.randint(1, 6)):
            specs.append(
                RuleSpec(
                    key=f"r{i}",
                    scope=rng.choice(scopes),
                    enforcement=rng.choice(["default", "default", "mandatory", "prohibition"]),
                    authority=rng.choice([10, 20, 30]),
                    text_ja="x",
                    targets=tuple(rng.sample(["t1", "t2", "t3"], rng.randint(1, 2))),
                    condition="true",
                    predicate=lambda _ctx: True,
                )
            )
        kept, overridden = expected_rules(specs, {}, request_scope=request, entities=set())
        ours = roles(
            [
                rule(s.key, s.enforcement, s.authority, scopes.index(s.scope), *s.targets)
                for s in specs
            ]
        )

        assert sorted(k for k, (role, _) in ours.items() if role != "overridden") == kept
        assert {k: by for k, (role, by) in ours.items() if role == "overridden"} == overridden
