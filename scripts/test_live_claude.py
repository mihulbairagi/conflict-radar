"""test_live_claude.py - Companion test harness for live Claude Code / AI agent verification.

This script sets up a live multi-agent collision scenario in FalkorDB so you can test
real AI coding agents (Claude Code, Cursor, or any MCP client) against Conflict Radar.

Usage:
    # 1. Interactive mode (Recommended):
    python scripts/test_live_claude.py

    # 2. Command-line flags (Alternative):
    python scripts/test_live_claude.py --setup
    python scripts/test_live_claude.py --status
    python scripts/test_live_claude.py --release
"""

import sys
import os
import time
import argparse

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_db import GraphDB
from radar_core import ConflictRadar
from indexer import index_codebase


CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


LOCK_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".live_test_lock.txt")


def get_radar() -> ConflictRadar:
    db = GraphDB(strict=True)
    try:
        db.connect()
    except Exception as e:
        print(f"{RED}[ERROR] Could not connect to FalkorDB on localhost:6379: {e}{RESET}")
        print("Ensure Docker container is running: docker compose up -d")
        sys.exit(1)
    return ConflictRadar(db=db)


def setup_background_lock(radar: ConflictRadar) -> str:
    """Index codebase, reset state, and place an active lock for agent-alice."""
    print(f"\n{BOLD}{CYAN}=== STEP 1: Setting up Background Upstream Agent Lock ==={RESET}")
    
    # 1. Ensure sample repo is indexed
    sample_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_repo")
    print(f"{DIM}[Setup] Indexing sample repo: {sample_dir}...{RESET}")
    radar.db.reset_demo_state()
    stats = index_codebase(sample_dir, graph=radar.db.graph)
    print(f"{GREEN}[Setup] Codebase indexed ({stats['symbols_count']} symbols, {stats['calls_count']} calls).{RESET}")

    # 2. Place active claim on utils.py::normalize
    verdict = radar.claim(
        agent_id="agent-alice",
        symbol_id="utils.py::normalize",
        change_type="BODY"
    )

    if verdict.verdict != "SAFE" or not verdict.claim_id:
        print(f"{RED}[FAIL] Could not establish initial lock: {verdict}{RESET}")
        sys.exit(1)

    claim_id = verdict.claim_id
    with open(LOCK_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(claim_id)

    print(f"\n{GREEN}[OK] Upstream lock successfully placed!{RESET}")
    print(f"  • Blocker Agent:  {BOLD}agent-alice{RESET}")
    print(f"  • Locked Symbol:  {BOLD}utils.py::normalize{RESET} (Callee leaf)")
    print(f"  • Claim ID:       {BOLD}{claim_id}{RESET}")
    print(f"  • Status:         {BOLD}ACTIVE{RESET}\n")

    return claim_id


def print_prompt_box():
    print(f"{BOLD}{MAGENTA}========================================================================{RESET}")
    print(f"{BOLD}{MAGENTA}  STEP 2: RUN CLAUDE CODE IN ANOTHER TERMINAL{RESET}")
    print(f"{BOLD}{MAGENTA}========================================================================{RESET}")
    print(f"Open a new terminal window in this repository and run:")
    print(f"  {BOLD}claude{RESET}\n")
    print(f"Paste this exact prompt into Claude Code:")
    print(f"{YELLOW}------------------------------------------------------------------------")
    print(f"Please inspect sample_repo/handler.py and refactor parse_request to accept")
    print(f"a new parameter auth_token (this is a SIGNATURE change).")
    print(f"")
    print(f"CRITICAL: Before making any file changes, coordinate with peer agents")
    print(f"by calling the Conflict Radar MCP 'claim' tool with your agent ID,")
    print(f"the symbol 'handler.py::parse_request', and change_type 'SIGNATURE'.")
    print(f"If the tool returns BLOCK, you must wait and periodically call check().")
    print(f"------------------------------------------------------------------------{RESET}\n")
    print(f"{BOLD}What to observe:{RESET}")
    print(f"1. Claude reads {BOLD}CLAUDE.md{RESET} and calls the MCP {BOLD}claim{RESET} tool.")
    print(f"2. Conflict Radar traverses FalkorDB: {CYAN}parse_request -> validate_input -> normalize{RESET}.")
    print(f"3. Radar detects the caller's SIGNATURE conflict and returns {RED}{BOLD}BLOCK{RESET}.")
    print(f"4. Claude receives status {YELLOW}'waiting'{RESET} with {YELLOW}'blocked_by'{RESET}.")
    print(f"5. Check {BOLD}http://localhost:8000{RESET} to see the real-time amber badge on parse_request!")
    print(f"{BOLD}{MAGENTA}========================================================================{RESET}\n")


def release_background_lock(radar: ConflictRadar, claim_id: str):
    """Release agent-alice's lock and trigger automatic promotion."""
    print(f"\n{BOLD}{CYAN}=== STEP 3: Releasing Upstream Lock ==={RESET}")
    print(f"{DIM}[Action] Calling radar.release('{claim_id}')...{RESET}")
    res = radar.release(claim_id)
    
    if os.path.exists(LOCK_CACHE_FILE):
        os.remove(LOCK_CACHE_FILE)

    unblocked = res.get("unblocked", [])
    print(f"\n{GREEN}[SUCCESS] Lock released!{RESET}")
    print(f"  • Released Claim: {claim_id}")
    if unblocked:
        print(f"  • {BOLD}Unblocked & Promoted Claims:{RESET} {GREEN}{unblocked}{RESET}")
        print(f"  • {BOLD}Result:{RESET} Claude Code is now {GREEN}ACTIVE{RESET} in FalkorDB!")
        print(f"  • On its next check() poll, Claude will see it is cleared and will apply its edits.")
    else:
        print(f"  • Note: No waiting claims were queued at the exact moment of release.")
    print(f"\nDashboard updated: Check {BOLD}http://localhost:8000{RESET} for live state.\n")


def check_status(radar: ConflictRadar):
    """Print current active and waiting claims."""
    claims = radar.db.get_active_claims() + radar.db.get_waiting_claims()
    print(f"\n{BOLD}Current FalkorDB Claims ({len(claims)} total):{RESET}")
    for c in claims:
        color = GREEN if c["status"] == "active" else (YELLOW if c["status"] == "waiting" else DIM)
        print(f"  • {color}[{c['status'].upper()}]{RESET} {c['agent_id']} -> {c['symbol_id']} ({c['change_type']}) ID: {c['id']}")


def main():
    parser = argparse.ArgumentParser(description="Live Claude Code Test Harness for Conflict Radar")
    parser.add_argument("--setup", action="store_true", help="Set up initial background lock")
    parser.add_argument("--release", action="store_true", help="Release background lock")
    parser.add_argument("--status", action="store_true", help="Show current claims status")
    args = parser.parse_args()

    radar = get_radar()

    if args.status:
        check_status(radar)
        return

    if args.setup:
        setup_background_lock(radar)
        print_prompt_box()
        return

    if args.release:
        if not os.path.exists(LOCK_CACHE_FILE):
            print(f"{RED}[ERROR] No saved lock found in {LOCK_CACHE_FILE}. Run --setup first.{RESET}")
            sys.exit(1)
        with open(LOCK_CACHE_FILE, "r", encoding="utf-8") as f:
            claim_id = f.read().strip()
        release_background_lock(radar, claim_id)
        return

    # Default: Interactive full-cycle walkthrough
    claim_id = setup_background_lock(radar)
    print_prompt_box()
    
    print(f"{YELLOW}>>> Press [ENTER] once Claude Code has called claim and is waiting in the other terminal...{RESET}")
    try:
        input()
    except EOFError:
        pass
        
    release_background_lock(radar, claim_id)


if __name__ == "__main__":
    main()
