"""Conftest for lineagelens-mcp tests.

The `mcp` package (FastMCP) is not installed in the backend virtualenv.
This conftest injects a minimal stub so that the import in lineagelens-mcp.py
succeeds, and also exposes the loaded module as a fixture.
"""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_mcp_stub() -> None:
    """Install a minimal `mcp` package stub into sys.modules."""
    if "mcp" in sys.modules:
        return

    mcp_pkg = types.ModuleType("mcp")
    server_pkg = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

        def run(self):
            pass

    fastmcp_mod.FastMCP = FakeFastMCP
    mcp_pkg.server = server_pkg
    server_pkg.fastmcp = fastmcp_mod

    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


_make_mcp_stub()


def _load_mcp_module():
    """Import the hyphen-named lineagelens-mcp.py via importlib."""
    mcp_path = Path(__file__).parent.parent / "lineagelens-mcp.py"
    spec = importlib.util.spec_from_file_location("lineagelens_mcp", mcp_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def mcp_mod():
    """Return the loaded lineagelens-mcp module (session-scoped for speed)."""
    return _load_mcp_module()
