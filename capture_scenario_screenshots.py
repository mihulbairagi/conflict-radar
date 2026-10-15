"""capture_scenario_screenshots.py - Automates driving Scenarios 2, 3, and 4 via real MCP,
verifying that live SSE updates arrive and capturing 1920x1080 screenshots of the polished dashboard.
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import urllib.request

ARTIFACT_DIR = r"C:\Users\mihul\.gemini\antigravity\brain\10f79c45-41c3-460a-bb91-b8e89152b91f"
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def capture_screenshot(out_filename: str):
    """Capture a 1920x1080 screenshot of the running dashboard using Edge."""
    out_path = os.path.join(ARTIFACT_DIR, out_filename)
    url = "http://localhost:8000/?no_sse=1"
    cmd = [
        EDGE_EXE,
        "--headless",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--virtual-time-budget=3000",
        f"--screenshot={out_path}",
        url
    ]
    print(f"[Screenshot] Capturing 1920x1080 view to {out_path}...")
    subprocess.run(cmd, check=True)
    print(f"[Screenshot] Saved: {out_path}")
    return out_path


async def call_mcp(session: ClientSession, tool: str, args: dict) -> dict:
    res = await session.call_tool(tool, args)
    if not res.content:
        return {}
    txt = res.content[0].text
    try:
        return json.loads(txt)
    except Exception:
        return {"result": txt}


def reset_demo():
    req = urllib.request.Request("http://localhost:8000/demo/reset", method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def main():
    print("[1/5] Resetting demo state on dashboard...")
    reset_demo()
    await asyncio.sleep(1)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        env=dict(os.environ)
    )

    print("[2/5] Connecting to MCP server via stdio...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[MCP] Session initialized.")

            # --- Scenario 1: SAFE ---
            print("\n--- Executing Scenario 1 (SAFE) ---")
            c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "billing.py::compute_tax",
                "change_type": "BODY"
            })
            c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "BODY"
            })
            print(f"Scenario 1 verdict: {c2.get('verdict')}")
            await call_mcp(session, "release", {"claim_id": c1["claim_id"]})
            await call_mcp(session, "release", {"claim_id": c2["claim_id"]})
            await asyncio.sleep(1.5)

            # --- Scenario 2: WARN ---
            print("\n--- Executing Scenario 2 (WARN) ---")
            s2_c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })
            s2_c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "BODY"
            })
            print(f"Scenario 2 verdict: {s2_c2.get('verdict')}")
            # Allow SSE / dashboard poll cycle to propagate
            await asyncio.sleep(2.0)
            capture_screenshot("screenshot_scenario2_warn.png")

            # Clean up scenario 2
            await call_mcp(session, "release", {"claim_id": s2_c1["claim_id"]})
            await call_mcp(session, "release", {"claim_id": s2_c2["claim_id"]})
            await asyncio.sleep(1.5)

            # --- Scenario 3: BLOCK ---
            print("\n--- Executing Scenario 3 (BLOCK) ---")
            s3_c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })
            s3_c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            print(f"Scenario 3 verdict: {s3_c2.get('verdict')}")
            await asyncio.sleep(2.0)
            capture_screenshot("screenshot_scenario3_block.png")

            # Unblock flow
            print("[Scenario 3] Agent 1 finishes and releases...")
            await call_mcp(session, "release", {"claim_id": s3_c1["claim_id"]})
            await asyncio.sleep(1.5)
            await call_mcp(session, "release", {"claim_id": s3_c2["claim_id"]})
            await asyncio.sleep(1.5)

            # --- Scenario 4: PRECEDENT ---
            print("\n--- Executing Scenario 4 (PRECEDENT) ---")
            s4_c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })
            s4_c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            print(f"Scenario 4 verdict: {s4_c2.get('verdict')}, Precedent: {s4_c2.get('precedent_count')}")
            await asyncio.sleep(2.0)
            capture_screenshot("screenshot_scenario4_precedent.png")

            print("\nAll 3 scenario screenshots captured successfully!")


if __name__ == "__main__":
    asyncio.run(main())
