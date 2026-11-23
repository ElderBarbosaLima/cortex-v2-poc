"""Derived outcome (Q5) and the named-owner lookup the API shows (issue 18)."""

from __future__ import annotations

import json

import pytest

from app.seed.tenants import load_seed, named_owners
from app.services.review import outcome
from tests.test_identity_and_seed import SEED_DIR


@pytest.mark.parametrize(
    ("status", "ever_approved", "expected"),
    [
        ("proposed", False, "pending"),
        ("approved", True, "approved"),
        ("stale", True, "approved"),
        ("superseded", False, "superseded"),
        ("superseded", True, "superseded"),
        ("deprecated", False, "rejected"),  # never approved: a rejection
        ("deprecated", True, "retired"),  # approved once: a retirement
        ("erased", True, "retired"),
    ],
)
def test_outcome_comes_from_history(status: str, ever_approved: bool, expected: str) -> None:
    assert outcome(status, ever_approved) == expected


def test_each_team_scope_has_a_named_owner_and_backup() -> None:
    seed = load_seed(SEED_DIR)
    jp = next(t for t in seed.tenants if t.key == "jp")

    for team in ("jp/finance/q2c-invoicing", "jp/finance/q2c-cash-application"):
        assert named_owners(jp, seed.node_ids[team]) == {
            "owner": seed.people["OWN-001"],
            "backup": seed.people["OWN-002"],
        }
    # The group (tenant root) has its own owners since issue 50, so jp/finance falls back to them.
    assert named_owners(jp, seed.node_ids["jp/finance"]) == {
        "owner": seed.people["GRP-001"],
        "backup": seed.people["GRP-002"],
    }
    assert seed.people["X-001"] not in json.dumps(jp.opa_data["owners"])  # other tenant's owner
