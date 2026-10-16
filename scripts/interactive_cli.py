"""interactive_cli.py - Real-Time Hands-on Playground for Conflict Radar.

Allows you to interactively lock, block, and release functions in FalkorDB
while watching the live Cytoscape.js dashboard react in real time at http://localhost:8000.
"""

import sys
import os
import time

# Auto-bootstrap virtualenv if running in a system Python missing dependencies
try:
    import falkordb
except ImportError:
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(repo_dir, ".venv", "bin", "python"),
        os.path.join(repo_dir, "venv", "Scripts", "python.exe"),
        os.path.join(repo_dir, "venv", "bin", "python"),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            import subprocess
            res = subprocess.run([cand] + sys.argv, env=os.environ)
            sys.exit(res.returncode)
    raise

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

SAMPLE_SYMBOLS = [
    ("handler.py::parse_request", "Root API Gateway Caller"),
    ("validator.py::validate_input", "Middle Validator (1 hop)"),
    ("utils.py::normalize", "Leaf Callee Utility (2 hops from handler)"),
    ("billing.py::compute_tax", "Independent Billing Domain (0 hops)"),
    ("billing.py::generate_invoice", "Independent Invoice Generator (0 hops)")
]


def print_header():
    print(f"\n{BOLD}{CYAN}========================================================================{RESET}")
    print(f"{BOLD}{CYAN}  📡 CONFLICT RADAR - REAL-TIME TRAFFIC CONTROL PLAYGROUND{RESET}")
    print(f"  Live Visual Dashboard: {BOLD}http://localhost:8000{RESET}")
    print(f"{BOLD}{CYAN}========================================================================{RESET}\n")


def print_verdict(v):
    color = GREEN if v.verdict == "SAFE" else (YELLOW if v.verdict == "WARN" else RED)
    print(f"\n{color}------------------------------------------------------------------------{RESET}")
    print(f"  {BOLD}VERDICT:{RESET}       {color}{BOLD}{v.verdict}{RESET}")
    print(f"  {BOLD}Claim ID:{RESET}      {v.claim_id or 'None'}")
    print(f"  {BOLD}Hops:{RESET}          {v.hops}")
    if v.path:
        print(f"  {BOLD}Path:{RESET}          {' -> '.join(v.path)}")
    print(f"  {BOLD}Precedent:{RESET}     Seen {v.precedent_count} times before")
    print(f"  {BOLD}Reason:{RESET}        {v.reason}")
    print(f"{color}------------------------------------------------------------------------{RESET}")
    print(f"{MAGENTA}👉 Check your browser at http://localhost:8000 to see the live graph!{RESET}\n")


def claim_symbol_flow(radar: ConflictRadar):
    print(f"\n{BOLD}Select a function to claim:{RESET}")
    for idx, (sym, desc) in enumerate(SAMPLE_SYMBOLS, 1):
        print(f"  [{idx}] {sym} {DIM}({desc}){RESET}")
    print(f"  [9] Custom symbol ID")

    choice = input(f"\n{BOLD}Choose function (1-{len(SAMPLE_SYMBOLS)} or 9): {RESET}").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(SAMPLE_SYMBOLS):
        symbol_id = SAMPLE_SYMBOLS[int(choice) - 1][0]
    elif choice == "9":
        symbol_id = input("Enter symbol_id (e.g., utils.py::normalize): ").strip()
    else:
        print(f"{RED}Invalid selection.{RESET}")
        return

    agent_id = input(f"{BOLD}Enter Agent Name [default: agent-1]: {RESET}").strip() or "agent-1"
    
    print(f"{BOLD}Change Type:{RESET} [1] BODY (Internal logic only)  [2] SIGNATURE (Parameters/Contract)")
    ct_choice = input(f"Choose (1 or 2) [default: 1]: ").strip() or "1"
    change_type = "SIGNATURE" if ct_choice == "2" else "BODY"

    print(f"\n{DIM}[Action] Querying FalkorDB with openCypher shortestPath...{RESET}")
    verdict = radar.claim(agent_id=agent_id, symbol_id=symbol_id, change_type=change_type)
    print_verdict(verdict)


def list_claims(radar: ConflictRadar):
    active = radar.db.get_active_claims()
    waiting = radar.db.get_waiting_claims()
    all_claims = active + waiting

    print(f"\n{BOLD}Current Live Claims ({len(all_claims)} total):{RESET}")
    if not all_claims:
        print(f"  {DIM}(No active or waiting claims in FalkorDB right now){RESET}\n")
        return []

    for idx, c in enumerate(all_claims, 1):
        color = GREEN if c["status"] == "active" else YELLOW
        blocked_str = f" [BLOCKED BY {c.get('blocked_by')}]" if c.get("blocked_by") else ""
        print(f"  [{idx}] {color}[{c['status'].upper()}]{RESET} Agent: {BOLD}{c['agent_id']}{RESET} | Symbol: {c['symbol_id']} ({c['change_type']}) | ID: {c['id']}{blocked_str}")
    print()
    return all_claims


