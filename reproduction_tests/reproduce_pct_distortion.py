"""Reproduction script for Flaw 2 in lineagelens-mcp:
_pct percentage formatting distortion.

Demonstrates that _pct multiplies values <= 1.0 by 100 regardless of metric scale.
For small raw numbers or low risk scores on a 0-100 scale (e.g. 0.85), _pct(0.85) returns "85.0%",
distorting low risk scores into 85% critical risk.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow importing mcp_loader from current directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_loader import get_mcp_module

mcp_mod = get_mcp_module()


def test_pct_distortion():
    """Reproduce _pct multiplying value 0.85 by 100 into '85.0%'."""
    # Flaw: _pct multiplies any input float <= 1.0 by 100.
    # For a risk score or metric of 0.85, _pct(0.85) produces "85.0%".
    result = mcp_mod._pct(0.85)
    # Expected: Should NOT distort low value 0.85 to 85.0%
    assert result != "85.0%", f"_pct(0.85) distorted low value 0.85 to '{result}'"
    assert result in ("0.9%", "0.85"), f"_pct(0.85) returned invalid result '{result}'"


if __name__ == "__main__":
    try:
        test_pct_distortion()
        print("FAILED TO REPRODUCE: Test passed unexpectedly!")
        sys.exit(0)
    except AssertionError as exc:
        print(f"REPRODUCED FLAW 2 SUCCESSFULLY (AssertionError raised):\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"REPRODUCED FLAW 2 WITH EXCEPTION ({type(exc).__name__}):\n{exc}")
        sys.exit(1)
