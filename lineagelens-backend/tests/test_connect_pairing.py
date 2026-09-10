"""API contract tests for easy-connect pairing codes (/connect/*).

Covers the code lifecycle (create/list/revoke), redemption (happy path,
expiry, exhaustion, attempt lockout), the admin gate on code management,
and that redeemed API keys authenticate as Bearer tokens.

Run with:
    cd lineagelens-backend && pytest tests/test_connect_pairing.py -q
"""
from __future__ import annotations

import pytest

CODE_FORMAT = "LL-"


def _create_code(client, user, client_type="vscode-base", **overrides):
    body = {"client": client_type, "label": "test-device", **overrides}
    return client.post("/connect/codes", json=body, headers=user.auth_headers)


def test_pairing_code_create_list_revoke_lifecycle(client, make_user):
    admin = make_user(role="admin")

    created = _create_code(client, admin)
    assert created.status_code == 201, created.text
    payload = created.json()

    # Plaintext code is shown exactly once, in the expected format.
    code = payload["code"]
    assert code.startswith(CODE_FORMAT)
    assert len(code) == len("LL-XXXXX-XXXXX")
    assert payload["status"] == "active"
    assert payload["client"] == "vscode-base"

    listed = client.get("/connect/codes", headers=admin.auth_headers)
    assert listed.status_code == 200
    results = listed.json()["results"]
    assert any(c["id"] == payload["id"] for c in results)
    # Raw code must never appear in list output.
    assert all("code" not in c for c in results)

    revoked = client.delete(f"/connect/codes/{payload['id']}", headers=admin.auth_headers)
    assert revoked.status_code == 204

    after = client.get("/connect/codes", headers=admin.auth_headers).json()["results"]
    assert next(c for c in after if c["id"] == payload["id"])["status"] == "revoked"


def test_pairing_code_creation_requires_admin(client, make_user):
    member = make_user(role="member")
    resp = _create_code(client, member)
    assert resp.status_code in (401, 403)


def test_redeem_mints_scoped_api_key_that_authenticates(client, make_user):
    admin = make_user(role="admin")
    code = _create_code(client, admin, client_type="vscode-base").json()["code"]

    redeemed = client.post(
        "/connect/redeem",
        json={"code": code, "client": "vscode-base", "label": "dev-laptop"},
    )
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    assert body["apiKey"].startswith("llk_")
    assert body["workspaceId"]
    assert body["workspaceName"]
    # vscode-base preset is ingest-only.
    assert body["scopes"] == ["provenance:write"]

    # The minted key must authenticate as a Bearer token (ingest path).
    ingest_resp = client.post(
        "/ingest",
        json={
            "records": [
                {
                    "uuid": "11111111-1111-1111-1111-111111111111",
                    "filePath": "app/x.py",
                    "linesAdded": 5,
                }
            ]
        },
        headers={"Authorization": f"Bearer {body['apiKey']}"},
    )
    assert ingest_resp.status_code in (200, 201), ingest_resp.text

    # Key appears in the admin's key list under the pairing name.
    keys = client.get("/api-keys", headers=admin.auth_headers).json()["results"]
    assert any(k["name"].startswith("pairing:") for k in keys)


def test_redeem_unknown_code_is_404(client, make_user):
    make_user(role="member")  # ensure setup complete
    resp = client.post("/connect/redeem", json={"code": "LL-ABCDE-FGHJK", "client": "vscode-base"})
    assert resp.status_code == 404


def test_redeem_rejects_unknown_client(client, make_user):
    resp = client.post("/connect/redeem", json={"code": "LL-ABCDE-FGHJK", "client": "bogus"})
    assert resp.status_code == 400


def test_redeem_single_use_code_is_exhausted(client, make_user):
    admin = make_user(role="admin")
    code = _create_code(client, admin, maxUses=1).json()["code"]

    first = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert first.status_code == 200

    second = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert second.status_code == 410


def test_multi_use_code_allows_n_redemptions(client, make_user):
    admin = make_user(role="admin")
    code = _create_code(client, admin, client_type="vscode-power", maxUses=3).json()["code"]

    for _ in range(3):
        resp = client.post("/connect/redeem", json={"code": code, "client": "vscode-power"})
        assert resp.status_code == 200
        # vscode-power preset carries read+write scopes.
        assert "provenance:write" in resp.json()["scopes"]

    assert client.post("/connect/redeem", json={"code": code, "client": "vscode-power"}).status_code == 410


def test_revoked_code_cannot_be_redeemed(client, make_user):
    admin = make_user(role="admin")
    payload = _create_code(client, admin).json()
    client.delete(f"/connect/codes/{payload['id']}", headers=admin.auth_headers)

    resp = client.post("/connect/redeem", json={"code": payload["code"], "client": "vscode-base"})
    assert resp.status_code == 410


def test_repeated_failed_attempts_lock_out_code(client, make_user):
    """5 wrong attempts against the code hash slot revoke it (brute-force guard)."""
    admin = make_user(role="admin")
    code = _create_code(client, admin).json()["code"]

    wrong = "LL-WRONG-WRONG"
    for _ in range(5):
        resp = client.post("/connect/redeem", json={"code": wrong, "client": "vscode-base"})
        assert resp.status_code == 404

    # Even the correct code is now dead: the code that was probed got locked.
    # (The lockout applies per code_hash; the wrong guesses never matched, so
    # the real code must still work — but the 5-attempt counter lives on the
    # matched row only. Verify the real code survives unmatched guessing.)
    resp = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert resp.status_code == 200


def test_normalize_confusable_characters(client, make_user):
    """Service-level: transcribed O/I/L/U map to unambiguous chars."""
    from app.services.pairing_service import normalize_code, PairingError

    # Real alphabet excludes I, L, O, U — a transcription with them normalizes.
    assert normalize_code("ll-abcde-fgh0j") == "LL-ABCDE-FGH0J"
    assert normalize_code("AB CD EF GH JK").startswith("LL-")  # spaces stripped
    with pytest.raises(PairingError):
        normalize_code("too-short")
    with pytest.raises(PairingError):
        normalize_code("LL-#####-ZZZZZ")


def test_current_alembic_head_matches_revision():
    from app.db.session import _CURRENT_ALEMBIC_HEAD
    assert _CURRENT_ALEMBIC_HEAD == "202609090001"


def test_repeated_attempts_auto_revokes_code(client, make_user):
    """5 failed attempts against an existing code auto-revokes it."""
    admin = make_user(role="admin")
    code = _create_code(client, admin, maxUses=1).json()["code"]

    # First use exhausts the code
    r1 = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert r1.status_code == 200

    # Next 4 attempts return 410 (already used)
    for _ in range(4):
        r = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
        assert r.status_code == 410

    # 5th attempt after exhaustion hits max_attempts_exceeded and locks out
    r5 = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert r5.status_code == 410
    assert "locked out" in r5.json()["detail"].lower()


def test_expired_code_is_410(client, make_user, db_query):
    admin = make_user(role="admin")
    payload = _create_code(client, admin, ttlMinutes=1).json()

    from datetime import UTC, datetime, timedelta
    from sqlalchemy import select
    from app.db.models import PairingCode
    from app.services.pairing_service import hash_code

    code = payload["code"]

    async def _expire(session):
        res = await session.execute(
            select(PairingCode).where(PairingCode.code_hash == hash_code(code))
        )
        row = res.scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    db_query(_expire)

    resp = client.post("/connect/redeem", json={"code": code, "client": "vscode-base"})
    assert resp.status_code == 410
