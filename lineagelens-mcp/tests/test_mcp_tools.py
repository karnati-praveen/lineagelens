"""Tests for lineagelens-mcp tools.

Strategy: patch `_req` on the loaded module with an AsyncMock so every test
runs with zero live backend. The mcp stub (injected by conftest) means FastMCP
decorators are no-ops, leaving the tool functions as plain async callables.
"""
import pytest
from unittest.mock import AsyncMock, patch


# ── search_provenance ─────────────────────────────────────────────────────────

class TestSearchProvenance:
    @pytest.mark.asyncio
    async def test_base_query_sends_no_extra_fields(self, mcp_mod):
        captured = {}

        async def fake_req(method, path, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return {"results": [], "count": 0}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.search_provenance(query="auth middleware")

        body = captured["body"]
        assert body["query"] == "auth middleware"
        assert "modelFamily" not in body
        assert "category" not in body
        assert "reviewStatus" not in body
        assert "dateFrom" not in body
        assert "dateTo" not in body
        assert "No matching" in result

    @pytest.mark.asyncio
    async def test_filters_passed_only_when_set(self, mcp_mod):
        captured = {}

        async def fake_req(method, path, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return {"results": [], "count": 0}

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.search_provenance(
                query="",
                model_family="gpt",
                review_status="unreviewed",
                date_from="2026-03-14T00:00:00Z",
            )

        body = captured["body"]
        assert body.get("modelFamily") == "gpt"
        assert body.get("reviewStatus") == "unreviewed"
        assert body.get("dateFrom") == "2026-03-14T00:00:00Z"
        assert "dateTo" not in body
        assert "category" not in body

    @pytest.mark.asyncio
    async def test_all_filters_passed_when_set(self, mcp_mod):
        captured = {}

        async def fake_req(method, path, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return {"results": [], "count": 0}

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.search_provenance(
                query="sql injection",
                model_family="claude",
                category="sql",
                review_status="pending",
                date_from="2026-01-01T00:00:00Z",
                date_to="2026-06-01T00:00:00Z",
            )

        body = captured["body"]
        assert body["modelFamily"] == "claude"
        assert body["category"] == "sql"
        assert body["reviewStatus"] == "pending"
        assert body["dateFrom"] == "2026-01-01T00:00:00Z"
        assert body["dateTo"] == "2026-06-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_results_formatted(self, mcp_mod):
        fake_results = [
            {
                "uuid": "aaaa0000-0000-0000-0000-000000000001",
                "filePath": "src/auth/tokens.ts",
                "model": "gpt-4o",
                "timestampIso": "2026-05-01T10:00:00Z",
                "snippet": "const token = jwt.sign(payload, secret);",
            }
        ]

        async def fake_req(method, path, **kwargs):
            return {"results": fake_results, "count": 1}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.search_provenance(query="JWT token")

        assert "src/auth/tokens.ts" in result
        assert "gpt-4o" in result
        assert "2026-05-01T10:00:00" in result

    @pytest.mark.asyncio
    async def test_empty_model_family_not_forwarded(self, mcp_mod):
        captured = {}

        async def fake_req(method, path, **kwargs):
            captured["body"] = kwargs.get("json", {})
            return {"results": [], "count": 0}

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.search_provenance(query="test", model_family="")

        assert "modelFamily" not in captured["body"]


# ── list_incidents ────────────────────────────────────────────────────────────

class TestListIncidents:
    @pytest.mark.asyncio
    async def test_returns_no_incidents_message(self, mcp_mod):
        async def fake_req(method, path, **kwargs):
            return {"items": []}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.list_incidents()

        assert "No production incidents" in result

    @pytest.mark.asyncio
    async def test_formats_incident_list(self, mcp_mod):
        incidents = [
            {
                "uuid": "bbbb0000-0000-0000-0000-000000000001",
                "title": "Auth service outage",
                "startedAt": "2026-06-01T14:00:00Z",
                "resolvedAt": "2026-06-01T15:30:00Z",
                "affectedFiles": ["src/auth/middleware.py", "src/auth/tokens.py"],
                "externalRef": "INC-1234",
            }
        ]

        async def fake_req(method, path, **kwargs):
            return {"items": incidents}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.list_incidents(limit=5)

        assert "Auth service outage" in result
        assert "INC-1234" in result
        assert "2026-06-01T14:00:00" in result
        assert "resolved" in result
        assert "2 affected" in result

    @pytest.mark.asyncio
    async def test_limit_capped_at_100(self, mcp_mod):
        captured = {}

        async def fake_req(method, path, **kwargs):
            captured["path"] = path
            return {"items": []}

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.list_incidents(limit=9999)

        assert "limit=100" in captured["path"]

    @pytest.mark.asyncio
    async def test_feature_gate_403(self, mcp_mod):
        async def fake_req(method, path, **kwargs):
            raise RuntimeError("Error: This feature is not available in Lite tier. Upgrade to Plus.")

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.list_incidents()

        assert "Error:" in result
        assert "Lite" in result or "not available" in result


# ── get_incident_provenance ───────────────────────────────────────────────────

class TestGetIncidentProvenance:
    @pytest.mark.asyncio
    async def test_empty_uuid_auto_fetches_latest(self, mcp_mod):
        call_paths = []

        async def fake_req(method, path, **kwargs):
            call_paths.append(path)
            if "/incidents?limit=1" in path:
                return {
                    "items": [{"uuid": "cccc0000-0000-0000-0000-000000000001", "title": "Latest"}]
                }
            if "/provenance" in path:
                return {
                    "incident": {
                        "title": "Latest",
                        "startedAt": "2026-06-10T08:00:00Z",
                        "resolvedAt": None,
                        "affectedFiles": ["src/db/migrate.py"],
                    },
                    "items": [],
                    "total": 0,
                }
            return {}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.get_incident_provenance(incident_uuid="")

        assert any("/incidents?limit=1" in p for p in call_paths)
        assert any("/provenance" in p for p in call_paths)
        assert "Latest" in result

    @pytest.mark.asyncio
    async def test_explicit_uuid_skips_list_call(self, mcp_mod):
        call_paths = []
        target_uuid = "dddd0000-0000-0000-0000-000000000002"

        async def fake_req(method, path, **kwargs):
            call_paths.append(path)
            if "/provenance" in path:
                return {
                    "incident": {
                        "title": "Auth bug",
                        "startedAt": "2026-06-05T09:00:00Z",
                        "resolvedAt": "2026-06-05T10:00:00Z",
                        "affectedFiles": ["src/auth.py"],
                    },
                    "items": [],
                    "total": 0,
                }
            return {}

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.get_incident_provenance(incident_uuid=target_uuid)

        assert not any("/incidents?limit=1" in p for p in call_paths)
        assert any(target_uuid in p for p in call_paths)

    @pytest.mark.asyncio
    async def test_provenance_items_show_risk_and_model(self, mcp_mod):
        target_uuid = "eeee0000-0000-0000-0000-000000000003"

        async def fake_req(method, path, **kwargs):
            return {
                "incident": {
                    "title": "DB crash",
                    "startedAt": "2026-06-08T12:00:00Z",
                    "resolvedAt": None,
                    "affectedFiles": ["src/db/session.py"],
                },
                "items": [
                    {
                        "filePath": "src/db/session.py",
                        "timestampIso": "2026-06-07T11:00:00Z",
                        "modelName": "claude-sonnet-4-6",
                        "riskScore": 82,
                        "tags": ["sql", "auth"],
                        "insertedCodePreview": "engine = create_engine(url)",
                    }
                ],
                "total": 1,
            }

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.get_incident_provenance(incident_uuid=target_uuid)

        assert "claude-sonnet-4-6" in result
        assert "82" in result
        assert "sql" in result
        assert "create_engine" in result

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_error(self, mcp_mod):
        with patch.object(mcp_mod, "_req", new=AsyncMock()):
            result = await mcp_mod.get_incident_provenance(incident_uuid="not-a-uuid")

        assert "Error" in result
        assert "UUID" in result or "uuid" in result

    @pytest.mark.asyncio
    async def test_no_incidents_returns_friendly_message(self, mcp_mod):
        async def fake_req(method, path, **kwargs):
            return {"items": []}

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.get_incident_provenance(incident_uuid="")

        assert "No production incidents" in result

    @pytest.mark.asyncio
    async def test_window_days_forwarded(self, mcp_mod):
        captured_path = []
        target_uuid = "ffff0000-0000-0000-0000-000000000004"

        async def fake_req(method, path, **kwargs):
            captured_path.append(path)
            return {
                "incident": {
                    "title": "Crash",
                    "startedAt": "2026-06-10T08:00:00Z",
                    "resolvedAt": None,
                    "affectedFiles": [],
                },
                "items": [],
                "total": 0,
            }

        with patch.object(mcp_mod, "_req", new=fake_req):
            await mcp_mod.get_incident_provenance(incident_uuid=target_uuid, window_days=30)

        assert any("windowDays=30" in p for p in captured_path)

    @pytest.mark.asyncio
    async def test_404_returns_friendly_message(self, mcp_mod):
        target_uuid = "a1b2c3d4-0000-0000-0000-000000000005"

        async def fake_req(method, path, **kwargs):
            raise RuntimeError("Backend returned 404: not found")

        with patch.object(mcp_mod, "_req", new=fake_req):
            result = await mcp_mod.get_incident_provenance(incident_uuid=target_uuid)

        assert "not found" in result.lower() or "404" in result
