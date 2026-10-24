"""Fail-closed contract for TSA notarization — the mock-notary regression guard.

`request_rfc3161_timestamp` previously returned a fabricated receipt when no TSA
was configured, persisting fake legal assurance. It must now always fail closed.
Pinned here so the mock cannot quietly come back.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.tsa_service import compute_merkle_root, request_rfc3161_timestamp


def test_no_configured_tsa_raises_instead_of_fabricating():
    with pytest.raises(RuntimeError, match="refusing to fabricate"):
        asyncio.run(request_rfc3161_timestamp("a" * 64))


def test_merkle_root_is_deterministic_and_order_sensitive():
    hashes = ["aa" * 32, "bb" * 32, "cc" * 32]
    assert compute_merkle_root(hashes) == compute_merkle_root(list(hashes))
    assert compute_merkle_root(list(reversed(hashes))) != compute_merkle_root(hashes)


def test_merkle_root_edge_cases():
    assert compute_merkle_root([]) is None
    assert compute_merkle_root(["x"]) == "x"
