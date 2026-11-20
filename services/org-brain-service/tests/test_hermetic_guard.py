"""The default test run must not reach the network (docs/adr/0001-test-tiers.md)."""

import socket

import pytest
from arbitr_test_utils.network import HermeticTestViolationError


def test_socket_to_non_loopback_is_blocked() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(HermeticTestViolationError),
    ):
        sock.connect(("10.255.255.1", 5432))


def test_dns_for_compose_hostname_is_blocked() -> None:
    with pytest.raises(HermeticTestViolationError):
        socket.getaddrinfo("postgres", 5432)
