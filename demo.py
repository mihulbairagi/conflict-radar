"""demo.py - Interactive CLI Demo Runner for Conflict Radar (Step 7).

Demonstrates real-time conflict prevention across simulated autonomous coding agents:
1. Resets database state and indexes ./sample_repo into FalkorDB.
2. Spawns and communicates with `mcp_server.py` strictly over stdio via the official MCP Python SDK.
3. Steps through 4 core radar traffic control scenarios:
   - Scenario 1 (SAFE): Unrelated edits across independent domains (billing vs handler).
   - Scenario 2 (WARN): Multi-hop transitive dependency with body-only edits.
   - Scenario 3 (BLOCK & UNBLOCK): Signature change collision causing blocking and automatic cascade unblock.
   - Scenario 4 (PRECEDENT): Collision memory and historical precedent retrieval.

Usage:
    python demo.py          # Interactive mode with Enter-key pauses for video recording
    python demo.py --fast   # Automated non-interactive mode for testing and CI
"""

import os
import sys
import json
import asyncio
import argparse
from typing import Dict, Any, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from falkordb import FalkorDB
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from indexer import index_codebase

# ANSI Color Codes for terminal visual feedback
BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
DIM = "\033[90m"


def print_banner(text: str, color: str = CYAN):
    """Render a prominent ASCII banner."""
    bar = "=" * 72
    print(f"\n{color}{BOLD}{bar}")
    print(f"  {text}")
    print(f"{bar}{RESET}\n")


def print_card(title: str, verdict: str, agent: str, symbol: str, change_type: str,
               hops: int, path: list, reason: str, precedent: int, last_time: Optional[float] = None):
    """Render a structured terminal card matching the dashboard visual."""
    v_color = GREEN if verdict == "SAFE" else (YELLOW if verdict == "WARN" else RED)
    print(f"  {BOLD}+------------------------------------------------------------------------+{RESET}")
    print(f"  {BOLD}|{RESET}  {v_color}{BOLD}VERDICT: {verdict:<10}{RESET}  | {CYAN}Agent: {agent:<12}{RESET} | Symbol: {symbol:<26} {BOLD}|{RESET}")
    print(f"  {BOLD}|{RESET}  Change Type: {change_type:<10}| Hops: {hops:<13}| Precedent Count: {precedent:<19} {BOLD}|{RESET}")
    print(f"  {BOLD}+------------------------------------------------------------------------+{RESET}")
    path_str = " -> ".join(path) if path else "None (Independent)"
    print(f"  {BOLD}|{RESET}  {DIM}Path:{RESET}   {path_str:<62} {BOLD}|{RESET}")
    print(f"  {BOLD}|{RESET}  {DIM}Reason:{RESET} {reason[:62]:<62} {BOLD}|{RESET}")
    if last_time:
        print(f"  {BOLD}|{RESET}  {MAGENTA}Historical Collision Memory: Prior incident recorded at t={last_time:<14.2f}{RESET} {BOLD}|{RESET}")
    print(f"  {BOLD}+------------------------------------------------------------------------+{RESET}")


def pause_prompt(fast_mode: bool, step_name: str = ""):
    """Prompt user to press Enter to continue unless --fast flag is provided."""
    if fast_mode:
        return
    print(f"\n{CYAN}{BOLD}>>> Press [ENTER] to proceed to {step_name}...{RESET}", end="", flush=True)
    input()
    print()


def reset_and_index():
    """Reset transient graph state and re-index sample_repo."""
    print(f"{DIM}[Setup] Connecting to FalkorDB on localhost:6379...{RESET}")
    client = FalkorDB(host="localhost", port=6379)
    graph = client.select_graph("conflict_radar")

    # Clear transient claims and collisions
    print(f"{DIM}[Setup] Clearing transient Claims & Collisions...{RESET}")
    graph.query("MATCH (c:Claim) DETACH DELETE c")
    graph.query("MATCH (col:Collision) DETACH DELETE col")

    # Re-index sample_repo
    sample_dir = os.path.join(os.path.dirname(__file__), "sample_repo")
    print(f"{DIM}[Setup] Indexing code graph from '{sample_dir}'...{RESET}")
    stats = index_codebase(sample_dir, graph=graph)
    print(f"{GREEN}[Setup] Codebase indexed: {stats['files_count']} files, {stats['symbols_count']} symbols, {stats['calls_count']} calls.{RESET}\n")


