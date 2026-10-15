"""tests/test_real_falkordb.py - Verifies connectivity and Cypher queries against a real FalkorDB instance.

Runs with strict=True so any connection failure or Cypher error is raised immediately.
If FalkorDB is offline, live tests are cleanly skipped with an explicit explanatory reason.
"""

import pytest
import time
from graph_db import GraphDB
from radar_core import ConflictRadar


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
        assert db.is_connected is True


@skip_if_no_falkordb
def test_real_falkordb_shortest_path_both_directions():
    """Live Cypher shortestPath execution in both directions with strict=True.
    Graph topology: A calls B, B calls C, D is unrelated.
    """
    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    # Setup symbols
    db.upsert_symbol("sym_a", "func_a", "a.py", "function")
    db.upsert_symbol("sym_b", "func_b", "b.py", "function")
    db.upsert_symbol("sym_c", "func_c", "c.py", "function")
    db.upsert_symbol("sym_d", "func_d", "d.py", "function")

    db.add_calls("sym_a", "sym_b")
    db.add_calls("sym_b", "sym_c")

    # 1. Forward direction 1 hop (A -> B)
    path_ab = db.find_call_path("sym_a", "sym_b", max_hops=3)
    assert path_ab == ["sym_a", "sym_b"]

    # 2. Reverse direction search (B queried against A still finds path)
    path_ba = db.find_call_path("sym_b", "sym_a", max_hops=3)
    assert path_ba == ["sym_a", "sym_b"]

    # 3. Two hops forward (A -> C) and reverse (C against A)
    path_ac = db.find_call_path("sym_a", "sym_c", max_hops=3)
    assert path_ac == ["sym_a", "sym_b", "sym_c"]

    path_ca = db.find_call_path("sym_c", "sym_a", max_hops=3)
    assert path_ca == ["sym_a", "sym_b", "sym_c"]

    # 4. Unrelated symbol D
    path_ad = db.find_call_path("sym_a", "sym_d", max_hops=3)
    assert path_ad is None

    # 5. Hop cutoff: A to C with max_hops=1 must return None
    path_cutoff = db.find_call_path("sym_a", "sym_c", max_hops=1)
    assert path_cutoff is None

    db.clear()


@skip_if_no_falkordb
def test_real_falkordb_collision_and_precedent_count():
    """Live Cypher Collision node and INVOLVED edge creation with precedent counting.
    Verifies order-independence and non-inflated counting.
    """
    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    # Seed symbols
    db.upsert_symbol("sym_a", "func_a", "a.py", "function")
    db.upsert_symbol("sym_b", "func_b", "b.py", "function")

    # Record first collision at t=100
    db.record_collision("sym_a", "sym_b", "BLOCK", "sym_a -> sym_b", agent_a="ag1", agent_b="ag2", now=100.0)

    # Precedent check at t=200 should find exactly 1
    p1 = db.get_precedent("sym_a", "sym_b", before_time=200.0)
    assert p1["count"] == 1
    assert p1["last_seen"] == 100.0

    # Order-independence: querying (B, A) should also find 1
    p1_rev = db.get_precedent("sym_b", "sym_a", before_time=200.0)
    assert p1_rev["count"] == 1
    assert p1_rev["last_seen"] == 100.0

    # Record second collision at t=150
    db.record_collision("sym_b", "sym_a", "WARN", "sym_a -> sym_b", agent_a="ag2", agent_b="ag1", now=150.0)

    # Precedent check at t=200 should find 2
    p2 = db.get_precedent("sym_a", "sym_b", before_time=200.0)
    assert p2["count"] == 2
    assert p2["last_seen"] == 150.0

    db.clear()


@skip_if_no_falkordb
def test_real_falkordb_expire_stale_claims():
    """Live Cypher claim expiry and edge cleanup with strict=True."""
    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    db.upsert_symbol("sym_a", "func_a", "a.py", "function")
    db.upsert_symbol("sym_b", "func_b", "b.py", "function")

    # Add active claim c1 and waiting claim c2 blocked by c1 at t=100
    c1 = db.add_claim("ag1", "sym_a", "SIGNATURE", status="active", now=100.0)
    c2 = db.add_claim("ag2", "sym_b", "BODY", status="waiting", now=100.0)
    db.add_blocked_by(c2, c1)

    # Active claims list at t=100
    active = db.get_active_claims()
    assert len(active) == 1
    assert active[0]["id"] == c1

    # Expire claims with cutoff t=150
    expired = db.expire_stale_claims(cutoff=150.0)
    expired_ids = [c["id"] for c in expired]
    assert c1 in expired_ids
    assert c2 in expired_ids

    # Active claims should now be empty
    assert len(db.get_active_claims()) == 0
    assert len(db.get_waiting_claims()) == 0

    db.clear()


