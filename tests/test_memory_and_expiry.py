"""tests/test_memory_and_expiry.py - Unit tests for Step 5: Collision Memory and Claim Expiry.

Tests cover:
1. Same collision twice: second verdict cites 1 earlier collision with timestamp.
2. Order-independence: collision (A, B) then check (B, A) cites 1 earlier collision.
3. Expired claim unblocks waiting claim on next call (lazy expiry using mock clock).
4. Claim under TTL does not expire.
5. Claude's cascade test: Two waiting claims that conflict with each other (both SIGNATURE)
   on blocker release: exactly one becomes ACTIVE, and the other remains WAITING.
"""

import time
import pytest
from graph_db import GraphDB
from radar_core import ConflictRadar


class MockClock:
    """Controllable mock clock for deterministic time manipulation in tests."""
    def __init__(self, initial_time: float = 1700000000.0):
        self._time = initial_time

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


@pytest.fixture
def fresh_graph():
    """Setup graph fixture with call chain:
    sym_e -> sym_a -> sym_b -> sym_c, and sym_d unrelated.
    """
    db = GraphDB()
    db.connect()
    db.clear()

    # Seed symbols and relations
    db.upsert_symbol("sym_a", "func_a", "file_a.py", "function")
    db.upsert_symbol("sym_b", "func_b", "file_b.py", "function")
    db.upsert_symbol("sym_c", "func_c", "file_c.py", "function")
    db.upsert_symbol("sym_d", "func_d", "file_d.py", "function")
    db.upsert_symbol("sym_e", "func_e", "file_e.py", "function")

    db.add_calls("sym_e", "sym_a")
    db.add_calls("sym_a", "sym_b")
    db.add_calls("sym_b", "sym_c")

    return db


def test_collision_memory_precedent(fresh_graph):
    """Case 1: Same collision occurs twice.
    First time: precedent is 0 (current collision is not counted yet).
    Second time: precedent is 1 with earlier collision timestamp in verdict and reason.
    """
    clock = MockClock(1700000000.0)
    radar = ConflictRadar(db=fresh_graph, ttl_seconds=300, clock=clock)

    # Agent 1 claims B (SIGNATURE)
    v1 = radar.claim("agent_1", "sym_b", "SIGNATURE")
    assert v1.verdict == "SAFE"

    # Agent 2 claims A (BODY) -> Call path: sym_a -> sym_b (1 hop). Since B is SIGNATURE, verdict is BLOCK
    v2 = radar.claim("agent_2", "sym_a", "BODY")
    assert v2.verdict == "BLOCK"
    assert v2.precedent_count == 0
    assert "collided" not in v2.reason

    # Agent 1 releases B (which unblocks v2 to active)
    radar.release(v1.claim_id)
    # Agent 2 finishes work on A and releases A
    radar.release(v2.claim_id)

    # Advance clock by 100 seconds
    clock.advance(100)

    # Now Agent 3 claims B (SIGNATURE) again cleanly
    v3 = radar.claim("agent_3", "sym_b", "SIGNATURE")
    assert v3.verdict == "SAFE"

    # Agent 4 claims A (BODY) -> Conflicts again with B
    v4 = radar.claim("agent_4", "sym_a", "BODY")
    assert v4.verdict == "BLOCK"
    assert v4.precedent_count == 1
    assert v4.last_collision_time is not None
    assert "collided 1 times before" in v4.reason


def test_collision_memory_order_independence(fresh_graph):
    """Case 2: Order-independence.
    Collision between (sym_a, sym_b) and later collision between (sym_b, sym_a).
    Precedent count must recognize them as identical symbol pairs.
    """
    clock = MockClock(1700000000.0)
    radar = ConflictRadar(db=fresh_graph, ttl_seconds=300, clock=clock)

    # Collision 1: Agent 1 claims A (SIGNATURE), Agent 2 claims B (BODY)
    v1 = radar.claim("agent_1", "sym_a", "SIGNATURE")
    v2 = radar.claim("agent_2", "sym_b", "BODY")
    assert v2.verdict == "BLOCK"
    assert v2.precedent_count == 0

    radar.release(v1.claim_id)
    radar.release(v2.claim_id)

    clock.advance(50)

    # Collision 2: Reversed order! Agent 3 claims B (BODY), Agent 4 claims A (SIGNATURE)
    v3 = radar.claim("agent_3", "sym_b", "BODY")
    v4 = radar.claim("agent_4", "sym_a", "SIGNATURE")
    assert v4.verdict == "BLOCK"
    assert v4.precedent_count == 1
    assert "collided 1 times before" in v4.reason


