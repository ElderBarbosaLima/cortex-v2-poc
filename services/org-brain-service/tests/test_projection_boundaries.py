"""Architecture guard (D12): the ledger and the gates never depend on a concrete
search index. Only the projection package, the workers and the CLIs may import
PostgresSearchProjection; an Elasticsearch adapter must be able to replace it
without touching the core."""

import re
from pathlib import Path

import pytest

from app.workers import handlers
from app.workers.outbox import register

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED = ("projections", "workers", "cli")


def test_core_modules_do_not_import_the_postgres_search_projection() -> None:
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if p.relative_to(APP).parts[0] not in ALLOWED
        and re.search(r"postgres_search|PostgresSearchProjection", p.read_text())
    ]

    assert offenders == []


def test_an_event_type_cannot_get_two_handlers() -> None:
    assert handlers.project_record  # registered on import

    with pytest.raises(ValueError, match="already has a handler"):
        register("record.appended")(handlers.project_record)