@skip_if_no_falkordb
def test_real_falkordb_blocked_by_promotion_on_release():
    """Live Cypher BLOCKED_BY edge deletion and status promotion to active when blocker releases."""
    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    # Setup topology: A calls B
    db.upsert_symbol("sym_a", "func_a", "a.py", "function")
    db.upsert_symbol("sym_b", "func_b", "b.py", "function")
    db.add_calls("sym_a", "sym_b")

    radar = ConflictRadar(db=db, ttl_seconds=300)

    # 1. Agent 1 claims signature of B -> SAFE (ACTIVE)
    v1 = radar.claim("agent_1", "sym_b", "SIGNATURE")
    assert v1.verdict == "SAFE"

    # 2. Agent 2 claims body of A -> BLOCK (WAITING, BLOCKED_BY Agent 1)
    v2 = radar.claim("agent_2", "sym_a", "BODY")
    assert v2.verdict == "BLOCK"
    chk_wait = radar.check(v2.claim_id)
    assert chk_wait["status"] == "waiting"
    assert chk_wait["blocked_by"] == v1.claim_id

    # 3. Agent 1 releases B -> Agent 2 automatically unblocked to ACTIVE
    rel = radar.release(v1.claim_id)
    assert rel["status"] == "released"
    unblocked_ids = [u["claim_id"] for u in rel["unblocked_claims"]]
    assert v2.claim_id in unblocked_ids

    # 4. Check Agent 2 claim in real DB is now active and unblocked
    chk_active = radar.check(v2.claim_id)
    assert chk_active["status"] == "active"
    assert chk_active.get("blocked_by") is None

    db.clear()


@skip_if_no_falkordb
def test_real_falkordb_sse_stream_precedent_count():
    """Verify that the SSE event stream on real FalkorDB computes precedent_count == 2 on the 3rd collision."""
    import json
    import dashboard
    from fastapi.testclient import TestClient

    db = GraphDB(strict=True)
    db.connect()
    db.clear()

    # Create symbols
    db.upsert_symbol("sym_x", "func_x", "x.py", "function")
    db.upsert_symbol("sym_y", "func_y", "y.py", "function")
    db.add_calls("sym_x", "sym_y")

    # Record 3 collisions between sym_x and sym_y at distinct increasing timestamps
    t0 = 1000.0
    db.record_collision("sym_x", "sym_y", "WARN", "sym_x -> sym_y", "agent-1", "agent-2", now=t0)
    db.record_collision("sym_x", "sym_y", "BLOCK", "sym_x -> sym_y", "agent-1", "agent-2", now=t0 + 10)
    db.record_collision("sym_x", "sym_y", "BLOCK", "sym_x -> sym_y", "agent-1", "agent-2", now=t0 + 20)

    # Point dashboard db to this connected test db
    dashboard.db = db
    client = TestClient(dashboard.app)

    # Request events stream with limit=2 cycles (seed=false so all 3 will be emitted)
    with client.stream("GET", "/events?limit=2&seed=false") as response:
        collision_events = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if data.get("type") == "collision":
                    collision_events.append(data)

    assert len(collision_events) == 3
    # First collision has precedent 0 and reason does NOT contain 'seen'
    assert collision_events[0]["precedent_count"] == 0
    assert "seen" not in collision_events[0]["reason"].lower()
    # Second collision has precedent 1 and says 'seen 1 time before'
    assert collision_events[1]["precedent_count"] == 1
    assert "seen 1 time before" in collision_events[1]["reason"]
    # Third collision has precedent 2 and says 'seen 2 times before'
    assert collision_events[2]["precedent_count"] == 2
    assert "seen 2 times before" in collision_events[2]["reason"]

    db.clear()
