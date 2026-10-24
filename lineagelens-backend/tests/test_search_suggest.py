"""Tests for GET /search/suggest autocomplete over file paths and model names."""
from __future__ import annotations


def _ingest(client, user, *, file_path: str, model_name: str | None = None) -> str:
    payload: dict = {
        "workspaceId": user.workspace_id,
        "filePath": file_path,
        "insertedText": "x = 1\n",
    }
    if model_name:
        payload["provenance"] = {"modelName": model_name}
        payload["modelName"] = model_name
    resp = client.post("/ingest", json=payload, headers=user.auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["uuid"]


def _suggest(client, user, q: str, limit: int | None = None) -> dict:
    params: dict = {"q": q}
    if limit is not None:
        params["limit"] = limit
    resp = client.get("/search/suggest", params=params, headers=user.auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_suggest_matches_file_paths(client, make_user):
    user = make_user(role="admin")
    _ingest(client, user, file_path="/srv/app/main.py")
    _ingest(client, user, file_path="/srv/app/worker.py")
    _ingest(client, user, file_path="/other/notes.txt")

    body = _suggest(client, user, "app")
    paths = [s["value"] for s in body["file_paths"]]
    assert "/srv/app/main.py" in paths
    assert "/srv/app/worker.py" in paths
    assert "/other/notes.txt" not in paths


def test_suggest_matches_model_names(client, make_user):
    user = make_user(role="admin")
    _ingest(client, user, file_path="/a.py", model_name="gpt-4o-mini")
    _ingest(client, user, file_path="/b.py", model_name="claude-3-haiku")

    body = _suggest(client, user, "gpt")
    models = [s["value"] for s in body["models"]]
    # Ingest normalizes "gpt-4o-mini" -> "GPT-4o Mini"; match case-insensitively.
    assert any("gpt" in m.lower() for m in models)
    assert not any("claude" in m.lower() for m in models)


def test_suggest_escapes_like_wildcards(client, make_user):
    user = make_user(role="admin")
    _ingest(client, user, file_path="/srv/app/main.py")

    # A bare "%" must not act as a wildcard matching everything.
    body = _suggest(client, user, "%")
    assert body["file_paths"] == []
    assert body["models"] == []


def test_suggest_limit_is_capped(client, make_user):
    user = make_user(role="admin")
    for i in range(5):
        _ingest(client, user, file_path=f"/srv/batch_{i}.py")

    body = _suggest(client, user, "batch", limit=2)
    assert len(body["file_paths"]) == 2

    resp = client.get(
        "/search/suggest", params={"q": "batch", "limit": 999}, headers=user.auth_headers
    )
    assert resp.status_code == 422


def test_suggest_empty_query_rejected(client, make_user):
    user = make_user(role="admin")
    resp = client.get("/search/suggest", params={"q": ""}, headers=user.auth_headers)
    assert resp.status_code == 422
