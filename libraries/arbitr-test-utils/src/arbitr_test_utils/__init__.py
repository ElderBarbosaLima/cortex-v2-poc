"""arbitr_test_utils: Shared hermetic testing utilities for Arbitr services.

Provides:
- In-memory SQLite savepoints with zero cross-test pollution (``create_savepoint_engine``, ``savepoint_session``).
- Fail-closed network and DNS interceptor blocking leaked sockets (``install_socket_guard``, ``allow_network``).
- Compose hostname rewrite and environment isolation (``rewrite_compose_hosts``, ``isolate_env_defaults``).
- Optional pytest plugin: ``pytest_plugins = ["arbitr_test_utils.pytest_plugin"]``.
"""

try:
    from arbitr_test_utils.db import (
        create_fresh_engine,
        create_savepoint_engine,
        is_fresh_db_requested,
        register_sqlite_emulations,
        savepoint_session,
    )
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    create_fresh_engine = None  # type: ignore[assignment]
    create_savepoint_engine = None  # type: ignore[assignment]
    is_fresh_db_requested = None  # type: ignore[assignment]
    register_sqlite_emulations = None  # type: ignore[assignment]
    savepoint_session = None  # type: ignore[assignment]

from arbitr_test_utils.env import (
    clear_lru_caches,
    is_compose_name,
    is_in_container,
    isolate_env_defaults,
    load_service_env,
    rewrite_compose_hosts,
)
from arbitr_test_utils.network import (
    HermeticTestViolationError,
    allow_network,
    install_socket_guard,
    is_allowed_host,
    uninstall_socket_guard,
)

__all__ = [
    "HermeticTestViolationError",
    "allow_network",
    "clear_lru_caches",
    "create_fresh_engine",
    "create_savepoint_engine",
    "install_socket_guard",
    "is_allowed_host",
    "is_compose_name",
    "is_fresh_db_requested",
    "is_in_container",
    "isolate_env_defaults",
    "load_service_env",
    "register_sqlite_emulations",
    "rewrite_compose_hosts",
    "savepoint_session",
    "uninstall_socket_guard",
]
