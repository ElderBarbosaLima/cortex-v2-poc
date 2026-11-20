"""Pytest plugin providing automated hermeticity and database fixtures for Arbitr services.

Usage:
    In your service's root ``tests/conftest.py``:

    ```python
    pytest_plugins = ["arbitr_test_utils.pytest_plugin"]
    ```

    This automatically:
    - Registers markers (``@pytest.mark.fresh_db``, ``@pytest.mark.allow_network``, ``@pytest.mark.integration``).
    - Rewrites single-label docker compose hosts to 127.0.0.1 during pytest configuration.
    - Installs an autouse fail-closed socket and DNS guard across all tests, preventing
      unmocked connections or corporate DNS queries to 10.135.78.5.
    - Temporarily permits network access for tests explicitly marked ``@pytest.mark.integration``
      or ``@pytest.mark.allow_network``.

Session Fixtures & Network Access:
    Because the fail-closed guard is active at session scope, any session-scoped fixture that
    authenticates or communicates with external services (e.g. in an integration test suite)
    must explicitly permit network calls using either the decorator or context manager:

    ```python
    from arbitr_test_utils.network import allow_network

    @pytest.fixture(scope="session")
    @allow_network()
    def live_service_client():
        return ExternalClient(...)

    # Or as a context manager inside the fixture body:
    @pytest.fixture(scope="session")
    def live_service_client():
        with allow_network():
            client = ExternalClient(...)
            client.connect()
        yield client
    ```
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from arbitr_test_utils.env import rewrite_compose_hosts
from arbitr_test_utils.network import (
    allow_network,
    install_socket_guard,
    uninstall_socket_guard,
)


def pytest_configure(config: pytest.Config) -> None:
    """Register markers and apply automatic host rewriting when pytest initializes."""
    config.addinivalue_line(
        "markers",
        "fresh_db: provision a dedicated unshared SQLite engine for this test instead of savepoints",
    )
    config.addinivalue_line(
        "markers",
        "allow_network: temporarily permit outbound network connections for this test",
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring real external services or network",
    )
    # Ensure compose network names in env vars are rewritten to localhost / loopback
    rewrite_compose_hosts(target_host="127.0.0.1")
    # Install socket and DNS guards early so import-time network activity is guarded
    install_socket_guard()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore pristine socket and DNS functions when pytest shuts down."""
    uninstall_socket_guard()


@pytest.fixture(scope="session", autouse=True)
def _session_socket_guard_lifecycle() -> Generator[None, None, None]:
    """Install socket and DNS guards once per test session and uninstall on exit."""
    install_socket_guard()
    try:
        yield
    finally:
        uninstall_socket_guard()


@pytest.fixture(autouse=True)
def _hermetic_socket_guard_fixture(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Autouse fixture enabling fail-closed socket guards.

    Bypasses guard for tests marked @pytest.mark.integration or @pytest.mark.allow_network.
    """
    if request.node.get_closest_marker(
        "integration"
    ) or request.node.get_closest_marker("allow_network"):
        with allow_network():
            yield
    else:
        yield
