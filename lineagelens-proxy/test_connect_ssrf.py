"""Tests for M1: SSRF hostname bypass fix in connect_tunnel._resolve_and_validate_host.

Covers:
- hostname that resolves to 127.0.0.1 (loopback) → blocked
- hostname that resolves to 169.254.169.254 (link-local) → blocked
- hostname that resolves to 10.0.0.1 (private) → blocked
- literal loopback IP → blocked
- literal link-local IP → blocked
- literal private IP → blocked
- hostname where ALL resolved IPs are public → allowed (pinned IP returned)
- hostname that fails DNS resolution → blocked
- blocked hostname literal → blocked
"""
import asyncio
import socket
from unittest.mock import patch

import pytest

from connect_tunnel import _resolve_and_validate_host


def _run(coro):
    return asyncio.run(coro)


# ── Literal IP checks ──────────────────────────────────────────────────────────

def test_literal_loopback_blocked():
    assert _run(_resolve_and_validate_host("127.0.0.1")) is None


def test_literal_link_local_blocked():
    assert _run(_resolve_and_validate_host("169.254.169.254")) is None


def test_literal_private_blocked():
    assert _run(_resolve_and_validate_host("10.0.0.1")) is None


def test_literal_private_172_blocked():
    assert _run(_resolve_and_validate_host("172.16.0.1")) is None


def test_literal_private_192_blocked():
    assert _run(_resolve_and_validate_host("192.168.1.1")) is None


def test_literal_loopback_ipv6_blocked():
    assert _run(_resolve_and_validate_host("::1")) is None


def test_literal_public_allowed():
    result = _run(_resolve_and_validate_host("1.1.1.1"))
    assert result == "1.1.1.1"


def test_blocklist_hostname():
    assert _run(_resolve_and_validate_host("localhost")) is None


# ── Hostname resolution checks (mocked) ───────────────────────────────────────

def _mock_getaddrinfo(ip: str):
    """Return a getaddrinfo patcher that resolves any host to *ip*."""
    return patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))],
    )


def test_hostname_resolves_to_loopback_blocked():
    with _mock_getaddrinfo("127.0.0.1"):
        assert _run(_resolve_and_validate_host("evil-internal.example.com")) is None


def test_hostname_resolves_to_link_local_blocked():
    with _mock_getaddrinfo("169.254.169.254"):
        assert _run(_resolve_and_validate_host("metadata.example.com")) is None


def test_hostname_resolves_to_private_10x_blocked():
    with _mock_getaddrinfo("10.0.0.5"):
        assert _run(_resolve_and_validate_host("internal.corp.example.com")) is None


def test_hostname_resolves_to_private_192_blocked():
    with _mock_getaddrinfo("192.168.0.1"):
        assert _run(_resolve_and_validate_host("router.local.example.com")) is None


def test_hostname_resolves_to_public_allowed():
    with _mock_getaddrinfo("93.184.216.34"):
        result = _run(_resolve_and_validate_host("example.com"))
    assert result == "93.184.216.34"


def test_hostname_any_internal_resolves_blocks_all():
    """If ANY of the resolved IPs is internal the host must be blocked."""
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ],
    ):
        assert _run(_resolve_and_validate_host("mixed.example.com")) is None


def test_hostname_dns_failure_blocked():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
        assert _run(_resolve_and_validate_host("does-not-exist.example.invalid")) is None


def test_pinned_ip_returned_not_hostname():
    """Caller should connect to the returned IP, not re-resolve the hostname."""
    with _mock_getaddrinfo("93.184.216.34"):
        result = _run(_resolve_and_validate_host("example.com"))
    assert result == "93.184.216.34"
    assert result != "example.com"
