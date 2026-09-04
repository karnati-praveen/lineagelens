"""Reproduction script for Flaw 5 in lineagelens-mcp:
Unsafe string risk score formatting crash ({score:.3f} raising TypeError/ValueError on string inputs).

Demonstrates that when search result record contains a string score (e.g. "0.95"),
_format_search_result checks `score is not None` and attempts `{score:.3f}`,
raising TypeError/ValueError: unsupported format string passed to float.__format__.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow importing mcp_loader from current directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_loader import get_mcp_module

mcp_mod = get_mcp_module()


def test_unsafe_score_format():
    """Reproduce TypeError/ValueError crash when score in search result is a string."""
    record = {
        "uuid": "record-777",
        "filePath": "src/api/auth.py",
        "model": "gpt-4o",
        "score": "0.95",
    }

    # Calling _format_search_result with a string score raises TypeError or ValueError
    res = mcp_mod._format_search_result(1, record)
    assert any("Score:" in line for line in res), "Expected formatted output containing 'Score:'"


if __name__ == "__main__":
    try:
        test_unsafe_score_format()
        print("FAILED TO REPRODUCE: Test passed unexpectedly!")
        sys.exit(0)
    except (TypeError, ValueError) as exc:
        print(f"REPRODUCED FLAW 5 SUCCESSFULLY ({type(exc).__name__} raised):\n{exc}")
        sys.exit(1)
    except AssertionError as exc:
        print(f"REPRODUCED FLAW 5 SUCCESSFULLY (AssertionError raised):\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"REPRODUCED FLAW 5 WITH EXCEPTION ({type(exc).__name__}):\n{exc}")
        sys.exit(1)