async def call_mcp(session: ClientSession, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to invoke MCP tools over stdio and parse JSON text results."""
    res = await session.call_tool(tool_name, arguments)
    if not res.content:
        return {}
    txt = res.content[0].text
    try:
        return json.loads(txt)
    except Exception:
        return {"result": txt}


async def run_demo(fast_mode: bool):
    """Main scripted demo flow executing the 4 required scenarios."""
    print_banner("== CONFLICT RADAR - MULTI-AGENT TRAFFIC CONTROL DEMO ==", CYAN)
    print(f"Interactive Live Dashboard: {BOLD}http://localhost:8000{RESET}")
    print(f"Target Codebase:            {BOLD}./sample_repo{RESET}")
    print(f"Execution Mode:             {BOLD}{'FAST (Automated)' if fast_mode else 'INTERACTIVE (Video Recording)'}{RESET}\n")

    # 1. Reset state and index sample repo
    reset_and_index()

    # 2. Spawn real MCP server process via official stdio transport
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        env=dict(os.environ)
    )

    print(f"{DIM}[MCP] Spawning python mcp_server.py via stdio...{RESET}")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"{GREEN}[MCP] Connection established & initialized with Conflict Radar MCP Server.{RESET}\n")

            # Verify ping
            ping_res = await call_mcp(session, "ping", {})
            if ping_res.get("result") != "ok" and ping_res.get("raw") != "ok":
                print(f"{RED}[FAIL] MCP ping check failed: {ping_res}{RESET}")
                sys.exit(1)

            # ==================================================================
            # SCENARIO 1: SAFE (Unrelated Functions)
            # ==================================================================
            pause_prompt(fast_mode, "Scenario 1 (SAFE)")
            print_banner("SCENARIO 1: SAFE - Independent Edits Across Separate Domains", GREEN)
            print(f"• Agent 1 intends to modify {BOLD}billing.py::compute_tax{RESET} (Billing Domain).")
            print(f"• Agent 2 intends to modify {BOLD}handler.py::parse_request{RESET} (API Gateway Domain).")
            print(f"• Radar evaluates call graph: No transitive dependency exists between them.\n")

            # Agent 1 locks compute_tax
            c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "billing.py::compute_tax",
                "change_type": "BODY"
            })
            print_card("Agent 1 Claim", c1["verdict"], "agent-1", "billing.py::compute_tax", "BODY",
                       c1["hops"], c1["path"], c1["reason"], c1["precedent_count"])

            # Agent 2 claims parse_request
            c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "BODY"
            })
            print_card("Agent 2 Claim", c2["verdict"], "agent-2", "handler.py::parse_request", "BODY",
                       c2["hops"], c2["path"], c2["reason"], c2["precedent_count"])

            if c2["verdict"] != "SAFE":
                print(f"{RED}[FAIL] Expected SAFE verdict for Scenario 1, got {c2['verdict']}{RESET}")
                sys.exit(1)

            # Cleanup claims
            await call_mcp(session, "release", {"claim_id": c1["claim_id"]})
            await call_mcp(session, "release", {"claim_id": c2["claim_id"]})
            print(f"{DIM}[State Cleaned] Released active claims for Scenario 1.{RESET}")

            # ==================================================================
            # SCENARIO 2: WARN (Transitive Body Edits along 2-Hop Chain)
            # ==================================================================
            pause_prompt(fast_mode, "Scenario 2 (WARN)")
            print_banner("SCENARIO 2: WARN - Multi-Hop Dependency with Body-Only Changes", YELLOW)
            print(f"• Agent 1 modifies body of {BOLD}utils.py::normalize{RESET} (Leaf Callee).")
            print(f"• Agent 2 modifies body of {BOLD}handler.py::parse_request{RESET} (Root Caller, 2 hops away).")
            print(f"• Radar evaluates call graph: parse_request -> validate_input -> normalize.")
            print(f"• Since neither agent breaks function signatures, Radar issues a non-blocking WARN.\n")

            # Agent 1 locks normalize
            c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })
            print_card("Agent 1 Claim", c1["verdict"], "agent-1", "utils.py::normalize", "BODY",
                       c1["hops"], c1["path"], c1["reason"], c1["precedent_count"])

            # Agent 2 claims parse_request
            c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "BODY"
            })
            print_card("Agent 2 Claim", c2["verdict"], "agent-2", "handler.py::parse_request", "BODY",
                       c2["hops"], c2["path"], c2["reason"], c2["precedent_count"])

            if c2["verdict"] != "WARN":
                print(f"{RED}[FAIL] Expected WARN verdict for Scenario 2, got {c2['verdict']}{RESET}")
                sys.exit(1)

            # Cleanup claims
            await call_mcp(session, "release", {"claim_id": c1["claim_id"]})
            await call_mcp(session, "release", {"claim_id": c2["claim_id"]})
            print(f"{DIM}[State Cleaned] Released active claims for Scenario 2.{RESET}")

            # ==================================================================
            # SCENARIO 3: BLOCK & AUTOMATIC UNBLOCK (Signature Collision)
            # ==================================================================
            pause_prompt(fast_mode, "Scenario 3 (BLOCK & UNBLOCK)")
            print_banner("SCENARIO 3: BLOCK & UNBLOCK - Signature Conflict & Reactive Promotion", RED)
            print(f"• Agent 1 modifies body of {BOLD}utils.py::normalize{RESET}.")
            print(f"• Agent 2 attempts a {BOLD}SIGNATURE{RESET} change on {BOLD}handler.py::parse_request{RESET} (2 hops away).")
            print(f"• Radar detects signature risk: Agent 2 is placed in {BOLD}WAITING{RESET} status with BLOCKED_BY edge.")
            print(f"• Once Agent 1 finishes and releases, Agent 2 is automatically unblocked to {BOLD}ACTIVE{RESET}!\n")

            # Agent 1 locks normalize
            c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })
            print_card("Agent 1 Claim", c1["verdict"], "agent-1", "utils.py::normalize", "BODY",
                       c1["hops"], c1["path"], c1["reason"], c1["precedent_count"])

            # Agent 2 claims SIGNATURE on parse_request -> BLOCK!
            c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            print_card("Agent 2 Claim", c2["verdict"], "agent-2", "handler.py::parse_request", "SIGNATURE",
                       c2["hops"], c2["path"], c2["reason"], c2["precedent_count"])

            if c2["verdict"] != "BLOCK":
                print(f"{RED}[FAIL] Expected BLOCK verdict for Scenario 3, got {c2['verdict']}{RESET}")
                sys.exit(1)

            # Verify Agent 2 is waiting
            chk_wait = await call_mcp(session, "check", {"claim_id": c2["claim_id"]})
            print(f"\n  {CYAN}Status check on Agent 2 claim:{RESET} {YELLOW}{chk_wait['status'].upper()}{RESET} (Blocked By: {chk_wait.get('blocked_by')})")

            # Pause before showing the release cascade
            pause_prompt(fast_mode, "Agent 1 Release & Cascade Unblock")

            # Agent 1 completes work and releases lock
            print(f"{CYAN}>>> Agent 1 completes code changes and calls release({c1['claim_id']})...{RESET}")
            rel_res = await call_mcp(session, "release", {"claim_id": c1["claim_id"]})
            print(f"  {GREEN}Release Success:{RESET} {rel_res['status']}")
            print(f"  {GREEN}Unblocked Waiting Claims:{RESET} {[u['claim_id'] for u in rel_res.get('unblocked_claims', [])]}")

            # Agent 2 checks its claim status
            chk_active = await call_mcp(session, "check", {"claim_id": c2["claim_id"]})
            print(f"  {GREEN}Agent 2 new status:{RESET} {BOLD}{chk_active['status'].upper()}{RESET} (Now cleared to proceed with code edit!)\n")

            if chk_active["status"] != "active":
                print(f"{RED}[FAIL] Expected Agent 2 to be promoted to active, got {chk_active['status']}{RESET}")
                sys.exit(1)

            # Cleanup Agent 2 claim
            await call_mcp(session, "release", {"claim_id": c2["claim_id"]})
            print(f"{DIM}[State Cleaned] Released Agent 2 claim for Scenario 3.{RESET}")

            # ==================================================================
            # SCENARIO 4: PRECEDENT (Historical Collision Memory)
            # ==================================================================
            pause_prompt(fast_mode, "Scenario 4 (PRECEDENT Memory)")
            print_banner("SCENARIO 4: PRECEDENT - Graph Memory of Prior Collisions", MAGENTA)
            print(f"• Agents attempt to modify {BOLD}utils.py::normalize{RESET} and {BOLD}handler.py::parse_request{RESET} again.")
            print(f"• FalkorDB queries historical :Collision nodes via [:INVOLVED] edges.")
            print(f"• Radar cites prior incident count and timestamp in the verdict reason!\n")

            # Agent 1 locks normalize
            c1 = await call_mcp(session, "claim", {
                "agent_id": "agent-1",
                "symbol_id": "utils.py::normalize",
                "change_type": "BODY"
            })

            # Agent 2 claims SIGNATURE on parse_request -> BLOCK with precedent!
            c2 = await call_mcp(session, "claim", {
                "agent_id": "agent-2",
                "symbol_id": "handler.py::parse_request",
                "change_type": "SIGNATURE"
            })
            print_card("Agent 2 Claim", c2["verdict"], "agent-2", "handler.py::parse_request", "SIGNATURE",
                       c2["hops"], c2["path"], c2["reason"], c2["precedent_count"], c2.get("last_collision_time"))

            if c2["verdict"] != "BLOCK":
                print(f"{RED}[FAIL] Expected BLOCK verdict for Scenario 4, got {c2['verdict']}{RESET}")
                sys.exit(1)

            if c2["precedent_count"] < 1:
                print(f"{RED}[FAIL] Expected precedent_count >= 1 for Scenario 4, got {c2['precedent_count']}{RESET}")
                sys.exit(1)

            # Cleanup claims
            await call_mcp(session, "release", {"claim_id": c1["claim_id"]})
            await call_mcp(session, "release", {"claim_id": c2["claim_id"]})
            print(f"{DIM}[State Cleaned] Released claims for Scenario 4.{RESET}")

            print_banner("*** ALL 4 SCENARIOS COMPLETED SUCCESSFULLY! ***", GREEN)
            print(f"{BOLD}Summary of Traffic Control Verified:{RESET}")
            print(f"  1. SAFE:      Unrelated domains proceed simultaneously without locks.")
            print(f"  2. WARN:      2-hop transitive body edits flagged without blocking development.")
            print(f"  3. BLOCK:     Signature change blocked callee; unblocked automatically on release.")
            print(f"  4. PRECEDENT: Prior collisions recorded and surfaced as historical evidence.\n")


def main():
    parser = argparse.ArgumentParser(description="Conflict Radar Live Demo Runner")
    parser.add_argument("--fast", action="store_true", help="Run without interactive pauses (for CI/automated testing)")
    args = parser.parse_args()

    try:
        asyncio.run(run_demo(fast_mode=args.fast))
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[Demo Interrupted by User]{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
