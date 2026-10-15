"""mcp_server.py - MCP Server for Conflict Radar.

Exposes tools to AI coding agents over Model Context Protocol (MCP).
Step 1: Provides a ping tool returning 'ok' to verify connectivity.
"""

import sys

try:
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("conflict-radar")
except ImportError:
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("conflict-radar")


@server.tool()
def ping() -> str:
    """Verify MCP connectivity with Conflict Radar."""
    return "ok"


if __name__ == "__main__":
    print("Conflict Radar MCP Server starting (stdio)...", file=sys.stderr)
    server.run()
