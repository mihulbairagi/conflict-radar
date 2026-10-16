"""tests/test_concurrency_race.py - Concurrency and Race Condition Verification.

Tests multi-process simultaneous claim registration on conflicting symbols
using a multiprocessing.Barrier against real FalkorDB (strict=True).
Verifies that Redis distributed locking eliminates race conditions across 30 iterations:
100% of iterations produce exactly 1 SAFE and 1 BLOCK, never double-SAFE.
"""

import multiprocessing
import pytest
from graph_db import GraphDB
from radar_core import ConflictRadar
from tests.test_real_falkordb import FALKORDB_AVAILABLE, skip_if_no_falkordb


def _worker_claim(barrier, agent_id: str, symbol_id: str, change_type: str, out_queue: multiprocessing.Queue):
    """Worker process: connects fresh to real FalkorDB, waits at barrier, and claims."""
    try:
        db = GraphDB(strict=True)
        db.connect()
        radar = ConflictRadar(db)

        # Synchronize exact execution start time across processes
        barrier.wait(timeout=5.0)

        verdict = radar.claim(agent_id, symbol_id, change_type)
        out_queue.put((agent_id, verdict.verdict, verdict.claim_id, verdict.conflicting_claim_id, None))
    except Exception as e:
        out_queue.put((agent_id, "ERROR", None, None, str(e)))


@skip_if_no_falkordb
def test_multiprocess_concurrent_claim_race_barrier_30_iterations():
    """Verify that 2 concurrent processes executing conflicting SIGNATURE claims

    at the exact same microsecond never result in double-SAFE.
    Over 30 iterations, exactly 1 must receive SAFE and 1 must receive BLOCK (100%).
    """
    db = GraphDB(strict=True)
    db.connect()

    # Seed the call chain: handler.py::parse_request -> validator.py::validate_input -> utils.py::normalize
    db.clear()
    db.upsert_symbol("utils.py::normalize", "normalize", "utils.py", "function")
    db.upsert_symbol("validator.py::validate_input", "validate_input", "validator.py", "function")
    db.upsert_symbol("handler.py::parse_request", "parse_request", "handler.py", "function")
    db.add_calls("handler.py::parse_request", "validator.py::validate_input")
    db.add_calls("validator.py::validate_input", "utils.py::normalize")

    iterations = 30
    results_summary = []

    for i in range(iterations):
        # Reset transient claims and collisions
        db.reset_demo_state()

        barrier = multiprocessing.Barrier(2)
        out_q = multiprocessing.Queue()

        p1 = multiprocessing.Process(
            target=_worker_claim,
            args=(barrier, "agent-1", "utils.py::normalize", "SIGNATURE", out_q)
        )
        p2 = multiprocessing.Process(
            target=_worker_claim,
            args=(barrier, "agent-2", "handler.py::parse_request", "SIGNATURE", out_q)
        )

        p1.start()
        p2.start()

        res1 = out_q.get(timeout=6.0)
        res2 = out_q.get(timeout=6.0)

        p1.join(timeout=3.0)
        p2.join(timeout=3.0)

        # Check for unexpected worker errors
        assert res1[4] is None, f"Worker 1 exception in iteration {i}: {res1[4]}"
        assert res2[4] is None, f"Worker 2 exception in iteration {i}: {res2[4]}"

        verdicts = sorted([res1[1], res2[1]])
        results_summary.append(verdicts)

        assert verdicts == ["BLOCK", "SAFE"], (
            f"Race condition detected in iteration {i + 1}/{iterations}! "
            f"Expected ['BLOCK', 'SAFE'], got {verdicts}. "
            f"Agent 1: {res1}, Agent 2: {res2}"
        )

    # 100% success verification
    safe_count = sum(1 for v in results_summary if "SAFE" in v)
    block_count = sum(1 for v in results_summary if "BLOCK" in v)
    double_safe_count = sum(1 for v in results_summary if v == ["SAFE", "SAFE"])

    assert len(results_summary) == iterations
    assert safe_count == iterations
    assert block_count == iterations
    assert double_safe_count == 0
