"""Shared fixtures. Hermetic by default (see docs/adr/0001-test-tiers.md).

The arbitr-test-utils plugin installs the fail-closed socket/DNS guard for every
test not marked `integration`, as in the monorepo.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import OrgBrainConfig

pytest_plugins = ["arbitr_test_utils.pytest_plugin"]

# Never read a developer's .env in tests.
OrgBrainConfig.model_config["env_file"] = None
os.environ.setdefault("LOG_FORMAT", "text")

from app.main import create_app  # noqa: E402 -- env must be set before the app is built
from app.models import Base  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def sqlite_session() -> Iterator[Session]:
    """In-memory SQLite with the ledger schema (no RLS, no triggers: those are
    integration tests). One StaticPool connection, so every checkout shares it."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
