"""Tests for external witness / split-trust anchoring (PART 5 #53)."""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from app.services.witness_service import (
    STATUS_FAILED,
    STATUS_NOT_CONFIGURED,
    STATUS_WITNESSED,
    CustomerObjectStoreBackend,
    GitTagWitnessBackend,
    RFC3161TSABackend,
    SigstoreRekorBackend,
    build_timestamp_request,
    compute_periodic_root,
    witness_root,
)


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        tsa_url=None, witness_git_repo_path=None, rekor_url=None, witness_object_store_url=None
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── compute_periodic_root ────────────────────────────────────────────────────

def test_periodic_root_empty_list_is_deterministic():
    assert compute_periodic_root([]) == compute_periodic_root([])


def test_periodic_root_changes_when_hashes_change():
    a = compute_periodic_root(["aa" * 32, "bb" * 32])
    b = compute_periodic_root(["aa" * 32, "cc" * 32])
    assert a != b


def test_periodic_root_single_hash():
    h = "ab" * 32
    root = compute_periodic_root([h])
    assert len(root) == 64


def test_build_timestamp_request_is_valid_der_sequence():
    req = build_timestamp_request("ab" * 32)
    assert req[0] == 0x30  # SEQUENCE tag


# ── is_configured gating ─────────────────────────────────────────────────────

def test_all_backends_report_not_configured_when_unset():
    settings = _settings()
    for backend in (RFC3161TSABackend(), GitTagWitnessBackend(), SigstoreRekorBackend(), CustomerObjectStoreBackend()):
        assert backend.is_configured(settings) is False


def test_backends_report_configured_when_set():
    settings = _settings(
        tsa_url="http://tsa.example",
        witness_git_repo_path="/repo",
        rekor_url="http://rekor.example",
        witness_object_store_url="http://store.example/put",
    )
    for backend in (RFC3161TSABackend(), GitTagWitnessBackend(), SigstoreRekorBackend(), CustomerObjectStoreBackend()):
        assert backend.is_configured(settings) is True


# ── Not-configured backends never attempt network I/O ───────────────────────

def test_unconfigured_backends_never_attempt_io():
    settings = _settings()

    async def _run():
        results = []
        for backend in (RFC3161TSABackend(), GitTagWitnessBackend(), SigstoreRekorBackend(), CustomerObjectStoreBackend()):
            results.append(await backend.publish("ab" * 32, settings=settings))
        return results

    receipts = asyncio.run(_run())
    for r in receipts:
        assert r.status == STATUS_NOT_CONFIGURED


# ── TSA backend (mocked HTTP) ─────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_tsa_backend_success(monkeypatch):
    import app.services.witness_service as ws

    monkeypatch.setattr(ws.url_request, "urlopen", lambda req, timeout: _FakeResponse(b"fake-token-bytes"))
    settings = _settings(tsa_url="http://tsa.example")

    receipt = asyncio.run(RFC3161TSABackend().publish("ab" * 32, settings=settings))
    assert receipt.status == STATUS_WITNESSED
    assert receipt.external_ref is not None


def test_tsa_backend_network_failure(monkeypatch):
    import app.services.witness_service as ws
    from urllib import error as url_error

    def _raise(req, timeout):
        raise url_error.URLError("connection refused")

    monkeypatch.setattr(ws.url_request, "urlopen", _raise)
    settings = _settings(tsa_url="http://tsa.example")

    receipt = asyncio.run(RFC3161TSABackend().publish("ab" * 32, settings=settings))
    assert receipt.status == STATUS_FAILED
    assert "connection refused" in receipt.details


# ── Git tag backend (mocked subprocess) ──────────────────────────────────────

def test_git_tag_backend_success(monkeypatch):
    import app.services.witness_service as ws

    monkeypatch.setattr(
        ws.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(args=a, returncode=0)
    )
    settings = _settings(witness_git_repo_path="/repo")

    receipt = asyncio.run(GitTagWitnessBackend().publish("ab" * 32, settings=settings))
    assert receipt.status == STATUS_WITNESSED
    assert receipt.external_ref.startswith("lineagelens-witness-")


def test_git_tag_backend_missing_git_binary(monkeypatch):
    import app.services.witness_service as ws

    def _raise(*a, **kw):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(ws.subprocess, "run", _raise)
    settings = _settings(witness_git_repo_path="/repo")

    receipt = asyncio.run(GitTagWitnessBackend().publish("ab" * 32, settings=settings))
    assert receipt.status == STATUS_FAILED


# ── witness_root orchestration ───────────────────────────────────────────────

def test_witness_root_returns_all_four_backends_even_when_unconfigured():
    settings = _settings()
    receipts = asyncio.run(witness_root("ab" * 32, settings=settings))
    assert len(receipts) == 4
    assert {r.backend for r in receipts} == {
        "rfc3161_tsa", "git_tag", "sigstore_rekor", "customer_object_store"
    }
    assert all(r.status == STATUS_NOT_CONFIGURED for r in receipts)
