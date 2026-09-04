"""Automated Reproduction Test Suite for lineagelens-mcp Flaws.

Reproduces all 5 critical lineagelens-mcp flaws:
1. check_file_risk flat search result risk score extraction bug (misclassifies high risk files as LOW).
2. _pct percentage formatting distortion (multiplies values <= 1.0 by 100).
3. API key auth header rejection (backend rejecting X-API-Key with HTTP 401 raises RuntimeError).
4. list_workspaces null handling bug (loses workspace data when "workspaces": None).
5. Unsafe string risk score formatting crash ({score:.3f} raising TypeError/ValueError on string inputs).

Execution:
  python reproduction_tests/reproduce_mcp_flaws.py
  pytest reproduction_tests/reproduce_mcp_flaws.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow importing mcp_loader from current directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_loader import get_mcp_module

mcp_mod = get_mcp_module()


async def test_flaw1_check_file_risk_flat():
    """Flaw 1: check_file_risk ignores top-level riskScore in flat search results."""
    fake_results = [
        {
            "uuid": "record-101",
            "filePath": "src/critical_auth.py",
            "riskScore": 95,
            "model": "claude-3-5-sonnet",
            "timestampIso": "2026-08-01T12:00:00Z",
        }
    ]

    async def fake_req(method, path, **kwargs):
        return {"results": fake_results}

    with patch.object(mcp_mod, "_req", new=fake_req):
        result = await mcp_mod.check_file_risk("src/critical_auth.py")

    assert "CRITICAL" in result, f"Expected CRITICAL risk level for score 95, but output was:\n{result}"


def test_flaw2_pct_distortion():
    """Flaw 2: _pct percentage formatter multiplies values <= 1.0 by 100."""
    result = mcp_mod._pct(0.85)
    assert result != "85.0%", f"_pct(0.85) distorted low value 0.85 to '{result}'"
    assert result in ("0.9%", "0.85"), f"_pct(0.85) returned invalid result '{result}'"


async def test_flaw3_api_key_rejection():
    """Flaw 3: API key auth header causes backend 401 and raises RuntimeError in _req."""
    mcp_mod._API_KEY = "ll_test_api_key_999"

    async def fake_request(method, url, headers=None, **kwargs):
        if "X-API-Key" in (headers or {}):
            return httpx.Response(401, json={"detail": "API key auth not accepted on query endpoint"})
        return httpx.Response(200, json={"results": [], "count": 0})

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.request.side_effect = fake_request

    try:
        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await mcp_mod._req("POST", "/search", json={"query": "test"})
            assert "API key was rejected" not in str(res)
    finally:
        mcp_mod._API_KEY = ""


async def test_flaw4_list_workspaces_null():
    """Flaw 4: list_workspaces returns 'No workspaces found' when backend returns workspaces: None."""
    fake_response = {
        "workspaceId": "ws-prod-456",
        "name": "Production Environment",
        "workspaces": None,
    }

    async def fake_req(method, path, **kwargs):
        return fake_response

    with patch.object(mcp_mod, "_req", new=fake_req):
        result = await mcp_mod.list_workspaces()

    assert "Production Environment" in result or "ws-prod-456" in result, (
        f"Expected workspace 'Production Environment' in output, but got:\n{result!r}"
    )


def test_flaw5_unsafe_score_format():
    """Flaw 5: Unsafe string risk score formatting ({score:.3f}) raises TypeError/ValueError on string inputs."""
    record = {
        "uuid": "record-777",
        "filePath": "src/api/auth.py",
        "model": "gpt-4o",
        "score": "0.95",
    }

    res = mcp_mod._format_search_result(1, record)
    assert any("Score:" in line for line in res), "Expected formatted output containing 'Score:'"


def run_all_reproductions():
    """Run all reproduction tests standalone and report results."""
    print("==================================================")
    print(" Running lineagelens-mcp Flaw Reproduction Suite ")
    print("==================================================")

    results = {}

    # Test 1
    try:
        asyncio.run(test_flaw1_check_file_risk_flat())
        results["Flaw 1 (check_file_risk flat result score)"] = "PASSED (UNEXPECTED)"
    except Exception as exc:
        results["Flaw 1 (check_file_risk flat result score)"] = f"REPRODUCED ({type(exc).__name__}: {exc})"

    # Test 2
    try:
        test_flaw2_pct_distortion()
        results["Flaw 2 (_pct formatting distortion)"] = "PASSED (UNEXPECTED)"
    except Exception as exc:
        results["Flaw 2 (_pct formatting distortion)"] = f"REPRODUCED ({type(exc).__name__}: {exc})"

    # Test 3
    try:
        asyncio.run(test_flaw3_api_key_rejection())
        results["Flaw 3 (API key auth header rejection)"] = "PASSED (UNEXPECTED)"
    except Exception as exc:
        results["Flaw 3 (API key auth header rejection)"] = f"REPRODUCED ({type(exc).__name__}: {exc})"

    # Test 4
    try:
        asyncio.run(test_flaw4_list_workspaces_null())
        results["Flaw 4 (list_workspaces null response)"] = "PASSED (UNEXPECTED)"
    except Exception as exc:
        results["Flaw 4 (list_workspaces null response)"] = f"REPRODUCED ({type(exc).__name__}: {exc})"

    # Test 5
    try:
        test_flaw5_unsafe_score_format()
        results["Flaw 5 (unsafe score format TypeError/ValueError)"] = "PASSED (UNEXPECTED)"
    except Exception as exc:
        results["Flaw 5 (unsafe score format TypeError/ValueError)"] = f"REPRODUCED ({type(exc).__name__}: {exc})"

    print("\nReproduction Test Results:")
    print("--------------------------------------------------")
    all_reproduced = True
    for flaw_name, outcome in results.items():
        print(f" - {flaw_name}: {outcome}")
        if "REPRODUCED" not in outcome:
            all_reproduced = False

    print("--------------------------------------------------")
    if all_reproduced:
        print("ALL 5 FLAWS SUCCESSFULLY REPRODUCE AND FAIL AS EXPECTED.")
        sys.exit(1)
    else:
        print("SOME FLAWS DID NOT REPRODUCE.")
        sys.exit(0)


if __name__ == "__main__":
    run_all_reproductions()
