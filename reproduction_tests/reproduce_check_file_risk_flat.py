"""Reproduction script for Flaw 1 in lineagelens-mcp:
check_file_risk flat search result risk score extraction bug.

Demonstrates that flat search results containing top-level `riskScore` or `risk_score`
are ignored because check_file_risk only inspects nested `r["record"]["riskScore"]`.
Missing `record` key causes `risk_score` to default to 0, misclassifying high risk files as LOW.
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


async def test_check_file_risk_flat_extraction():
    """Reproduce check_file_risk misclassifying flat search result with riskScore 95 as LOW."""
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

    # A record with riskScore 95 must be classified as CRITICAL (or HIGH).
    # Because check_file_risk looks inside r["record"]["riskScore"], it defaults
    # risk_score to 0 and output shows "LOW 1" and no CRITICAL count.
    assert "CRITICAL" in result, f"Expected CRITICAL risk level for score 95, but output was:\n{result}"


if __name__ == "__main__":
    try:
        asyncio.run(test_check_file_risk_flat_extraction())
        print("FAILED TO REPRODUCE: Test passed unexpectedly!")
        sys.exit(0)
    except AssertionError as exc:
        print(f"REPRODUCED FLAW 1 SUCCESSFULLY (AssertionError raised):\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"REPRODUCED FLAW 1 WITH EXCEPTION ({type(exc).__name__}):\n{exc}")
        sys.exit(1)
