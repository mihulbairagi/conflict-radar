"""create_scenario3_and_screenshot.py - Sets up Scenario 3 state in FalkorDB and captures a screenshot."""

import os
import sys
import time
import subprocess
from falkordb import FalkorDB
from indexer import index_codebase
from graph_db import GraphDB

# 1. Connect and reset
db = GraphDB(strict=True)
db.connect()
db.clear()

# 2. Index sample_repo
sample_dir = os.path.join(os.path.dirname(__file__), "sample_repo")
client = FalkorDB(host="localhost", port=6379)
graph = client.select_graph("conflict_radar")
index_codebase(sample_dir, graph=graph)

# 3. Setup Scenario 3 state
# Agent 1 claims utils.py::normalize (BODY, active)
c1_id = db.add_claim("agent-1", "utils.py::normalize", "BODY", status="active")

# Agent 2 claims handler.py::parse_request (SIGNATURE, waiting)
c2_id = db.add_claim("agent-2", "handler.py::parse_request", "SIGNATURE", status="waiting")
db.add_blocked_by(c2_id, c1_id)

# Record BLOCK collision
path_text = "handler.py::parse_request -> validator.py::validate_input -> utils.py::normalize"
db.record_collision(
    "handler.py::parse_request",
    "utils.py::normalize",
    "BLOCK",
    path_text,
    agent_a="agent-2",
    agent_b="agent-1"
)

print(f"Scenario 3 state established: c1={c1_id}, c2={c2_id}")

# 4. Give dashboard SSE a moment
time.sleep(2)

# 5. Take headless screenshot using Edge
edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
screenshot_path = os.path.abspath("screenshot_scenario3.png")

cmd = [
    edge_exe,
    "--headless",
    "--disable-gpu",
    f"--screenshot={screenshot_path}",
    "--window-size=1440,900",
    "--virtual-time-budget=3000",
    "http://localhost:8000/?no_sse=1"
]

print("Capturing screenshot via headless Edge...")
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
print("Edge return code:", proc.returncode)

if os.path.exists(screenshot_path):
    print(f"SUCCESS: Screenshot saved to {screenshot_path} ({os.path.getsize(screenshot_path)} bytes)")
else:
    print("FAILED to generate screenshot")
