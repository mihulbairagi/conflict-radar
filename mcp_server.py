"""mcp_server.py - MCP Server for Conflict Radar.

Exposes tools to AI coding agents (Claude Code, Antigravity, Cursor) over MCP:
- claim: evaluate proposed code modification and register lock
- check: check current lock status or blocking claim if WAITING
- release: release lock and re-evaluate waiting claims
- ping: connectivity verification
"""

import sys
from typing import Dict, Any
from graph_db import GraphDB
from radar_core import ConflictRadar

try:
    from mcp.server.mcpserver import MCPServer
    server = MCPServer("conflict-radar")
except ImportError:
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("conflict-radar")

# Global instances initialized on startup in strict mode
db = GraphDB(strict=True)
try:
    db.connect()
except ConnectionError as e:
    print(f"FATAL: Conflict Radar MCP Server requires running FalkorDB: {e}", file=sys.stderr)

radar = ConflictRadar(db=db)


@server.tool()
def ping() -> str:
    """Verify MCP connectivity with Conflict Radar."""
    return "ok"


@server.tool()
def claim(agent_id: str, symbol_id: str, change_type: str = "BODY") -> Dict[str, Any]:
    """Declare intent to edit a symbol and receive an instant graph verdict.
    
    Args:
        agent_id: Unique identifier for your agent (e.g. 'claude-1', 'antigravity-1').
        symbol_id: Target symbol identifier (e.g. 'parser.py::parse').
        change_type: 'SIGNATURE' (renaming, changing params, deleting) or 'BODY' (internal logic only).
    """
    verdict = radar.claim(agent_id=agent_id, symbol_id=symbol_id, change_type=change_type)
    return {
        "verdict": verdict.verdict,
        "claim_id": verdict.claim_id,
        "conflicting_claim_id": verdict.conflicting_claim_id,
        "path": verdict.path,
        "hops": verdict.hops,
        "reason": verdict.reason,
        "precedent_count": verdict.precedent_count,
        "last_collision_time": verdict.last_collision_time
    }


@server.tool()
def check(claim_id: str) -> Dict[str, Any]:
    """Check the current status of a claim and, if WAITING, which claim blocks it.
    
    Args:
        claim_id: The claim ID returned from a prior claim call.
    """
    return radar.check(claim_id)


@server.tool()
def release(claim_id: str) -> Dict[str, Any]:
    """Release your lock on a symbol after finishing code modifications.
    
    Automatically re-checks waiting claims and promotes eligible ones to ACTIVE.
    
    Args:
        claim_id: The claim ID to release.
    """
    return radar.release(claim_id)


if __name__ == "__main__":
    if not db._connected:
        print("FATAL: FalkorDB is not connected. Exiting.", file=sys.stderr)
        sys.exit(1)
    print("Conflict Radar MCP Server starting (stdio)...", file=sys.stderr)
    server.run()