def release_claim_flow(radar: ConflictRadar):
    claims = list_claims(radar)
    if not claims:
        return

    sel = input(f"{BOLD}Enter the number [1-{len(claims)}] or Claim ID to release (or 'c' to cancel): {RESET}").strip()
    if sel.lower() == "c":
        return

    claim_id = None
    if sel.isdigit() and 1 <= int(sel) <= len(claims):
        claim_id = claims[int(sel) - 1]["id"]
    else:
        claim_id = sel

    print(f"\n{DIM}[Action] Calling radar.release('{claim_id}')...{RESET}")
    res = radar.release(claim_id)
    unblocked = res.get("unblocked", [])
    print(f"{GREEN}[SUCCESS] Claim {claim_id} marked DONE!{RESET}")
    if unblocked:
        print(f"{GREEN}{BOLD}🎉 Automatically Unblocked & Promoted to ACTIVE:{RESET} {GREEN}{unblocked}{RESET}")
    else:
        print(f"{DIM}No waiting claims were dependent on this claim.{RESET}")
    print(f"{MAGENTA}👉 Check your browser at http://localhost:8000 to see the update!{RESET}\n")


def run_scenario3_guided(radar: ConflictRadar):
    """Walk Mihul through Scenario 3 with live commentary and dashboard observation."""
    print(f"\n{BOLD}{MAGENTA}========================================================================{RESET}")
    print(f"{BOLD}{MAGENTA}  SCENARIO 3: THE SIGNATURE CONFLICT & AUTO-PROMOTION DEMO{RESET}")
    print(f"{BOLD}{MAGENTA}========================================================================{RESET}\n")

    print(f"{DIM}[Setup] Resetting claims for a clean demonstration...{RESET}")
    radar.db.reset_demo_state()
    time.sleep(0.5)

    print(f"{BOLD}STEP 1: Agent 1 modifies body of `utils.py::normalize` (Leaf Callee){RESET}")
    input(f"{YELLOW}Press [ENTER] to lock utils.py::normalize with Agent 1...{RESET}")
    v1 = radar.claim("agent-1", "utils.py::normalize", "BODY")
    print_verdict(v1)
    print(f"👀 Look at http://localhost:8000: `normalize` has a {GREEN}GREEN [ACTIVE]{RESET} badge!\n")

    print(f"{BOLD}STEP 2: Agent 2 attempts a SIGNATURE change on `handler.py::parse_request` (Caller, 2 hops away){RESET}")
    input(f"{YELLOW}Press [ENTER] to simulate Agent 2 attempting breaking change...{RESET}")
    v2 = radar.claim("agent-2", "handler.py::parse_request", "SIGNATURE")
    print_verdict(v2)
    print(f"👀 Look at http://localhost:8000:")
    print(f"  • `parse_request` is now {YELLOW}{BOLD}AMBER [WAITING]{RESET}!")
    print(f"  • Top banner shows the Collision Alert in prominent red!")
    print(f"  • The 2-hop path (parse_request -> validate_input -> normalize) is highlighted in RED!\n")

    print(f"{BOLD}STEP 3: Agent 1 finishes code edits and calls `release('{v1.claim_id}')`{RESET}")
    input(f"{YELLOW}Press [ENTER] to release Agent 1's lock and observe auto-promotion...{RESET}")
    res = radar.release(v1.claim_id)
    print(f"\n{GREEN}[RELEASED] Agent 1 lock released!{RESET}")
    print(f"{GREEN}{BOLD}🎉 Auto-Promoted Claims:{RESET} {res.get('unblocked')}")
    print(f"👀 Look at http://localhost:8000 right now:")
    print(f"  • `parse_request` has automatically flipped from AMBER to {GREEN}{BOLD}GREEN [ACTIVE]{RESET}!")
    print(f"  • Agent 2 is now clear to make its code edits!\n")


def main():
    db = GraphDB(strict=True)
    try:
        db.connect()
    except Exception as e:
        print(f"{RED}[FATAL] Could not connect to FalkorDB on localhost:6379: {e}{RESET}")
        print("Run: docker compose up -d")
        sys.exit(1)

    radar = ConflictRadar(db=db)

    # Ensure codebase is indexed
    sample_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_repo")
    index_codebase(sample_dir, graph=db.graph)

    while True:
        print_header()
        print(f"{BOLD}Choose an action to try:{RESET}")
        print(f"  {BOLD}[1]{RESET} Claim a Function (Acquire Lock as an Agent)")
        print(f"  {BOLD}[2]{RESET} Release a Claim (Watch Waiting Claims Unblock)")
        print(f"  {BOLD}[3]{RESET} View Live Active & Waiting Claims")
        print(f"  {BOLD}[4]{RESET} {MAGENTA}{BOLD}Run Guided Scenario 3 (Signature Conflict & Unblock Live){RESET}")
        print(f"  {BOLD}[5]{RESET} Reset Demo State (Clean All Locks)")
        print(f"  {BOLD}[0]{RESET} Exit\n")

        choice = input(f"{BOLD}Enter choice (0-5): {RESET}").strip()

        if choice == "1":
            claim_symbol_flow(radar)
        elif choice == "2":
            release_claim_flow(radar)
        elif choice == "3":
            list_claims(radar)
        elif choice == "4":
            run_scenario3_guided(radar)
        elif choice == "5":
            radar.db.reset_demo_state()
            print(f"\n{GREEN}[OK] All claims and collisions reset to clean state.{RESET}\n")
        elif choice == "0":
            print(f"\n{CYAN}Goodbye! Remember to keep your dashboard open at http://localhost:8000.{RESET}\n")
            break
        else:
            print(f"{RED}Invalid option. Please choose 0 to 5.{RESET}")

        input(f"\n{DIM}Press [ENTER] to return to menu...{RESET}")


if __name__ == "__main__":
    main()
