"""test_mcp_tools.py - Pytest suite for Step 4 MCP tools (claim, check, release).

Tests tool functions and the full lifecycle flow:
Agent 1 claims (ACTIVE) -> Agent 2 gets BLOCK (WAITING) ->
Agent 1 releases -> Agent 2 automatically transitions to ACTIVE.
"""

import pytest
from graph_db import GraphDB
import mcp_server


@pytest.fixture
def mcp_fixture():
    """Setup a fresh graph topology and re-bind mcp_server instances.
    Topology:
    - E calls A
    - A calls B
    - B calls C
    - D is unrelated
    """
    from radar_core import ConflictRadar
    mcp_server.db = GraphDB(strict=False)
    mcp_server.radar = ConflictRadar(db=mcp_server.db)
    db = mcp_server.db
    db.clear()

    # Files
    db.upsert_file("pkg/mod.py")
    db.upsert_file("pkg/other.py")

    # Symbols
    symbols = ["func_a", "func_b", "func_c", "func_e"]
    for s in symbols:
        sym_id = f"pkg/mod.py::{s}"
        db.upsert_symbol(sym_id, s, "pkg/mod.py", "function")
        db.add_contains("pkg/mod.py", sym_id)

    db.upsert_symbol("pkg/other.py::func_d", "func_d", "pkg/other.py", "function")
    db.add_contains("pkg/other.py", "pkg/other.py::func_d")

    # Calls: E -> A -> B -> C
    db.add_calls("pkg/mod.py::func_e", "pkg/mod.py::func_a")
    db.add_calls("pkg/mod.py::func_a", "pkg/mod.py::func_b")
    db.add_calls("pkg/mod.py::func_b", "pkg/mod.py::func_c")

    yield mcp_server

    db.clear()


def test_mcp_ping(mcp_fixture):
    """Test MCP ping tool."""
    assert mcp_server.ping() == "ok"


def test_mcp_claim_safe(mcp_fixture):
    """Test single agent claim returning SAFE."""
    res = mcp_server.claim("agent_1", "pkg/mod.py::func_a", change_type="BODY")
    assert res["verdict"] == "SAFE"
    assert res["conflicting_claim_id"] is None
    assert res["hops"] == 0

    # Verify status is active
    status_info = mcp_server.check(res["claim_id"])
    assert status_info["status"] == "active"


def test_mcp_full_blocking_and_unblock_flow(mcp_fixture):
    """Core Flow Test:
    1. Agent 1 claims signature of B -> SAFE (ACTIVE)
    2. Agent 2 claims body of A (calls B) -> BLOCK (WAITING, BLOCKED_BY Agent 1)
    3. check(claim_2) confirms WAITING and identifies blocker
    4. Agent 1 calls release(claim_1) -> Agent 2 automatically unblocked to ACTIVE
    5. check(claim_2) confirms ACTIVE
    """
    # 1. Agent 1 claims signature of B
    res_1 = mcp_server.claim("agent_1", "pkg/mod.py::func_b", change_type="SIGNATURE")
    assert res_1["verdict"] == "SAFE"
    claim_1_id = res_1["claim_id"]

    # 2. Agent 2 claims body of caller A (A calls B)
    res_2 = mcp_server.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert res_2["verdict"] == "BLOCK"
    assert res_2["conflicting_claim_id"] == claim_1_id
    assert res_2["path"] == ["pkg/mod.py::func_a", "pkg/mod.py::func_b"]
    claim_2_id = res_2["claim_id"]

    # 3. check() confirms claim 2 is WAITING and blocked by claim 1
    chk_waiting = mcp_server.check(claim_2_id)
    assert chk_waiting["status"] == "waiting"
    assert chk_waiting["blocked_by"] == claim_1_id

    # 4. Agent 1 releases claim 1
    rel_res = mcp_server.release(claim_1_id)
    assert rel_res["status"] == "released"
    assert len(rel_res["unblocked_claims"]) == 1
    assert rel_res["unblocked_claims"][0]["claim_id"] == claim_2_id
    assert rel_res["unblocked_claims"][0]["new_status"] == "active"

    # 5. check() confirms claim 2 is now ACTIVE
    chk_active = mcp_server.check(claim_2_id)
    assert chk_active["status"] == "active"
    assert chk_active.get("blocked_by") is None


def test_mcp_check_non_existent_and_done(mcp_fixture):
    """Test check tool handling of missing claims and done claims."""
    # Non-existent
    chk_missing = mcp_server.check("claim_does_not_exist")
    assert chk_missing["status"] == "not_found"

    # Done claim
    res = mcp_server.claim("agent_1", "pkg/other.py::func_d", change_type="BODY")
    mcp_server.release(res["claim_id"])
    chk_done = mcp_server.check(res["claim_id"])
    assert chk_done["status"] == "done"


def test_mcp_multi_waiter_cascade_unblock(mcp_fixture):
    """Custom Test (Multi-Waiter Cascade):
    Agent 1 holds B.
    Agent 2 is blocked on A (calls B).
    Agent 3 is blocked on E (calls A).
    When Agent 1 releases B, all waiting agents along the chain are re-checked and promoted to ACTIVE.
    """
    # Agent 1 locks B
    c1 = mcp_server.claim("agent_1", "pkg/mod.py::func_b", change_type="SIGNATURE")

    # Agent 2 tries to edit A -> BLOCKED (WAITING)
    c2 = mcp_server.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert c2["verdict"] == "BLOCK"
    assert mcp_server.check(c2["claim_id"])["status"] == "waiting"

    # Agent 3 tries to edit E -> BLOCKED (WAITING, E calls A -> B)
    c3 = mcp_server.claim("agent_3", "pkg/mod.py::func_e", change_type="BODY")
    assert c3["verdict"] == "BLOCK"
    assert mcp_server.check(c3["claim_id"])["status"] == "waiting"

    # Agent 1 releases
    rel = mcp_server.release(c1["claim_id"])
    unblocked_ids = [u["claim_id"] for u in rel["unblocked_claims"]]
    assert c2["claim_id"] in unblocked_ids
    assert c3["claim_id"] in unblocked_ids

    # Both are now active
    assert mcp_server.check(c2["claim_id"])["status"] == "active"
    assert mcp_server.check(c3["claim_id"])["status"] == "active"
