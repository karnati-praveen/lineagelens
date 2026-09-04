"""Helper module loader for lineagelens-mcp reproduction tests.

Provides get_mcp_module() to dynamically load lineagelens-mcp.py with FastMCP stub.
"""
import importlib.util
import sys
import types
from pathlib import Path


def get_mcp_module():
    """Load the lineagelens-mcp module with FastMCP stub if needed."""
    if "mcp" not in sys.modules:
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

    mcp_path = Path(__file__).resolve().parent.parent / "lineagelens-mcp" / "lineagelens-mcp.py"
    spec = importlib.util.spec_from_file_location("lineagelens_mcp", mcp_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
