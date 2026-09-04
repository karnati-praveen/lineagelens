"""Reproduction script for Flaw 3 in lineagelens-mcp:
API key auth header rejection breaking tool invocations.

Demonstrates that setting LINEAGELENS_API_KEY (documented as recommended auth method for Plus/Max)
causes _req to attach X-API-Key header. When backend query endpoints reject X-API-Key with HTTP 401,
_req raises RuntimeError, breaking all tool invocations.
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


async def test_api_key_rejection():
    """Reproduce API key authentication failure raising RuntimeError on 401."""
    # Configure API key as instructed in lineagelens-mcp module docstring
    mcp_mod._API_KEY = "ll_test_api_key_999"

    # Mock backend response returning HTTP 401 for X-API-Key header
    async def fake_request(method, url, headers=None, **kwargs):
        if "X-API-Key" in (headers or {}):
            return httpx.Response(401, json={"detail": "API key auth not accepted on query endpoint"})
        return httpx.Response(200, json={"results": [], "count": 0})

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.request.side_effect = fake_request

    try:
        with patch("httpx.AsyncClient", return_value=mock_client):
            # Invoking search_provenance tool or _req with API key set raises RuntimeError on 401
            res = await mcp_mod._req("POST", "/search", json={"query": "test"})
            assert "API key was rejected" not in str(res)
    finally:
        mcp_mod._API_KEY = ""


if __name__ == "__main__":
    try:
        asyncio.run(test_api_key_rejection())
        print("FAILED TO REPRODUCE: Test passed unexpectedly!")
        sys.exit(0)
    except RuntimeError as exc:
        print(f"REPRODUCED FLAW 3 SUCCESSFULLY (RuntimeError raised):\n{exc}")
        sys.exit(1)
    except AssertionError as exc:
        print(f"REPRODUCED FLAW 3 SUCCESSFULLY (AssertionError raised):\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"REPRODUCED FLAW 3 WITH EXCEPTION ({type(exc).__name__}):\n{exc}")
        sys.exit(1)
