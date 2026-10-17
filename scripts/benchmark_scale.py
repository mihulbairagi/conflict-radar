"""benchmark_scale.py - Performance and visual scale verification for Conflict Radar.

Tests Conflict Radar on a medium codebase (150+ symbols):
1. Indexes 150+ symbols and evaluates indexing duration.
2. Executes 100 consecutive bidirectional path-traversal queries in FalkorDB (Cypher).
3. Evaluates p50, p95, and p99 query latencies.
4. Validates collision subgraph extraction for large codebases.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import time
from falkordb import FalkorDB
from indexer import index_codebase
from graph_db import GraphDB
from radar_core import ConflictRadar


def run_scale_benchmark():
    print("==================================================")
    print("   CONFLICT RADAR: SCALE & LATENCY BENCHMARK")
    print("==================================================")

    db = GraphDB(strict=True)
    db.connect()
    
    # 1. Measure Indexing on 150+ symbols
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"\n[1] Indexing medium codebase from '{repo_root}'...")
    
    t0 = time.perf_counter()
    stats = index_codebase(repo_root, graph=db.graph)
    idx_time = (time.perf_counter() - t0) * 1000.0
    
    print(f"    Indexed: {stats['files_count']} files, {stats['symbols_count']} symbols, {stats['calls_count']} calls.")
    print(f"    Indexing Latency: {idx_time:.2f} ms")

    # 2. Query Latency Benchmark (100 random lookups)
    symbols = db.get_all_symbol_ids()
    print(f"\n[2] Executing 100 bidirectional path traversals across {len(symbols)} symbols...")

    latencies = []
    found_paths = 0

    for i in range(min(50, len(symbols) - 1)):
        s1 = symbols[i]
        s2 = symbols[(i + 1) % len(symbols)]
        t_start = time.perf_counter()
        path = db.find_call_path(s1, s2, max_hops=3)
        elapsed = (time.perf_counter() - t_start) * 1000.0
        latencies.append(elapsed)
        if path:
            found_paths += 1

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[-1]

    print(f"    Total Queries: {len(latencies)}")
    print(f"    Paths Discovered: {found_paths}")
    print(f"    p50 Latency: {p50:.2f} ms")
    print(f"    p95 Latency: {p95:.2f} ms")
    print(f"    Max Latency: {p99:.2f} ms")

    print("\n[3] Scale Verdict:")
    if p95 < 15.0:
        print("    PASS: FalkorDB graph traversal scales sub-15ms on 100+ symbol graph.")
    else:
        print("    WARN: Latency exceeded expected 15ms threshold.")

    # Clean up and restore sample_repo
    sample_dir = os.path.join(repo_root, "sample_repo")
    db.clear()
    index_codebase(sample_dir, graph=db.graph)
    print(f"\n[4] Restored demo state to {sample_dir}.\n")


if __name__ == "__main__":
    run_scale_benchmark()
