"""Tests for AI Change Exposure Exchange (/exposure) - OpenVEX and OSV endpoints."""
from __future__ import annotations

import uuid as uuid_pkg
from datetime import UTC, datetime


def test_exposure_openvex_empty(client, make_user):
    user = make_user(role="member")
    resp = client.get("/exposure/openvex", headers=user.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert "@id" in data
    assert data["author"] == "LineageLens AI Change Exposure Exchange"
    assert "statements" in data
    assert isinstance(data["statements"], list)


def test_exposure_osv_empty(client, make_user):
    user = make_user(role="member")
    resp = client.get("/exposure/osv", headers=user.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_exposure_openvex_with_campaign(client, make_user):
    admin = make_user(role="admin")

    # Create a recall campaign
    create_resp = client.post(
        "/recall",
        json={"model": "claude-3-5-sonnet"},
        headers=admin.auth_headers,
    )
    assert create_resp.status_code == 201
    campaign_id = create_resp.json()["id"]

    # Fetch OpenVEX advisory
    resp = client.get("/exposure/openvex", headers=admin.auth_headers)
    assert resp.status_code == 200
    vex = resp.json()
    assert len(vex["statements"]) >= 1

    stmt = next(s for s in vex["statements"] if s["vulnerability"]["name"] == f"LL-RECALL-{campaign_id}")
    assert stmt["status"] == "affected"
    assert stmt["vulnerability"]["name"] == f"LL-RECALL-{campaign_id}"
    assert "Quarantined" in stmt["actionStatement"]


def test_exposure_openvex_filter_campaign_id(client, make_user):
    admin = make_user(role="admin")

    # Create campaign
    create_resp = client.post(
        "/recall",
        json={"model": "gpt-4o"},
        headers=admin.auth_headers,
    )
    assert create_resp.status_code == 201
    campaign_id = create_resp.json()["id"]

    # Filter by campaignId
    resp = client.get(f"/exposure/openvex?campaignId={campaign_id}", headers=admin.auth_headers)
    assert resp.status_code == 200
    vex = resp.json()
    assert len(vex["statements"]) == 1
    assert vex["statements"][0]["vulnerability"]["name"] == f"LL-RECALL-{campaign_id}"


def test_exposure_osv_with_campaign(client, make_user):
    admin = make_user(role="admin")

    # Create campaign
    create_resp = client.post(
        "/recall",
        json={"model": "gemini-1.5-pro"},
        headers=admin.auth_headers,
    )
    assert create_resp.status_code == 201
    campaign_id = create_resp.json()["id"]

    # Fetch OSV advisory
    resp = client.get("/exposure/osv", headers=admin.auth_headers)
    assert resp.status_code == 200
    osv_list = resp.json()
    assert len(osv_list) >= 1

    doc = next(d for d in osv_list if f"-{campaign_id}" in d["id"])
    assert doc["schemaVersion"] == "1.6.0"
    assert f"Recall Campaign #{campaign_id}" in doc["summary"]
    assert len(doc["references"]) >= 1
    assert doc["references"][0]["type"] == "ADVISORY"
