"""Reproduction script for Flaw 4 in lineagelens-mcp:
list_workspaces null handling bug / iteration failure when backend returns {"workspaces": None}.

Demonstrates that when backend returns {"workspaces": None} or workspace dictionary where
"workspaces" key is None, data.get("workspaces", [data]) evaluates to None (instead of [data]),
causing workspace data to be lost and returning "No workspaces found..." or iteration failure.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow importing mcp_loader from current directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_loader import get_mcp_module

mcp_mod = get_mcp_module()


async def test_list_workspaces_null():
    """Reproduce list_workspaces returning 'No workspaces found' when backend returns workspaces: None."""
    fake_response = {
        "workspaceId": "ws-prod-456",
        "name": "Production Environment",
        "workspaces": None,
    }

    async def fake_req(method, path, **kwargs):
        return fake_response

    with patch.object(mcp_mod, "_req", new=fake_req):
        result = await mcp_mod.list_workspaces()

    # Expected: The workspace 'Production Environment' should be listed.
    # Flaw: data.get("workspaces", [data]) evaluates to None because "workspaces" key is present with value None.
    # Then `if not workspaces:` evaluates to True and returns "No workspaces found for the current user."
    assert "Production Environment" in result or "ws-prod-456" in result, (
        f"Expected workspace 'Production Environment' (ws-prod-456) in output, but got:\n{result!r}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(test_list_workspaces_null())
        print("FAILED TO REPRODUCE: Test passed unexpectedly!")
        sys.exit(0)
    except AssertionError as exc:
        print(f"REPRODUCED FLAW 4 SUCCESSFULLY (AssertionError raised):\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"REPRODUCED FLAW 4 WITH EXCEPTION ({type(exc).__name__}):\n{exc}")
        sys.exit(1)
