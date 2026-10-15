"""test_radar_core.py - Pytest suite for Step 3 Verdict Engine.

Tests deterministic verdict rules against the symbol fixture:
Chain: E -> A -> B -> C (3 hops from E to C)
Unrelated: D
Beyond limit: F -> E -> A -> B -> C (4 hops from F to C)
"""

import pytest
from graph_db import GraphDB
from radar_core import ConflictRadar, Verdict


@pytest.fixture
def radar_fixture():
    """Fixture providing an indexed graph and ConflictRadar engine.
    Topological call chains:
    - E calls A
    - A calls B
    - B calls C
    - D is unrelated
    - F calls E (making F to C a 4-hop chain)
    """
    db = GraphDB()
    db.connect()
    db.clear()

    # Files
    db.upsert_file("pkg/mod.py")
    db.upsert_file("pkg/other.py")

    # Symbols: A, B, C, E, F in mod.py; D in other.py
    symbols = ["func_a", "func_b", "func_c", "func_e", "func_f"]
    for s in symbols:
        sym_id = f"pkg/mod.py::{s}"
        db.upsert_symbol(sym_id, s, "pkg/mod.py", "function")
        db.add_contains("pkg/mod.py", sym_id)

    db.upsert_symbol("pkg/other.py::func_d", "func_d", "pkg/other.py", "function")
    db.add_contains("pkg/other.py", "pkg/other.py::func_d")

    # Calls: F -> E -> A -> B -> C
    db.add_calls("pkg/mod.py::func_f", "pkg/mod.py::func_e")
    db.add_calls("pkg/mod.py::func_e", "pkg/mod.py::func_a")
    db.add_calls("pkg/mod.py::func_a", "pkg/mod.py::func_b")
    db.add_calls("pkg/mod.py::func_b", "pkg/mod.py::func_c")

    radar = ConflictRadar(db=db, max_hops=3)
    yield radar

    db.clear()


def test_case_1_rename_vs_caller_block(radar_fixture):
    """Case 1: Agent 1 changes signature of B, Agent 2 changes body of A -> BLOCK with path."""
    radar = radar_fixture

    # Agent 1 claims signature change on B
    v1 = radar.claim("agent_1", "pkg/mod.py::func_b", change_type="SIGNATURE")
    assert v1.verdict == "SAFE"

    # Agent 2 claims body change on caller A (A calls B)
    v2 = radar.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert v2.verdict == "BLOCK"
    assert v2.hops == 1
    assert v2.path == ["pkg/mod.py::func_a", "pkg/mod.py::func_b"]
    assert "agent_1" in v2.reason
    assert "agent_2" in v2.reason


def test_case_2_unrelated_symbols_safe(radar_fixture):
    """Case 2: Agent 1 edits D, Agent 2 edits A -> SAFE."""
    radar = radar_fixture

    v1 = radar.claim("agent_1", "pkg/other.py::func_d", change_type="BODY")
    assert v1.verdict == "SAFE"

    v2 = radar.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert v2.verdict == "SAFE"
    assert v2.hops == 0
    assert v2.path == []
    assert v2.conflicting_claim_id is None


def test_case_3_body_edits_multi_hop_warn(radar_fixture):
    """Case 3: Agent 1 changes body of C, Agent 2 changes body of A (2 hops apart) -> WARN."""
    radar = radar_fixture

    v1 = radar.claim("agent_1", "pkg/mod.py::func_c", change_type="BODY")
    assert v1.verdict == "SAFE"

    # A -> B -> C (2 hops)
    v2 = radar.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert v2.verdict == "WARN"
    assert v2.hops == 2
    assert v2.path == ["pkg/mod.py::func_a", "pkg/mod.py::func_b", "pkg/mod.py::func_c"]
    assert "agent_1" in v2.reason
    assert "agent_2" in v2.reason


def test_case_4_path_beyond_max_hops_safe(radar_fixture):
    """Case 4: Path of 4 hops (F -> E -> A -> B -> C) is beyond 3-hop limit -> SAFE."""
    radar = radar_fixture

    # Agent 1 claims C (signature change)
    v1 = radar.claim("agent_1", "pkg/mod.py::func_c", change_type="SIGNATURE")
    assert v1.verdict == "SAFE"

    # Agent 2 claims F (F to C is 4 hops: F -> E -> A -> B -> C)
    v2 = radar.claim("agent_2", "pkg/mod.py::func_f", change_type="BODY")
    assert v2.verdict == "SAFE"
    assert v2.hops == 0


def test_case_5_same_agent_never_blocks_itself(radar_fixture):
    """Case 5: Same agent claims two related symbols -> SAFE (agent never blocks itself)."""
    radar = radar_fixture

    # Agent 1 claims A (signature)
    v1 = radar.claim("agent_1", "pkg/mod.py::func_a", change_type="SIGNATURE")
    assert v1.verdict == "SAFE"

    # Agent 1 also claims B (body), which is called by A
    v2 = radar.claim("agent_1", "pkg/mod.py::func_b", change_type="BODY")
    assert v2.verdict == "SAFE"


def test_case_6_identical_symbol_direct_collision(radar_fixture):
    """Case 6 [Extra Case]: Identical symbol collision (0 hops).
    - Both edit BODY -> WARN (shared function coordination)
    - One edits SIGNATURE -> BLOCK (conflicting concurrent signature rewrite)
    """
    radar = radar_fixture

    # Sub-case A: Both BODY -> WARN
    v1 = radar.claim("agent_1", "pkg/mod.py::func_a", change_type="BODY")
    v2 = radar.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert v2.verdict == "WARN"
    assert v2.hops == 0
    assert v2.path == ["pkg/mod.py::func_a"]

    # Release locks
    radar.release(v1.claim_id)
    radar.release(v2.claim_id)

    # Sub-case B: One SIGNATURE -> BLOCK
    v3 = radar.claim("agent_1", "pkg/mod.py::func_a", change_type="SIGNATURE")
    v4 = radar.claim("agent_2", "pkg/mod.py::func_a", change_type="BODY")
    assert v4.verdict == "BLOCK"
    assert v4.hops == 0
    assert v4.path == ["pkg/mod.py::func_a"]
