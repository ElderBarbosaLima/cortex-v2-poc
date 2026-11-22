"""Applicability and resolution on real Postgres + OPA (issue 19), fake pack.

Every test uses its own widget code as the case's entity and puts the same
code on its rules, so rules written by other tests never apply here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.main import register_tenants
from app.models import Kind, Status
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.access_gate.applicability import (
    Applicability,
    CaseFacts,
    applicable_rules,
    case_facts,
)
from app.services.access_gate.resolve import resolve
from app.services.policy_gate import PolicyGate, now_rfc3339
from app.utils.business_dates import business_start
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration


@dataclass
class World:
    seed: Seed
    gate: PolicyGate

    @property
    def tenant(self) -> str:
        return self.seed.consumers["askq2c-agent"].tenant_id

    def scope(self, key: str) -> str:
        return self.seed.node_ids[key]

    def readable(self, at: str = "jp/finance/q2c-invoicing") -> list[str]:
        decision = self.gate.read(
            {
                "tenant_id": self.tenant,
                "consumer": {"id": "askq2c-agent"},
                "end_user": None,
                "purpose": "invoice_drafting",
                "scope_node": self.scope(at),
                "now": now_rfc3339(),
            }
        )
        assert decision.allow, decision.reason
        return decision.readable_scopes


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        yield World(seed, gate)
        app_engine().dispose()


def _rule(
    w: World,
    *,
    scope: str,
    enforcement: str,
    authority: int = 10,
    text_ja: str,
    condition: str,
    targets: tuple[str, ...] = ("order.label",),
    entities: tuple[str, ...],
    valid_from: dt.date = dt.date(2026, 1, 1),
    valid_to: dt.date | None = None,
    status: Status = Status.APPROVED,
) -> str:
    with allow_network(), tenant_session(w.tenant) as s:
        row = ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=status,
            approved_by=(
                {"approver_type": "user", "user_id": w.seed.people["OWN-001"], "decision_id": None}
                if status is Status.APPROVED
                else None
            ),
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body={"text_ja": text_ja, "targets": list(targets), "condition": condition},
                scope=w.scope(scope),
                asserted_by={"type": "import", "id": "it"},
                authority=authority,
                source={},
                valid_from=business_start(valid_from),
                valid_to=business_start(valid_to) if valid_to else None,
                enforcement=enforcement,
                entities=entities,
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        )
        return row.record_id


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _facts(code: str, **case: Any) -> CaseFacts:
    order = {"order_id": "O-1", "widget_code": code, "colour": "赤", "qty": 3, **case}
    order["order_date"] = dt.date.fromisoformat(str(order.get("order_date", "2026-11-02")))
    return CaseFacts(entities=(f"widget:{code}",), context={"order": order})


def _apply(w: World, facts: CaseFacts, as_of: dt.date = dt.date(2026, 11, 2)) -> Applicability:
    with allow_network(), tenant_session(w.tenant) as s:
        return applicable_rules(
            s, pack=PACK, facts=facts, readable_scopes=w.readable(), as_of=as_of
        )


def _ids(result: Applicability) -> set[str]:
    return {r.record_id for r in result.rules}


def test_a_mandatory_rule_with_no_word_in_common_with_the_case_still_applies(
    world: World,
) -> None:
    code = _code()
    rule = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="数量が十個を超える出荷は二重確認する。",  # nothing of the case's text
        condition="order.qty >= 1",
        entities=(f"widget:{code}",),
    )

    result = _apply(world, _facts(code))

    assert rule in _ids(result)
    assert [r.role for r in resolve(result.rules) if r.rule.record_id == rule] == ["constraint"]


@pytest.mark.parametrize(
    ("local_authority", "global_authority"),
    [(30, 10), (10, 30)],
    ids=["local-higher", "global-higher"],
)
def test_a_global_prohibition_overrides_a_local_default_on_the_same_target(
    world: World, local_authority: int, global_authority: int
) -> None:
    code = _code()
    local = _rule(
        world,
        scope="jp/finance/q2c-invoicing",
        enforcement="default",
        authority=local_authority,
        text_ja="この商品には「急ぎ」ラベルを付ける。",
        condition=f'order.widget_code == "{code}"',
        entities=(f"widget:{code}",),
    )
    ban = _rule(
        world,
        scope="jp",
        enforcement="prohibition",
        authority=global_authority,
        text_ja="ラベルに独自の文言を加えてはならない。",
        condition="true",
        entities=(f"widget:{code}",),
    )

    resolved = {r.rule.record_id: r for r in resolve(_apply(world, _facts(code)).rules)}

    assert (resolved[ban].role, resolved[ban].overridden_by) == ("constraint", None)
    assert (resolved[local].role, resolved[local].overridden_by) == ("overridden", ban)


def test_a_rule_about_another_customer_never_appears(world: World) -> None:
    mine, theirs = _code(), _code()
    other = _rule(
        world,
        scope="jp/finance/q2c-invoicing",
        enforcement="mandatory",
        text_ja="この商品は検品必須。",
        condition="true",  # would apply to anything, if its entity matched
        entities=(f"widget:{theirs}",),
    )

    assert other not in _ids(_apply(world, _facts(mine)))
    assert other in _ids(_apply(world, _facts(theirs)))


def test_validity_is_judged_at_the_events_date_not_the_clock(world: World) -> None:
    code = _code()
    old = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="旧ルール（2025 年のみ）。",
        condition="true",
        entities=(f"widget:{code}",),
        valid_from=dt.date(2025, 1, 1),
        valid_to=dt.date(2026, 1, 1),
    )
    future = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="新ルール（2027 年から）。",
        condition="true",
        entities=(f"widget:{code}",),
        valid_from=dt.date(2027, 1, 1),
    )

    in_2025 = _ids(_apply(world, _facts(code), as_of=dt.date(2025, 6, 1)))
    now_ish = _ids(_apply(world, _facts(code), as_of=dt.date(2026, 11, 2)))
    last_day = _ids(_apply(world, _facts(code), as_of=dt.date(2025, 12, 31)))
    end_day = _ids(_apply(world, _facts(code), as_of=dt.date(2026, 1, 1)))

    assert old in in_2025 and old in last_day  # applies although the clock says 2026
    assert old not in now_ish and old not in end_day  # valid_to is exclusive
    assert future not in in_2025 and future not in now_ish


def test_a_false_condition_or_an_unapproved_rule_does_not_apply(world: World) -> None:
    code = _code()
    false = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="赤以外。",
        condition='order.colour == "青"',
        entities=(f"widget:{code}",),
    )
    proposed = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="未承認。",
        condition="true",
        entities=(f"widget:{code}",),
        status=Status.PROPOSED,
    )

    ids = _ids(_apply(world, _facts(code)))

    assert false not in ids and proposed not in ids


def test_a_sibling_scope_never_applies(world: World) -> None:
    code = _code()
    sibling = _rule(
        world,
        scope="jp/finance/q2c-cash-application",
        enforcement="mandatory",
        text_ja="入金消込チームのルール。",
        condition="true",
        entities=(f"widget:{code}",),
    )

    assert sibling not in _ids(_apply(world, _facts(code)))


def test_a_condition_that_cannot_be_evaluated_is_reported_not_dropped(world: World) -> None:
    code = _code()
    rule = _rule(
        world,
        scope="jp",
        enforcement="mandatory",
        text_ja="数量の確認。",
        condition="order.qty > 0",
        entities=(f"widget:{code}",),
    )
    facts = _facts(code)
    broken = CaseFacts(
        facts.entities, {"order": {k: v for k, v in facts.context["order"].items() if k != "qty"}}
    )

    result = _apply(world, broken)

    assert rule not in _ids(result)
    assert [(u.record_id, u.enforcement) for u in result.unevaluable if u.record_id == rule] == [
        (rule, "mandatory")
    ]


def test_case_facts_come_from_the_pack(world: World) -> None:
    case = {
        "order_id": "O-1",
        "widget_code": "W-1",
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }

    facts = case_facts(PACK, case)

    assert facts.entities == ("widget:W-1",)
    assert facts.context["order"]["order_date"] == dt.date(2026, 11, 2)
