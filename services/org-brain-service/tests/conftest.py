"""Shared fixtures. Hermetic by default (see docs/adr/0001-test-tiers.md).

The arbitr-test-utils plugin installs the fail-closed socket/DNS guard for every
test not marked `integration`, as in the monorepo.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import OrgBrainConfig

pytest_plugins = ["arbitr_test_utils.pytest_plugin"]

# Never read a developer's .env in tests.
OrgBrainConfig.model_config["env_file"] = None
os.environ.setdefault("LOG_FORMAT", "text")

from app.main import create_app  # noqa: E402 -- env must be set before the app is built


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
