"""Machines propose, people approve, enforced by the database (issue 34)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.db import app_engine, tenant_session
from app.models import Kind, Status
from app.services import ledger

pytestmark = pytest.mark.integration
TENANT = str(uuid.uuid4())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> Iterator[None]:
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
    yield
    app_engine().dispose()


def _draft(actor: str) -> ledger.RecordDraft:
    return ledger.RecordDraft(
        kind=Kind.RULE,
        schema_version="t.rule/1",
        body={"text_ja": "x", "targets": ["t"], "condition": "true"},
        scope=TENANT,
        asserted_by={"type": actor, "id": "m"},
        authority=10,
        source={},
        valid_from=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        enforcement="default",
    )


def _append(actor: str, **kw: Any) -> str:
    with allow_network(), tenant_session(TENANT) as s:
        return ledger.append_new(s, tenant_id=TENANT, draft=_draft(actor), **kw).record_id


@pytest.mark.parametrize("actor", ["model", "policy"])
def test_a_machine_record_cannot_be_born_approved(actor: str) -> None:
    with pytest.raises(DBAPIError, match="starts as proposed"):
        _append(
            actor, status=Status.APPROVED, approved_by={"approver_type": "user", "user_id": "u"}
        )


@pytest.mark.parametrize("actor", ["model", "policy"])
def test_a_machine_proposal_is_approved_only_by_a_person(actor: str) -> None:
    rid = _append(actor)
    with (
        pytest.raises(DBAPIError, match="approved only by a person"),
        allow_network(),
        tenant_session(TENANT) as s,
    ):
        ledger.transition(
            s, record_id=rid, to=Status.APPROVED, approved_by={"approver_type": "model", "id": "m"}
        )
    with allow_network(), tenant_session(TENANT) as s:
        done = ledger.transition(
            s,
            record_id=rid,
            to=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": "u"},
        )
    assert done.status == "approved"


def test_people_and_imports_are_untouched() -> None:
    assert _append(
        "user", status=Status.APPROVED, approved_by={"approver_type": "user", "user_id": "u"}
    )
    assert _append(
        "import", status=Status.APPROVED, approved_by={"approver_type": "user", "user_id": "u"}
    )
