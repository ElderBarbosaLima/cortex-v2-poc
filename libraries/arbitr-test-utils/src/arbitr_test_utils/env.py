"""Environment isolation and host rewriting utilities for hermetic tests.

Ensures that tests running outside Docker containers do not attempt to contact
single-label compose service names (e.g. redis, postgres, client-service) that
resolve via corporate / VPN DNS search paths to blackholed internal IPs.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def is_in_container() -> bool:
    """Check if the current process is running inside a Docker or Podman container."""
    return _IN_CONTAINER


def is_compose_name(host: str | None) -> bool:
    """Return True if host looks like a single-label docker-compose service name.

    IP literals, localhost, and qualified FQDNs with dots return False.
    'host.docker.internal' returns True.
    """
    if not host or host in ("localhost", "127.0.0.1", "::1"):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP literal: already explicit
    except ValueError:
        pass
    if host == "host.docker.internal":
        return True
    return (
        "." not in host
    )  # single-label compose name (e.g. redis, postgres, client-service)


def rewrite_compose_hosts(
    target_host: str = "127.0.0.1",
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Rewrite single-label compose hosts in _HOST and _URL env vars to target_host.

    Mutates os.environ by default (or the supplied env dict) when running outside
    containers. Returns a dictionary of all modified {var_name: new_value}.
    """
    if (env is None or env is os.environ) and is_in_container():
        return {}

    target_env = os.environ if env is None else env
    rewritten: dict[str, str] = {}

    for var, value in list(target_env.items()):
        if not value or not var.endswith(("_HOST", "_URL")):
            continue

        if "://" in value:
            parts = urlparse(value)
            if is_compose_name(parts.hostname):
                user_info = ""
                if parts.username:
                    user_info = parts.username
                    if parts.password:
                        user_info += f":{parts.password}"
                    user_info += "@"
                port = f":{parts.port}" if parts.port else ""
                new_netloc = f"{user_info}{target_host}{port}"
                new_value = urlunparse(parts._replace(netloc=new_netloc))
                target_env[var] = new_value
                rewritten[var] = new_value
        else:
            host, sep, port = value.partition(":")
            if is_compose_name(host):
                new_value = f"{target_host}{sep}{port}" if sep else target_host
                target_env[var] = new_value
                rewritten[var] = new_value

    return rewritten


def load_service_env(env_path: str | Path | None = None) -> bool:
    """Load .env file if available, looking relative to cwd or specified path."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        return False

    if env_path:
        p = Path(env_path).resolve()
        if p.is_file():
            if p.name == ".env":
                load_dotenv(p)
                return True
            # If passed a file like conftest.py, check parent directories for .env
            for parent in [p.parent, p.parent.parent]:
                candidate = parent / ".env"
                if candidate.is_file():
                    load_dotenv(candidate)
                    return True
        elif p.is_dir():
            candidate = p / ".env"
            if candidate.is_file():
                load_dotenv(candidate)
                return True
    else:
        p = Path.cwd() / ".env"
        if p.is_file():
            load_dotenv(p)
            return True

    return False


def isolate_env_defaults(
    *,
    closed_port: int = 1,
    loopback_ip: str = "127.0.0.1",
    blank_urls: Sequence[str] = (),
    isolate_db: bool = True,
    env: dict[str, str] | None = None,
) -> None:
    """Configure strict hermetic default environment variables.

    - Rewrites compose hosts to loopback_ip.
    - Sets specified service URLs to "" (empty string) to trigger early guards.
    - Points DB_HOST / DB_PORT to closed loopback ports if isolate_db is True.
    """
    target_env = os.environ if env is None else env
    rewrite_compose_hosts(target_host=loopback_ip, env=env)

    for url_var in blank_urls:
        target_env[url_var] = ""

    if isolate_db:
        target_env["DB_HOST"] = loopback_ip
        target_env["DB_PORT"] = str(closed_port)


def clear_lru_caches(*functions: Callable[..., Any]) -> None:
    """Clear @functools.lru_cache on all passed functions if they have cache_clear."""
    for fn in functions:
        clear = getattr(fn, "cache_clear", None)
        if callable(clear):
            clear()
