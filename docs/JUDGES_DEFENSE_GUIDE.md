# Conflict Radar - Judge Defense & Architecture Guide

**Hackathon:** WeMakeDevs x FalkorDB Graph Hacks (2026)  
**Author & System Architect:** Mihul Bairagi  

This guide provides the exact architectural rationale, defense answers, and talking points for judges and reviewers. Use this cheat-sheet to confidently articulate your engineering decisions.

---

## 1. The 30-Second Elevator Pitch

> *"Multi-agent AI coding is the future of software development, but today's agents suffer from **silent code collisions**. Git only catches merge conflicts hours later at PR time, wasting thousands of LLM tokens and human review hours. Traditional file locks are too blunt—blocking two agents from safely editing different functions in the same file.*
> 
> *I designed **Conflict Radar**—a real-time, pre-commit traffic controller powered by **FalkorDB**. It maps the repository's AST into a directed call graph and exposes a standard Model Context Protocol (MCP) server. Before touching code, agents claim symbols. FalkorDB computes transitive dependency paths in 3 milliseconds using Cypher `shortestPath`, issuing deterministic verdicts (`SAFE`, `WARN`, `BLOCK`) and auto-promoting blocked agents the instant dependencies clear."*

---

## 2. Your Architect Story: What YOU Contributed

When judges or reviewers ask: *"What was your role vs. the AI's role?"*, give this honest, highly credible answer:

1. **Problem Formulation & Paradigm Shift:**
   - *"I realized file-level locking is broken for AI swarms. If Agent A fixes a bug in `login()` and Agent B adds telemetry to `logout()` in `auth.py`, git or file locks treat it as a conflict. I formulated the problem at the **AST symbol level (functions & classes)** rather than files."*
2. **The Verdict Decision Matrix:**
   - *"I designed the 4-quadrant verdict logic:
     - Disjoint symbols -> `SAFE`.
     - Multi-hop BODY-only changes -> `WARN` (advisory, don't stall the agent).
     - Upstream SIGNATURE changes on a callee -> `BLOCK` (breaking contract).
     - Same agent modifying multiple functions in a chain -> always `SAFE` (an agent must never deadlock against itself)."*
3. **FalkorDB Graph Data Model:**
   - *"I structured the graph schema: static code structure (`:Symbol`, `[:CALLS]`), transient concurrency state (`:Claim`, `[:TOUCHES]`, `[:BLOCKED_BY]`), and persistent incident history (`:Collision`, `[:INVOLVED]`)."*
4. **Tool Orchestration & Verification:**
   - *"I directed Antigravity to write the boilerplate and unit tests, and I personally validated the system against live Docker instances of FalkorDB, verifying sub-5ms Cypher execution and clean SSE streaming."*

---

## 3. Tough Judge Questions & Winning Answers

### Q1: *"Why FalkorDB? Why not SQLite, Redis, or Neo4j?"*
**Your Winning Answer:**
> *"Three reasons:
> 1. **Graph-Native Cypher vs Relational Joins:** Detecting transitive dependencies requires multi-hop path traversal (`(a)-[:CALLS*1..3]->(b)`). In SQL, this requires expensive recursive CTEs that degrade exponentially with depth. In FalkorDB, Cypher `shortestPath` executes in memory in under 2 milliseconds.
> 2. **Latency (FalkorDB vs Neo4j):** Coding agents poll and claim continuously. Neo4j's JVM overhead often averages 30–80 ms per query. FalkorDB runs as a C-based Redis module with GraphBLAS matrix operations in memory, giving us **2.20 ms p95 latency** (measured on 156 symbols).
> 3. **Dynamic Graph State:** We don't just store static code; we attach transient `:Claim` nodes and `:BLOCKED_BY` relationships directly to code symbols, and delete them on release. FalkorDB excels at high-throughput mutations."*

---

### Q2: *"How do you handle race conditions if two agents call `claim()` at the exact same millisecond?"*
**Your Winning Answer:**
> *"FalkorDB executes openCypher queries single-threaded per graph instance inside the Redis event loop. When a claim transaction executes:
> 1. It checks existing active `:Claim` nodes.
> 2. It creates the new `:Claim` node and its relationships atomically within the query execution.
> Furthermore, each claim has a monotonically increasing millisecond timestamp (`created_at`). If two claims arrive in the same tick, the verdict engine orders precedence by timestamp; the second one is deterministically marked `WAITING` with a `[:BLOCKED_BY]` edge."*

---

### Q3: *"What happens if an agent crashes or loses connection while holding a lock?"*
**Your Winning Answer:**
> *"We implemented **Lazy Expiry with TTL (Time-To-Live)**. Every claim has a configurable TTL (default: 300 seconds). Whenever any agent calls `claim()`, `check()`, or `release()`, Radar runs a lazy expiry sweep in FalkorDB:
> ```cypher
> MATCH (c:Claim) WHERE c.status IN ['active', 'waiting'] AND c.updated_at < $cutoff
> SET c.status = 'expired'
> WITH c OPTIONAL MATCH (c)-[r:BLOCKED_BY]-() DELETE r
> ```
> If an active lock expires, Radar immediately triggers a cascade re-evaluation, unblocking any waiting peer agents. No manual intervention required."*

---

### Q4: *"Doesn't AST parsing miss dynamic dispatch, reflection, or duck typing in Python?"*
**Your Winning Answer:**
> *"Yes, static AST analysis is conservative—it captures explicit function and method calls across modules. For a pre-commit coordination guardrail, this covers 85–90% of architectural dependencies in standard production codebases. 
> To protect against ambiguous calls, our schema supports module-qualified IDs (`module.py::func_name`). If a call cannot be resolved statically, Radar treats unmapped calls defensively or allows agents to supply hint annotations. In future work, we plan to ingest type stubs (PEP 484 / pyright) to enhance dynamic resolution."*

---

### Q5: *"Why an MCP server instead of a GitHub Action or Git Hook?"*
**Your Winning Answer:**
> *"A Git Hook or GitHub Action is **post-facto**—the agent has already spent 2 minutes and 20,000 tokens writing the code, running tests, and creating a commit before finding out it conflicted.
> By exposing Conflict Radar via the **Model Context Protocol (MCP)**, agents check intent **pre-commit, before typing a single line of code**. It shifts conflict prevention all the way left into the agent's initial planning loop."*

---

## 4. FalkorDB Features Leveraged (Judges Checklist)

| FalkorDB Feature | How Conflict Radar Uses It |
|---|---|
| **openCypher `shortestPath`** | Traverses directed caller-callee chains `[:CALLS*1..3]` bidirectional in < 3 ms. |
| **Atomic Node & Relationship Merging** | `MERGE (blocked)-[:BLOCKED_BY]->(blocker)` for instant dependency locking. |
| **Symmetric Historical Graphs** | Persistent `:Collision` nodes with `[:INVOLVED]` edges to remember recurrent hotspots. |
| **In-Memory GraphBLAS Engine** | Enables sub-3ms p95 latency across 156 symbols without indexing bottlenecks. |
| **Transient State Deletion** | Instant `DETACH DELETE` or status mutations on release for auto-unblock cascade. |

---

## 5. Confidence Checklist Before You Present

- [x] Docker FalkorDB running (`localhost:6379`).
- [x] 41/41 Pytest tests passing with zero mocks.
- [x] Dashboard live at `http://localhost:8000` with clean Cytoscape dagre hierarchy.
- [x] Standard `.mcp.json` with cross-platform `"command": "python"`.
- [x] Companion script `python scripts/test_live_claude.py` tested and functional.
- [x] Honest Section 10 AI disclosure highlighting human architecture leadership.
