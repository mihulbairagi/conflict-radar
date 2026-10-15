"""tests/test_real_falkordb.py - Verifies connectivity and Cypher queries against a real FalkorDB instance.

Runs with strict=True so any connection failure or Cypher error is raised immediately.
If FalkorDB is offline, live tests are cleanly skipped with an explicit explanatory reason.
"""

import pytest
from graph_db import GraphDB


def is_falkordb_available() -> bool:
    """Check whether a real FalkorDB instance is running on localhost:6379."""
    db = GraphDB(strict=True)
    try:
        db.connect()
        # Verify basic query
        db.graph.query("RETURN 1")
        return True
    except Exception:
        return False


FALKORDB_AVAILABLE = is_falkordb_available()
skip_if_no_falkordb = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="Real FalkorDB instance is offline (localhost:6379 unreachable).",
)


def test_falkordb_connection_status():
    """Reports connection status honestly.
    If FalkorDB is offline, tests that strict=True properly raises ConnectionError.
    If FalkorDB is online, verifies that connection succeeds and strict=True stays active.
    """
    db = GraphDB(strict=True)
    if not FALKORDB_AVAILABLE:
        with pytest.raises(ConnectionError) as exc_info:
            db.connect()
        assert "Failed to connect to FalkorDB" in str(exc_info.value)
    else:
        db.connect()
        assert db._connected is True


@skip_if_no_falkordb
def test_real_falkordb_cypher_roundtrip():
    """Live Cypher execution against real FalkorDB with strict=True.
    Verifies upserts, MERGE behavior, and shortestPath traversal.
    """
    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    # 1. Upsert symbols
    db.upsert_symbol("real_a", "func_a", "a.py", "function")
    db.upsert_symbol("real_b", "func_b", "b.py", "function")
    db.add_calls("real_a", "real_b")

    # 2. Check path
    path = db.find_call_path("real_a", "real_b", max_hops=3)
    assert path == ["real_a", "real_b"]

    # 3. Add claims and precedent
    cid = db.add_claim("agent_1", "real_b", "SIGNATURE", status="active")
    assert cid is not None

    db.record_collision("real_a", "real_b", "BLOCK", "real_a -> real_b")
    prec = db.get_precedent("real_a", "real_b")
    assert prec["count"] == 1

    # Cleanup
    db.clear()
