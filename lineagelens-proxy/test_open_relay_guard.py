"""Tests for M2: open-relay startup guard in connect_tunnel._assert_not_open_relay.

Rules:
- non-loopback host + no token  → SystemExit (fatal)
- non-loopback host + token set → OK
- loopback host + no token      → OK (safe local dev)
- loopback host + token set     → OK
"""
import pytest

import connect_tunnel


def _patch(host: str, token: str, monkeypatch):
    monkeypatch.setattr(connect_tunnel, "PROXY_HOST", host)
    monkeypatch.setattr(connect_tunnel, "PROXY_CONNECT_TOKEN", token)


def test_non_loopback_no_token_raises(monkeypatch):
    _patch("0.0.0.0", "", monkeypatch)
    with pytest.raises(SystemExit, match="PROXY_CONNECT_TOKEN"):
        connect_tunnel._assert_not_open_relay()


def test_non_loopback_explicit_ip_no_token_raises(monkeypatch):
    _patch("192.168.1.10", "", monkeypatch)
    with pytest.raises(SystemExit):
        connect_tunnel._assert_not_open_relay()


def test_non_loopback_with_token_ok(monkeypatch):
    _patch("0.0.0.0", "s" * 32, monkeypatch)
    connect_tunnel._assert_not_open_relay()  # must not raise


def test_loopback_127_no_token_ok(monkeypatch):
    _patch("127.0.0.1", "", monkeypatch)
    connect_tunnel._assert_not_open_relay()  # must not raise


def test_loopback_ipv6_no_token_ok(monkeypatch):
    _patch("::1", "", monkeypatch)
    connect_tunnel._assert_not_open_relay()  # must not raise


def test_loopback_with_token_ok(monkeypatch):
    _patch("127.0.0.1", "s" * 32, monkeypatch)
    connect_tunnel._assert_not_open_relay()  # must not raise