def test_claim_under_ttl_does_not_expire(fresh_graph):
    """Case 3: A claim queried well under its TTL (e.g. 50s into 300s TTL) remains ACTIVE."""
    clock = MockClock(1700000000.0)
    radar = ConflictRadar(db=fresh_graph, ttl_seconds=300, clock=clock)

    v1 = radar.claim("agent_1", "sym_a", "SIGNATURE")
    assert v1.verdict == "SAFE"

    clock.advance(50)  # 50s < 300s TTL

    status = radar.check(v1.claim_id)
    assert status["status"] == "active"


def test_lazy_expiry_unblocks_waiting_claim(fresh_graph):
    """Case 4: Lazy claim expiry.
    Agent 1 locks sym_b (SIGNATURE) at t=0.
    Agent 2 attempts to lock sym_a (BODY) at t=100 -> BLOCK, enters WAITING status.
    Clock advances past Agent 1's TTL (total time 310s).
    On next call (e.g. check or claim), Agent 1's claim expires lazily,
    triggering re-evaluation that promotes Agent 2's waiting claim (age 210s < 300s TTL) to ACTIVE.
    """
    clock = MockClock(1700000000.0)
    radar = ConflictRadar(db=fresh_graph, ttl_seconds=300, clock=clock)

    # t = 0: Agent 1 claims B
    v1 = radar.claim("agent_1", "sym_b", "SIGNATURE")
    assert v1.verdict == "SAFE"

    # t = 100: Agent 2 arrives and claims A
    clock.advance(100)
    v2 = radar.claim("agent_2", "sym_a", "BODY")
    assert v2.verdict == "BLOCK"

    # Agent 2's claim should be waiting
    check_v2 = radar.check(v2.claim_id)
    assert check_v2["status"] == "waiting"
    assert check_v2["blocked_by"] == v1.claim_id

    # Time passes: advance 210s -> Total time = 310s.
    # Agent 1 age = 310s (> 300s TTL -> expired).
    # Agent 2 age = 210s (< 300s TTL -> valid waiting claim).
    clock.advance(210)

    # Agent 2 checks its status (triggers lazy expiry)
    check_v2_after = radar.check(v2.claim_id)

    # Agent 1 must now be EXPIRED, and Agent 2 must be promoted to ACTIVE!
    check_v1 = radar.check(v1.claim_id)
    assert check_v1["status"] == "expired"
    assert check_v2_after["status"] == "active"
    assert check_v2_after.get("blocked_by") is None


def test_cascade_conflicting_waiters(fresh_graph):
    """Case 5 (Claude's Cascade Test):
    Two waiting claims that conflict with each other (both SIGNATURE):
    - Agent 1 claims B (SIGNATURE) -> ACTIVE
    - Agent 2 claims A (SIGNATURE) -> BLOCKED by B -> WAITING
    - Agent 3 claims E (SIGNATURE) -> BLOCKED by B (via E->A->B) -> WAITING
      Note: E calls A! So Agent 3 and Agent 2 conflict directly with each other as well.
    When Agent 1 releases B:
    - Exactly ONE should become ACTIVE (e.g. Agent 2).
    - The other (Agent 3) must be evaluated against the newly active Agent 2,
      and therefore REMAIN WAITING (blocked by Agent 2)!
    """
    clock = MockClock(1700000000.0)
    radar = ConflictRadar(db=fresh_graph, ttl_seconds=300, clock=clock)

    # 1. Agent 1 locks B
    v1 = radar.claim("agent_1", "sym_b", "SIGNATURE")
    assert v1.verdict == "SAFE"

    # 2. Agent 2 locks A (A calls B) -> BLOCKED by Agent 1
    v2 = radar.claim("agent_2", "sym_a", "SIGNATURE")
    assert v2.verdict == "BLOCK"
    assert radar.check(v2.claim_id)["status"] == "waiting"

    # 3. Agent 3 locks E (E calls A, A calls B) -> BLOCKED by Agent 1
    v3 = radar.claim("agent_3", "sym_e", "SIGNATURE")
    assert v3.verdict == "BLOCK"
    assert radar.check(v3.claim_id)["status"] == "waiting"

    # 4. Agent 1 releases B
    rel_res = radar.release(v1.claim_id)

    # Re-evaluate statuses
    status_v2 = radar.check(v2.claim_id)
    status_v3 = radar.check(v3.claim_id)

    # Exactly one must be active, and the other must remain waiting
    statuses = [status_v2["status"], status_v3["status"]]
    assert "active" in statuses
    assert "waiting" in statuses

    if status_v2["status"] == "active":
        assert status_v3["status"] == "waiting"
        assert status_v3["blocked_by"] == v2.claim_id
    else:
        assert status_v2["status"] == "waiting"
        assert status_v2["blocked_by"] == v3.claim_id
