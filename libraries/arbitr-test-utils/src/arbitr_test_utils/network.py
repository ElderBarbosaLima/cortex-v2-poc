"""Fail-closed network interceptor for hermetic tests.

Blocks any socket connections and DNS resolution to non-loopback addresses,
preventing slow corporate DNS queries (e.g. to 10.135.78.5) and unmocked
external HTTP/TCP calls from stalling or polluting test runs.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections.abc import Callable, Generator, Sequence
from typing import Any


class HermeticTestViolationError(RuntimeError):
    """Raised when test code attempts a network connection or DNS resolution to a non-loopback host."""


_DEFAULT_ALLOWED: set[str] = {"localhost", "127.0.0.1", "::1"}
_ORIGINAL_CONNECT: Callable[..., Any] | None = None
_ORIGINAL_CONNECT_EX: Callable[..., Any] | None = None
_ORIGINAL_GETADDRINFO: Callable[..., Any] | None = None
_ORIGINAL_GETHOSTBYNAME: Callable[..., Any] | None = None
_ORIGINAL_GETHOSTBYNAME_EX: Callable[..., Any] | None = None

_GUARD_ENABLED: bool = False
_ALLOWED_HOSTS: set[str] = set(_DEFAULT_ALLOWED)


def _normalize_host(host: Any) -> str | None:
    if host is None:
        return None
    if isinstance(host, (bytes, bytearray)):
        return host.decode("utf-8", "ignore")
    return str(host)


def is_allowed_host(host: str | None, allowed: set[str] | None = None) -> bool:
    """Return True if host is localhost or a valid loopback IP address."""
    norm = _normalize_host(host)
    if not norm:
        return True
    active_allowed = allowed if allowed is not None else _ALLOWED_HOSTS
    if norm in active_allowed:
        return True
    try:
        ip = ipaddress.ip_address(norm)
        return ip.is_loopback
    except ValueError:
        return False


def _resolve_and_record_ips(host: str, target_set: set[str]) -> None:
    """Helper to resolve a hostname using original getaddrinfo and record resolved IPs."""
    resolver = _ORIGINAL_GETADDRINFO or socket.getaddrinfo
    try:
        results = resolver(host, 0)
        for r in results:
            if len(r) >= 5 and isinstance(r[4], (tuple, list)) and len(r[4]) >= 1:
                ip_str = _normalize_host(r[4][0])
                if ip_str:
                    target_set.add(ip_str)
    except Exception:
        pass


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    norm_host = _normalize_host(host)
    if _GUARD_ENABLED and norm_host and not is_allowed_host(norm_host):
        raise HermeticTestViolationError(
            f"Hermetic test violation: attempted DNS resolution for '{norm_host}' (port {port}). "
            "Tests must mock network clients (respx, unittest.mock, AsyncMock) "
            "or use loopback addresses (127.0.0.1)."
        )
    assert _ORIGINAL_GETADDRINFO is not None
    results = _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)
    # Automatically add resolved IPs to _ALLOWED_HOSTS so subsequent socket.connect to the resolved IP succeeds
    for r in results:
        if len(r) >= 5 and isinstance(r[4], (tuple, list)) and len(r[4]) >= 1:
            ip_str = _normalize_host(r[4][0])
            if ip_str:
                _ALLOWED_HOSTS.add(ip_str)
    return results


def _guarded_gethostbyname(host: Any) -> Any:
    norm_host = _normalize_host(host)
    if _GUARD_ENABLED and norm_host and not is_allowed_host(norm_host):
        raise HermeticTestViolationError(
            f"Hermetic test violation: attempted DNS resolution for '{norm_host}'. "
            "Tests must mock network clients (respx, unittest.mock, AsyncMock) "
            "or use loopback addresses (127.0.0.1)."
        )
    assert _ORIGINAL_GETHOSTBYNAME is not None
    ip = _ORIGINAL_GETHOSTBYNAME(host)
    if isinstance(ip, str):
        _ALLOWED_HOSTS.add(ip)
    return ip


def _guarded_gethostbyname_ex(host: Any) -> Any:
    norm_host = _normalize_host(host)
    if _GUARD_ENABLED and norm_host and not is_allowed_host(norm_host):
        raise HermeticTestViolationError(
            f"Hermetic test violation: attempted DNS resolution for '{norm_host}'. "
            "Tests must mock network clients (respx, unittest.mock, AsyncMock) "
            "or use loopback addresses (127.0.0.1)."
        )
    assert _ORIGINAL_GETHOSTBYNAME_EX is not None
    name, aliases, ip_list = _ORIGINAL_GETHOSTBYNAME_EX(host)
    for ip in ip_list:
        if isinstance(ip, str):
            _ALLOWED_HOSTS.add(ip)
    return name, aliases, ip_list


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    if _GUARD_ENABLED and isinstance(address, (tuple, list)) and len(address) >= 2:
        host, port = _normalize_host(address[0]), address[1]
        if host and not is_allowed_host(host):
            raise HermeticTestViolationError(
                f"Hermetic test violation: attempted socket connect to {host}:{port}. "
                "Tests must mock network clients (respx, unittest.mock, AsyncMock) "
                "or use loopback addresses (127.0.0.1)."
            )
    assert _ORIGINAL_CONNECT is not None
    return _ORIGINAL_CONNECT(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    if _GUARD_ENABLED and isinstance(address, (tuple, list)) and len(address) >= 2:
        host, port = _normalize_host(address[0]), address[1]
        if host and not is_allowed_host(host):
            raise HermeticTestViolationError(
                f"Hermetic test violation: attempted socket connect_ex to {host}:{port}. "
                "Tests must mock network clients (respx, unittest.mock, AsyncMock) "
                "or use loopback addresses (127.0.0.1)."
            )
    assert _ORIGINAL_CONNECT_EX is not None
    return _ORIGINAL_CONNECT_EX(self, address)


def install_socket_guard(allowed_hosts: Sequence[str] | None = None) -> None:
    """Install fail-closed socket and DNS guards globally."""
    global _ORIGINAL_CONNECT, _ORIGINAL_CONNECT_EX, _ORIGINAL_GETADDRINFO
    global _ORIGINAL_GETHOSTBYNAME, _ORIGINAL_GETHOSTBYNAME_EX, _GUARD_ENABLED, _ALLOWED_HOSTS

    _ALLOWED_HOSTS = set(_DEFAULT_ALLOWED)
    if allowed_hosts is not None:
        for h in allowed_hosts:
            norm = _normalize_host(h)
            if norm:
                _ALLOWED_HOSTS.add(norm)
                _resolve_and_record_ips(norm, _ALLOWED_HOSTS)

    if _ORIGINAL_CONNECT is None:
        _ORIGINAL_CONNECT = socket.socket.connect
        socket.socket.connect = _guarded_connect  # type: ignore[assignment]

    if _ORIGINAL_CONNECT_EX is None:
        _ORIGINAL_CONNECT_EX = socket.socket.connect_ex
        socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[assignment]

    if _ORIGINAL_GETADDRINFO is None:
        _ORIGINAL_GETADDRINFO = socket.getaddrinfo
        socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]

    if _ORIGINAL_GETHOSTBYNAME is None:
        _ORIGINAL_GETHOSTBYNAME = socket.gethostbyname
        socket.gethostbyname = _guarded_gethostbyname  # type: ignore[assignment]

    if _ORIGINAL_GETHOSTBYNAME_EX is None:
        _ORIGINAL_GETHOSTBYNAME_EX = socket.gethostbyname_ex
        socket.gethostbyname_ex = _guarded_gethostbyname_ex  # type: ignore[assignment]

    _GUARD_ENABLED = True


def uninstall_socket_guard() -> None:
    """Restore original socket and DNS functions."""
    global _ORIGINAL_CONNECT, _ORIGINAL_CONNECT_EX, _ORIGINAL_GETADDRINFO
    global _ORIGINAL_GETHOSTBYNAME, _ORIGINAL_GETHOSTBYNAME_EX, _GUARD_ENABLED

    _GUARD_ENABLED = False
    if _ORIGINAL_CONNECT is not None:
        socket.socket.connect = _ORIGINAL_CONNECT  # type: ignore[assignment]
        _ORIGINAL_CONNECT = None
    if _ORIGINAL_CONNECT_EX is not None:
        socket.socket.connect_ex = _ORIGINAL_CONNECT_EX  # type: ignore[assignment]
        _ORIGINAL_CONNECT_EX = None
    if _ORIGINAL_GETADDRINFO is not None:
        socket.getaddrinfo = _ORIGINAL_GETADDRINFO  # type: ignore[assignment]
        _ORIGINAL_GETADDRINFO = None
    if _ORIGINAL_GETHOSTBYNAME is not None:
        socket.gethostbyname = _ORIGINAL_GETHOSTBYNAME  # type: ignore[assignment]
        _ORIGINAL_GETHOSTBYNAME = None
    if _ORIGINAL_GETHOSTBYNAME_EX is not None:
        socket.gethostbyname_ex = _ORIGINAL_GETHOSTBYNAME_EX  # type: ignore[assignment]
        _ORIGINAL_GETHOSTBYNAME_EX = None


@contextlib.contextmanager
def allow_network(
    allowed_hosts: Sequence[str] | None = None,
) -> Generator[None, None, None]:
    """Temporarily allow specific hosts or all network access within a block."""
    global _GUARD_ENABLED, _ALLOWED_HOSTS
    previous_state = _GUARD_ENABLED
    previous_allowed = set(_ALLOWED_HOSTS)
    try:
        if allowed_hosts is None:
            _GUARD_ENABLED = False
        else:
            new_allowed = set(previous_allowed)
            for h in allowed_hosts:
                norm = _normalize_host(h)
                if norm:
                    new_allowed.add(norm)
                    _resolve_and_record_ips(norm, new_allowed)
            _ALLOWED_HOSTS = new_allowed
        yield
    finally:
        _GUARD_ENABLED = previous_state
        _ALLOWED_HOSTS = previous_allowed
