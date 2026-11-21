"""Ledger commands on SQLite: transitions, the model ceiling, outbox rows, atomicity."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Kind, MemoryRecord, Outbox, Status
from app.services import ledger
from app.services.body_codec import PlainJsonCodec, body_hash
from tests.factories import SCOPE, TENANT_A

PERSON = {"approver_type": "user", "user": "OWN-001", "decision_id": "d-1"}


def draft(**overrides: Any) -> ledger.RecordDraft:
    fields: dict[str, Any] = {
        "kind": Kind.RULE,
        "schema_version": "test.rule/1",
        "body": {"text_ja": "登録番号を記載する", "targets": ["invoice.registration_number"]},
        "scope": SCOPE,
        "asserted_by": {"type": "user", "id": "R-001"},
        "authority": 10,
        "source": {"system": "test"},
        "valid_from": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        "enforcement": "mandatory",
    }
    fields.update(overrides)
    return ledger.RecordDraft(**fields)


def _count(session: Session, model: type[Any]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_append_writes_version_1_and_one_outbox_row(sqlite_session: Session) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    assert (row.version, row.status, row.body_codec) == (1, "proposed", "plain-json/1")
    assert row.body_hash == body_hash(draft().body)
    assert ledger.open_body(sqlite_session, row) == draft().body
    event = sqlite_session.scalars(select(Outbox)).one()
    assert event.payload == {
        "record_id": row.record_id,
        "version": 1,
        "status": "proposed",
        "kind": "rule",
    }


def test_approval_appends_a_new_version_and_may_set_authority(sqlite_session: Session) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    approved = ledger.transition(
        sqlite_session,
        record_id=row.record_id,
        to=Status.APPROVED,
        approved_by=PERSON,
        authority=30,
    )

    assert (approved.version, approved.status, approved.authority) == (2, "approved", 30)
    assert approved.approved_by == PERSON
    assert approved.body_sealed == row.body_sealed
    assert _count(sqlite_session, MemoryRecord) == 2
    assert _count(sqlite_session, Outbox) == 2


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([], Status.STALE),  # proposed cannot go stale
        ([Status.DEPRECATED], Status.APPROVED),  # rejected/retired never comes back
        ([Status.ERASED], Status.DEPRECATED),  # erased is terminal
    ],
)
def test_illegal_transitions_are_refused(
    sqlite_session: Session, path: list[Status], illegal: Status
) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())
    for step in path:
        ledger.transition(sqlite_session, record_id=row.record_id, to=step)

    with pytest.raises(ledger.IllegalTransitionError):
        ledger.transition(sqlite_session, record_id=row.record_id, to=illegal, approved_by=PERSON)


def test_approval_requires_an_approver(sqlite_session: Session) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    with pytest.raises(ledger.IllegalTransitionError, match="approved_by"):
        ledger.transition(sqlite_session, record_id=row.record_id, to=Status.APPROVED)


def test_only_an_approval_changes_authority(sqlite_session: Session) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    with pytest.raises(ledger.IllegalTransitionError, match="only an approval"):
        ledger.transition(
            sqlite_session, record_id=row.record_id, to=Status.DEPRECATED, authority=40
        )


def test_a_model_cannot_assert_above_the_ceiling(sqlite_session: Session) -> None:
    with pytest.raises(ledger.ModelCeilingError):
        ledger.append_new(
            sqlite_session,
            tenant_id=TENANT_A,
            draft=draft(asserted_by={"type": "model", "id": "m"}, authority=20),
        )
    assert _count(sqlite_session, MemoryRecord) == 0


def test_a_person_approving_lifts_a_model_proposal_above_the_ceiling(
    sqlite_session: Session,
) -> None:
    row = ledger.append_new(
        sqlite_session,
        tenant_id=TENANT_A,
        draft=draft(asserted_by={"type": "model", "id": "m"}, authority=10),
    )

    approved = ledger.transition(
        sqlite_session,
        record_id=row.record_id,
        to=Status.APPROVED,
        approved_by=PERSON,
        authority=30,
    )
    policy_approval = {"approver_type": "policy", "policy": "auto-v1"}
    second = ledger.append_new(
        sqlite_session,
        tenant_id=TENANT_A,
        draft=draft(asserted_by={"type": "model", "id": "m"}, authority=10),
    )

    assert approved.authority == 30
    with pytest.raises(ledger.ModelCeilingError):
        ledger.transition(
            sqlite_session,
            record_id=second.record_id,
            to=Status.APPROVED,
            approved_by=policy_approval,
            authority=30,
        )


def test_supersede_links_the_new_record_and_closes_the_old(sqlite_session: Session) -> None:
    old = ledger.append_new(
        sqlite_session,
        tenant_id=TENANT_A,
        draft=draft(),
        status=Status.APPROVED,
        approved_by=PERSON,
    )

    new = ledger.supersede(
        sqlite_session,
        tenant_id=TENANT_A,
        old_record_id=old.record_id,
        draft=draft(body={"text_ja": "新しい版"}),
        approved_by=PERSON,
    )

    assert new.record_id != old.record_id
    assert (new.supersedes, new.status, new.version) == (old.record_id, "approved", 1)
    assert ledger.latest(sqlite_session, old.record_id).status == "superseded"
    assert _count(sqlite_session, Outbox) == 3  # old v1, old v2 (superseded), new v1


def test_commands_flush_but_never_commit(sqlite_session: Session) -> None:
    ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    sqlite_session.rollback()

    assert _count(sqlite_session, MemoryRecord) == 0
    assert _count(sqlite_session, Outbox) == 0


def test_unknown_record_is_not_found(sqlite_session: Session) -> None:
    with pytest.raises(ledger.RecordNotFoundError):
        ledger.transition(
            sqlite_session, record_id="00000000-0000-0000-0000-000000000000", to=Status.DEPRECATED
        )


def test_business_time_window() -> None:
    row = MemoryRecord(
        valid_from=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
        valid_to=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
    )

    assert not ledger.is_valid_at(row, dt.datetime(2026, 11, 30, tzinfo=dt.UTC))
    assert ledger.is_valid_at(row, dt.datetime(2026, 12, 1, tzinfo=dt.UTC))
    assert not ledger.is_valid_at(
        row, dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    )  # valid_to is exclusive


def test_opening_with_the_wrong_codec_is_refused(sqlite_session: Session) -> None:
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft())

    class OtherCodec(PlainJsonCodec):
        name = "aes-gcm/1"

    with pytest.raises(ValueError, match="sealed with plain-json/1"):
        ledger.open_body(sqlite_session, row, OtherCodec())
