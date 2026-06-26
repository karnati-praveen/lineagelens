"""Tests for immutable policy versioning (PART 2 #12).

Edits append immutable versions instead of overwriting; delete archives instead
of physically removing, so past decisions remain reproducible.
"""
from __future__ import annotations


def _create(client, admin, *, name="block gpt-3.5", action="block"):
    body = {
        "workspaceId": admin.workspace_id,
        "name": name,
        "policyType": "blocklist",
        "config": {"field": "model_name", "values": ["gpt-3.5-turbo"]},
        "action": action,
    }
    return client.post("/policies", json=body, headers=admin.auth_headers)


def test_create_policy_starts_at_version_1(client, make_user):
    admin = make_user(role="admin")
    resp = _create(client, admin)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["currentVersion"] == 1
    assert body["currentDigest"] is not None
    assert body["archived"] is False


def test_update_appends_immutable_version(client, make_user):
    admin = make_user(role="admin")
    pid = _create(client, admin).json()["id"]

    r = client.patch(f"/policies/{pid}", json={"action": "flag"}, headers=admin.auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["currentVersion"] == 2

    versions = client.get(f"/policies/{pid}/versions", headers=admin.auth_headers).json()
    assert versions["count"] == 2
    v1, v2 = versions["results"]
    assert v1["version"] == 1 and v1["action"] == "block"
    assert v2["version"] == 2 and v2["action"] == "flag"
    # v1 is frozen and superseded; its digest differs from v2's.
    assert v1["supersededAt"] is not None
    assert v1["digest"] != v2["digest"]
    assert v2["supersededAt"] is None


def test_digest_is_stable_for_same_content(client, make_user):
    admin = make_user(role="admin")
    pid = _create(client, admin).json()["id"]
    d1 = client.get(f"/policies/{pid}/versions", headers=admin.auth_headers).json()["results"][0]["digest"]

    from app.services.policy_version_service import compute_policy_digest
    recomputed = compute_policy_digest(
        name="block gpt-3.5",
        description=None,
        policy_type="blocklist",
        config={"field": "model_name", "values": ["gpt-3.5-turbo"]},
        action="block",
    )
    assert d1 == recomputed


def test_delete_archives_and_retains_versions(client, make_user):
    admin = make_user(role="admin")
    pid = _create(client, admin).json()["id"]
    client.patch(f"/policies/{pid}", json={"name": "renamed"}, headers=admin.auth_headers)

    resp = client.delete(f"/policies/{pid}", headers=admin.auth_headers)
    assert resp.status_code == 204

    # Policy row is retained (archived), and version history survives.
    listing = client.get("/policies", headers=admin.auth_headers).json()
    archived = [p for p in listing["results"] if p["id"] == pid]
    assert len(archived) == 1
    assert archived[0]["archived"] is True
    assert archived[0]["enabled"] is False

    versions = client.get(f"/policies/{pid}/versions", headers=admin.auth_headers).json()
    assert versions["count"] == 3  # create + update + archive
    assert versions["results"][-1]["changeType"] == "archive"


def test_evaluate_policies_stamps_version_digest():
    """Decisions carry the policy version + digest so they can be reproduced (PART 2 #12)."""
    from types import SimpleNamespace
    from app.core.policy import evaluate_policies

    policy = SimpleNamespace(
        id="p1", name="block gpt-3.5", policy_type="blocklist", action="block", enabled=True,
        config={"field": "model_name", "values": ["gpt-3.5-turbo"]},
        current_version=2, current_digest="deadbeef",
    )
    result = evaluate_policies([policy], {"model_name": "gpt-3.5-turbo"})
    assert result.passed is False
    assert result.violations[0].policy_version == 2
    assert result.violations[0].policy_digest == "deadbeef"
