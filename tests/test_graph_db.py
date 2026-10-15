"""test_graph_db.py - Pytest suite for Conflict Radar Graph Layer (Step 2).

Tests graph operations against the fixture of four symbols:
A calls B, B calls C, D is unrelated.
"""

import pytest
from graph_db import GraphDB


@pytest.fixture
def graph_fixture():
    """Fixture providing a clean graph with four symbols:
    - A calls B
    - B calls C
    - D is unrelated
    """
    db = GraphDB(strict=False)
    # Connect if daemon is running, otherwise operates on in-memory fallback
    db.connect()
    db.clear()

    # Files
    db.upsert_file("pkg/mod.py")
    db.upsert_file("pkg/other.py")

    # Symbols: A, B, C in mod.py; D in other.py
    db.upsert_symbol("pkg/mod.py::func_a", "func_a", "pkg/mod.py", "function")
    db.upsert_symbol("pkg/mod.py::func_b", "func_b", "pkg/mod.py", "function")
    db.upsert_symbol("pkg/mod.py::func_c", "func_c", "pkg/mod.py", "function")
    db.upsert_symbol("pkg/other.py::func_d", "func_d", "pkg/other.py", "function")

    # Contains
    db.add_contains("pkg/mod.py", "pkg/mod.py::func_a")
    db.add_contains("pkg/mod.py", "pkg/mod.py::func_b")
    db.add_contains("pkg/mod.py", "pkg/mod.py::func_c")
    db.add_contains("pkg/other.py", "pkg/other.py::func_d")

    # Calls: A -> B -> C
    db.add_calls("pkg/mod.py::func_a", "pkg/mod.py::func_b")
    db.add_calls("pkg/mod.py::func_b", "pkg/mod.py::func_c")

    yield db

    db.clear()


def test_find_call_path_forward(graph_fixture):
    """Test 1-hop and 2-hop forward traversals."""
    db = graph_fixture
    # 1 hop: A -> B
    path_ab = db.find_call_path("pkg/mod.py::func_a", "pkg/mod.py::func_b", max_hops=3)
    assert path_ab == ["pkg/mod.py::func_a", "pkg/mod.py::func_b"]

    # 2 hops: A -> B -> C
    path_ac = db.find_call_path("pkg/mod.py::func_a", "pkg/mod.py::func_c", max_hops=3)
    assert path_ac == ["pkg/mod.py::func_a", "pkg/mod.py::func_b", "pkg/mod.py::func_c"]


def test_find_call_path_reverse_search(graph_fixture):
    """Test that find_call_path searches both directions."""
    db = graph_fixture
    # Querying (C, A) should find the dependency path connecting them
    path_ca = db.find_call_path("pkg/mod.py::func_c", "pkg/mod.py::func_a", max_hops=3)
    assert path_ca == ["pkg/mod.py::func_a", "pkg/mod.py::func_b", "pkg/mod.py::func_c"]


def test_find_call_path_unrelated(graph_fixture):
    """Test that unrelated symbol D returns None."""
    db = graph_fixture
    path_ad = db.find_call_path("pkg/mod.py::func_a", "pkg/other.py::func_d", max_hops=3)
    assert path_ad is None


def test_max_hops_cutoff(graph_fixture):
    """Test that max_hops cuts off paths beyond the hop limit."""
    db = graph_fixture
    # A -> B -> C is 2 hops. With max_hops=1, it must return None
    path_cutoff = db.find_call_path("pkg/mod.py::func_a", "pkg/mod.py::func_c", max_hops=1)
    assert path_cutoff is None


def test_claims_lifecycle(graph_fixture):
    """Test adding, listing, and releasing claims."""
    db = graph_fixture
    claim_id = db.add_claim("agent-alpha", "pkg/mod.py::func_a", "signature")

    active = db.get_active_claims()
    assert len(active) == 1
    assert active[0]["id"] == claim_id
    assert active[0]["agent_id"] == "agent-alpha"

    # Release
    db.release_claim(claim_id)
    active_after = db.get_active_claims()
    assert len(active_after) == 0


def test_precedent_recording(graph_fixture):
    """Test recording collisions and fetching memory precedent."""
    db = graph_fixture
    sym_a = "pkg/mod.py::func_a"
    sym_c = "pkg/mod.py::func_c"

    # Initial precedent should be 0
    p0 = db.get_precedent(sym_a, sym_c)
    assert p0["count"] == 0

    # Record first collision
    db.record_collision(sym_a, sym_c, "BLOCK", f"{sym_a} -> {sym_c}")
    p1 = db.get_precedent(sym_a, sym_c)
    assert p1["count"] == 1

    # Record second collision
    db.record_collision(sym_a, sym_c, "BLOCK", f"{sym_a} -> {sym_c}")
    p2 = db.get_precedent(sym_a, sym_c)
    assert p2["count"] == 2


def test_strict_mode_unreachable():
    """Verify strict mode raises ConnectionError when DB is unreachable."""
    db = GraphDB(host="127.0.0.1", port=9999, strict=True)
    with pytest.raises(ConnectionError) as exc_info:
        db.connect()
    assert "Failed to connect to FalkorDB" in str(exc_info.value)
