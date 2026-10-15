"""generate_mcp_walkthrough.py - Scripted MCP protocol walkthrough generator.

Executes a scripted end-to-end walkthrough of Conflict Radar MCP tools over stdio:
- Connects to `mcp_server.py` via official MCP Python client
- Demonstrates JSON-RPC payload structures for `claim`, `check`, and `release`
- Illustrates waiting behavior upon `BLOCK` and reactive promotion upon release
- Outputs markdown protocol documentation to docs/SCRIPTED_MCP_WALKTHROUGH.md
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
    log_turn("User Instruction", "Please refactor `normalize()` in `utils.py` to strip leading and trailing whitespace. Ensure you coordinate with other agents using Conflict Radar MCP before making any changes.")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Upstream agent already has an active lock on handler.py::parse_request (SIGNATURE)
            upstream_claim = await call_mcp(session, "claim", {
                "agent_id": "upstream-agent",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            
            # Dev Agent inspects intent and calls claim tool
            log_turn("Agent Workflow (narration)", 
                "Before modifying `utils.py::normalize`, the agent consults Conflict Radar via the `claim` MCP tool to verify whether any concurrent agents are touching dependent symbols."
            )

            claim_req = {
                "agent_id": "test-dev-agent",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            }
            log_turn("Agent Client -> MCP Request", 
                f"**Tool:** `claim`\n```json\n{json.dumps(claim_req, indent=2)}\n```"
            )

            claim_resp = await call_mcp(session, "claim", claim_req)
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(claim_resp, indent=2)}\n```"
            )

            agent_claim_id = claim_resp["claim_id"]

            # Dev Agent receives BLOCK verdict
            log_turn("Agent State Transition (narration)", 
                f"Conflict Radar returned a **BLOCK** verdict. An active lock is held upstream on `handler.py::parse_request` (claim ID `{claim_resp.get('conflicting_claim_id')}`). Modifying `utils.py::normalize` now would risk breaking an in-flight caller contract. The agent enters a `WAITING` state and polls `check()` until the upstream lock clears."
            )

            # Agent calls check()
            check_resp1 = await call_mcp(session, "check", {"claim_id": agent_claim_id})
            log_turn("Agent Client -> MCP Request", 
                f"**Tool:** `check`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(check_resp1, indent=2)}\n```"
            )

            # Upstream agent completes work and releases
            print("\n[Harness] Upstream agent finishes refactoring and calls release()...")
            release_upstream = await call_mcp(session, "release", {"claim_id": upstream_claim["claim_id"]})
            log_turn("Harness Event (Upstream Release)", 
                f"`upstream-agent` finishes its code changes and calls `release` on `{upstream_claim['claim_id']}`.\nFalkorDB atomically removes the `BLOCKED_BY` relationship and promotes the waiting claim to `active`:\n```json\n{json.dumps(release_upstream.get('unblocked_claims', []), indent=2)}\n```"
            )

            # Dev Agent calls check() again
            check_resp2 = await call_mcp(session, "check", {"claim_id": agent_claim_id})
            log_turn("Agent Client -> MCP Request", 
                f"**Tool:** `check`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(check_resp2, indent=2)}\n```"
            )

            log_turn("Agent Action (narration)", 
                "Status has been promoted to **ACTIVE** (`blocked_by: null`). It is now safe to apply code edits without conflicting with peers.\n\n```python\n# utils.py\ndef normalize(text: str) -> str:\n    return text.strip().lower()\n```"
            )

            # Dev Agent releases lock
            release_dev = await call_mcp(session, "release", {"claim_id": agent_claim_id})
            log_turn("Agent Client -> MCP Request", 
                f"**Tool:** `release`\n```json\n{json.dumps({'claim_id': agent_claim_id}, indent=2)}\n```"
            )
            log_turn("Conflict Radar -> MCP Response", 
                f"```json\n{json.dumps(release_dev, indent=2)}\n```"
            )

            log_turn("Agent Response (narration)", 
                "Refactoring of `normalize()` in `utils.py` complete. Coordinated via Conflict Radar: initially waited for an upstream signature lock on `handler.py::parse_request` to clear, acquired an active lock upon release, applied edits safely, and released the lock."
            )

    # Save to docs/SCRIPTED_MCP_WALKTHROUGH.md
    out_file = os.path.join(REPO_ROOT, "docs", "SCRIPTED_MCP_WALKTHROUGH.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("# Scripted MCP Protocol Walkthrough\n\n")
        f.write("> ⚠️ **Note:** This is a scripted walkthrough of the MCP protocol. The agent turns are written by the test harness script, not generated by an LLM. It demonstrates the exact JSON-RPC request and response payload exchange between an MCP client and Conflict Radar over stdio.\n\n")
        f.write(f"*Captured at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write("---\n\n")
        f.write("\n".join(transcript_lines))
    
    print(f"\n[Saved] Scripted MCP walkthrough written to: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
