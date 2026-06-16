"""Tests for POST /leads and DELETE /leads.

Run with:
    cd lineagelens-backend && pytest tests/test_leads.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import Lead

def _delete(client, email: str):
    return client.delete("/leads", params={"email": email})


# ── POST /leads ───────────────────────────────────────────────────────────────

def test_post_lead_valid_email_returns_saved(client):
    resp = client.post("/leads", json={"email": "user@example.com", "source": "vscode-extension"})
    assert resp.status_code == 200
    assert resp.json() == {"saved": True}


def test_post_lead_normalizes_email_to_lowercase(client, db_query):
    client.post("/leads", json={"email": "Test.User@Example.COM", "source": "vscode-extension"})

    async def _get(session):
        return await session.scalar(select(Lead).where(Lead.email == "test.user@example.com"))

    lead = db_query(_get)
    assert lead is not None
    assert lead.email == "test.user@example.com"
    assert lead.source == "vscode-extension"


def test_post_lead_stores_extension_version(client, db_query):
    client.post(
        "/leads",
        json={"email": "ver@example.com", "source": "vscode-extension", "extension_version": "1.2.3"},
    )

    async def _get(session):
        return await session.scalar(select(Lead).where(Lead.email == "ver@example.com"))

    lead = db_query(_get)
    assert lead is not None
    assert lead.extension_version == "1.2.3"


def test_post_lead_duplicate_is_idempotent_no_dup_row(client, db_query):
    client.post("/leads", json={"email": "dup@example.com", "source": "vscode-extension"})
    resp = client.post("/leads", json={"email": "DUP@example.com", "source": "retry"})

    assert resp.status_code == 200
    assert resp.json() == {"saved": True}

    async def _count(session):
        return await session.scalar(
            select(func.count()).select_from(Lead).where(Lead.email == "dup@example.com")
        )

    assert db_query(_count) == 1


def test_post_lead_malformed_email_returns_400(client):
    resp = client.post("/leads", json={"email": "not-an-email"})
    assert resp.status_code == 400


def test_post_lead_empty_email_returns_400(client):
    resp = client.post("/leads", json={"email": "  "})
    assert resp.status_code == 400


def test_post_lead_email_too_long_returns_400(client):
    long_email = "a" * 250 + "@b.com"
    resp = client.post("/leads", json={"email": long_email})
    assert resp.status_code == 400


def test_post_lead_requires_no_auth(client):
    # Must succeed with no Authorization header (unauthenticated endpoint)
    resp = client.post("/leads", json={"email": "noauth@example.com"})
    assert resp.status_code == 200


def test_post_lead_missing_email_field_returns_422(client):
    resp = client.post("/leads", json={"source": "vscode-extension"})
    assert resp.status_code == 422


# ── DELETE /leads ─────────────────────────────────────────────────────────────

def test_delete_lead_removes_existing_row(client, db_query):
    client.post("/leads", json={"email": "todelete@example.com"})

    resp = _delete(client, "todelete@example.com")
    assert resp.status_code == 200
    assert resp.json() == {"removed": True}

    async def _get(session):
        return await session.scalar(
            select(Lead).where(Lead.email == "todelete@example.com")
        )

    assert db_query(_get) is None


def test_delete_lead_nonexistent_is_ok(client):
    resp = _delete(client, "ghost@example.com")
    assert resp.status_code == 200
    assert resp.json() == {"removed": True}


def test_delete_lead_normalizes_email(client, db_query):
    client.post("/leads", json={"email": "case@example.com"})
    resp = _delete(client, "CASE@EXAMPLE.COM")
    assert resp.status_code == 200

    async def _get(session):
        return await session.scalar(select(Lead).where(Lead.email == "case@example.com"))

    assert db_query(_get) is None
