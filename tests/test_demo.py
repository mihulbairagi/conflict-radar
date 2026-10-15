"""tests/test_demo.py - Verifies that demo.py --fast executes against real FalkorDB.

Asserts that:
1. demo.py completes with returncode == 0
2. All four scenarios execute in exact chronological order (SAFE -> WARN -> BLOCK -> PRECEDENT)
3. The final success banner is printed
"""

import sys
import subprocess
import pytest
from graph_db import GraphDB


def is_falkordb_available() -> bool:
    """Check whether real FalkorDB instance is reachable."""
    db = GraphDB(strict=True)
    try:
        db.connect()
        db.graph.query("RETURN 1")
        return True
    except Exception:
        return False


skip_if_no_falkordb = pytest.mark.skipif(
    not is_falkordb_available(),
    reason="Real FalkorDB instance is offline (localhost:6379 unreachable)."
)


@skip_if_no_falkordb
def test_demo_runner_fast_mode():
    """Execute demo.py with --fast flag and verify all four verdicts in order."""
    cmd = [sys.executable, "demo.py", "--fast"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    # 1. Must exit with code 0
    assert result.returncode == 0, f"demo.py failed with stderr: {result.stderr}\nstdout: {result.stdout}"

    stdout = result.stdout

    # 2. Assert all 4 scenarios appear in exact chronological sequence
    s1_pos = stdout.find("SCENARIO 1: SAFE")
    s2_pos = stdout.find("SCENARIO 2: WARN")
    s3_pos = stdout.find("SCENARIO 3: BLOCK")
    s4_pos = stdout.find("SCENARIO 4: PRECEDENT")

    assert s1_pos != -1, "Scenario 1 (SAFE) not found in demo output"
    assert s2_pos != -1, "Scenario 2 (WARN) not found in demo output"
    assert s3_pos != -1, "Scenario 3 (BLOCK) not found in demo output"
    assert s4_pos != -1, "Scenario 4 (PRECEDENT) not found in demo output"

    assert s1_pos < s2_pos < s3_pos < s4_pos, "Scenarios did not execute in expected order"

    # 3. Assert specific verdict strings
    assert "VERDICT: SAFE" in stdout
    assert "VERDICT: WARN" in stdout
    assert "VERDICT: BLOCK" in stdout
    assert "Historical Collision Memory:" in stdout
    assert "ALL 4 SCENARIOS COMPLETED SUCCESSFULLY!" in stdout
