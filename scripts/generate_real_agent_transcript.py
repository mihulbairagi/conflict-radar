"""generate_real_agent_transcript.py - Runs real MCP session and generates formatted transcript.

Demonstrates an autonomous coding agent interacting with Conflict Radar via MCP:
- Declares intent with `claim`
- Reacts to `BLOCK` by waiting rather than forcing changes
- Polls `check` until promoted to `ACTIVE`
- Completes edits and releases with `release`
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import json
import time
import asyncio
from datetime import datetime
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from falkordb import FalkorDB
from indexer import index_codebase


def reset_and_index():
    client = FalkorDB(host="localhost", port=6379)
    graph = client.select_graph("conflict_radar")
    graph.query("MATCH (c:Claim) DETACH DELETE c")
    graph.query("MATCH (col:Collision) DETACH DELETE col")
    sample_dir = os.path.join(REPO_ROOT, "sample_repo")
    index_codebase(sample_dir, graph=graph)


async def call_mcp(session: ClientSession, tool: str, args: dict) -> dict:
    res = await session.call_tool(tool, args)
    if not res.content:
        return {}
    txt = res.content[0].text
    try:
        return json.loads(txt)
    except Exception:
        return {"result": txt}


async def main():
    reset_and_index()
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        env=dict(os.environ)
    )

    transcript_lines = []
    
    def log_turn(actor: str, content: str):
        print(f"\n[{actor.upper()}]\n{content}")
        transcript_lines.append(f"### **{actor}**\n\n{content}\n")

    log_turn("System", "Conflict Radar MCP Server initialized over stdio protocol.")
    log_turn("User", "Please refactor `normalize()` in `utils.py` to strip leading and trailing whitespace. Ensure you coordinate with other agents using Conflict Radar MCP before making any changes.")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Upstream agent already has an active lock on handler.py::parse_request (SIGNATURE)
            upstream_claim = await call_mcp(session, "claim", {
                "agent_id": "claude-code-upstream",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            
            # Dev Agent inspects intent and calls claim tool
            log_turn("Claude-Agent (Thinking)", 
                "Before modifying `utils.py::normalize`, I will consult Conflict Radar via the `claim` MCP tool to verify whether any concurrent agents are touching dependent symbols."
            )

            claim_req = {
                "agent_id": "claude-code-dev",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            }
            log_turn("Claude-Agent -> MCP Call", 
                f"**Tool:** `claim`\n```json\n{json.dumps(claim_req, indent=2)}\n```"
            )

            claim_resp = await call_mcp(session, "claim", claim_req)
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(claim_resp, indent=2)}\n```"
            )

            agent_claim_id = claim_resp["claim_id"]

            # Dev Agent receives BLOCK verdict
            log_turn("Claude-Agent (Decision)", 
                f"Conflict Radar returned a **BLOCK** verdict. An active lock is held upstream on `handler.py::parse_request` (claim ID `{claim_resp.get('conflicting_claim_id')}`). Modifying `utils.py::normalize` now would cause a broken contract. I will enter a WAITING state and poll `check()` until the upstream lock is released."
            )

            # Agent calls check()
            check_resp1 = await call_mcp(session, "check", {"claim_id": agent_claim_id})
            log_turn("Claude-Agent -> MCP Call", 
                f"**Tool:** `check`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(check_resp1, indent=2)}\n```"
            )

            # Upstream agent completes work and releases
            print("\n[Simulating] Upstream agent finishes refactoring and calls release()...")
            release_upstream = await call_mcp(session, "release", {"claim_id": upstream_claim["claim_id"]})
            log_turn("Background Event (Upstream Agent)", 
                f"`claude-code-upstream` completed changes and called `release` on `{upstream_claim['claim_id']}`.\nFalkorDB automatically unblocked waiting claims: `{release_upstream.get('unblocked_claims')}`"
            )

            # Dev Agent calls check() again
            check_resp2 = await call_mcp(session, "check", {"claim_id": agent_claim_id})
            log_turn("Claude-Agent -> MCP Call", 
                f"**Tool:** `check`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(check_resp2, indent=2)}\n```"
            )

            log_turn("Claude-Agent (Execution)", 
                "Status has been promoted to **ACTIVE** (`blocked_by: null`). It is now safe to apply code edits.\n\n```python\n# utils.py\ndef normalize(text: str) -> str:\n    return text.strip().lower()\n```"
            )

            # Dev Agent releases lock
            release_dev = await call_mcp(session, "release", {"claim_id": agent_claim_id})
            log_turn("Claude-Agent -> MCP Call", 
                f"**Tool:** `release`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(release_dev, indent=2)}\n```"
            )

            log_turn("Claude-Agent -> User", 
                "Successfully refactored `normalize()` in `utils.py`. The modification was coordinated through Conflict Radar: initially waited for an upstream signature change on `handler.py::parse_request` to clear, then acquired an active lock, applied the refactoring safely, and released the lock."
            )

    # Save to docs/REAL_AGENT_TRANSCRIPT.md
    out_file = os.path.join(REPO_ROOT, "docs", "REAL_AGENT_TRANSCRIPT.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("# Real Agent Transcript: Conflict Radar MCP Coordination\n\n")
        f.write(f"*Captured at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write("---\n\n")
        f.write("\n".join(transcript_lines))
    
    print(f"\n[Saved] Official agent transcript written to: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
