"""Tests for L5: right-to-left XFF chain walk in effective_client_ip.

Verifies that a spoofed leftmost X-Forwarded-For entry cannot override the
IP identified by the innermost trusted proxy.
"""
from __future__ import annotations

import pytest

from app.core.rate_limit import effective_client_ip


TRUSTED = "10.0.0.1,10.0.0.2"


# ── basic behaviour ───────────────────────────────────────────────────────────

def test_no_trusted_proxies_returns_peer() -> None:
    # With no trusted proxies configured, always use the direct peer.
    assert effective_client_ip("5.5.5.5", "1.1.1.1, 2.2.2.2", "", None) == "5.5.5.5"


def test_untrusted_peer_returns_peer_regardless_of_xff() -> None:
    # Peer is not in the trusted list → ignore XFF.
    result = effective_client_ip("99.99.99.99", "1.1.1.1", "", TRUSTED)
    assert result == "99.99.99.99"


def test_trusted_peer_single_xff_entry() -> None:
    # Peer is trusted, single XFF entry that is public → return it.
    result = effective_client_ip("10.0.0.1", "203.0.113.5", "", TRUSTED)
    assert result == "203.0.113.5"


def test_trusted_peer_returns_real_ip_fallback_when_xff_empty() -> None:
    result = effective_client_ip("10.0.0.1", "", "203.0.113.7", TRUSTED)
    assert result == "203.0.113.7"


def test_trusted_peer_returns_peer_when_xff_and_real_ip_both_empty() -> None:
    result = effective_client_ip("10.0.0.1", "", "", TRUSTED)
    assert result == "10.0.0.1"


# ── multi-hop chain (the key regression tests) ────────────────────────────────

def test_multi_hop_returns_first_untrusted_from_right() -> None:
    # XFF: spoofed_client, real_client, internal_lb
    # Peer: 10.0.0.1 (trusted)
    # Walking right-to-left: 10.0.0.2 (trusted skip), 1.2.3.4 (not trusted → return)
    xff = "spoofed_192.168.0.99, 1.2.3.4, 10.0.0.2"
    result = effective_client_ip("10.0.0.1", xff, "", TRUSTED)
    assert result == "1.2.3.4"


def test_attacker_cannot_spoof_via_leftmost_xff_entry() -> None:
    # Attacker sets XFF: 127.0.0.1 (hoping to impersonate localhost)
    # Real chain (added by trusted proxy): real_client_ip, 10.0.0.1
    xff = "127.0.0.1, 203.0.113.42, 10.0.0.1"
    result = effective_client_ip("10.0.0.1", xff, "", TRUSTED)
    # Must return 203.0.113.42, not the spoofed 127.0.0.1
    assert result == "203.0.113.42"
    assert result != "127.0.0.1"


def test_all_xff_trusted_falls_back_to_real_ip() -> None:
    # Unusual: entire chain is trusted proxies
    xff = "10.0.0.2, 10.0.0.1"
    result = effective_client_ip("10.0.0.1", xff, "203.0.113.9", TRUSTED)
    assert result == "203.0.113.9"


def test_all_xff_trusted_no_real_ip_falls_back_to_peer() -> None:
    xff = "10.0.0.2, 10.0.0.1"
    result = effective_client_ip("10.0.0.1", xff, "", TRUSTED)
    assert result == "10.0.0.1"


def test_none_peer_with_xff() -> None:
    # peer_host is None (e.g. UNIX socket)
    result = effective_client_ip(None, "203.0.113.1", "", None)
    assert result == "unknown"


def test_xff_with_spaces_is_normalised() -> None:
    xff = "  203.0.113.1  ,  10.0.0.1  "
    result = effective_client_ip("10.0.0.1", xff, "", TRUSTED)
    assert result == "203.0.113.1"
