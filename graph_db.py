"""graph_db.py - FalkorDB Graph Layer and Cypher Query Engine for Conflict Radar.

Wraps the falkordb Python client to manage codebase graph topology,
claims, call-chain traversals, and precedent memory.
"""

import os
import time
import uuid
import threading
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Tuple
from falkordb import FalkorDB

# Thread-local storage for distributed lock re-entrancy
_lock_tls = threading.local()

# Redis Lua script for safe atomic lock release (releases only if held token matches)
LUA_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# ==============================================================================
# Cypher Query Constants (with explanatory comments)
# ==============================================================================

# Idempotently inserts or matches a File node by its path.
UPSERT_FILE_QUERY = """
MERGE (f:File {path: $path})
RETURN f
"""

# Idempotently inserts or matches a Symbol (function or class) node.
# Updates properties on match to reflect current file or kind.
UPSERT_SYMBOL_QUERY = """
MERGE (s:Symbol {id: $id})
ON CREATE SET s.name = $name, s.file = $file, s.kind = $kind
ON MATCH SET s.name = $name, s.file = $file, s.kind = $kind
RETURN s
"""

# Creates a CONTAINS relationship between a File and a Symbol inside it.
ADD_CONTAINS_QUERY = """
MATCH (f:File {path: $file_path}), (s:Symbol {id: $sym_id})
MERGE (f)-[r:CONTAINS]->(s)
RETURN r
"""

# Creates a directed CALLS relationship from caller Symbol to callee Symbol.
ADD_CALLS_QUERY = """
MATCH (caller:Symbol {id: $caller_id}), (callee:Symbol {id: $callee_id})
MERGE (caller)-[r:CALLS]->(callee)
RETURN r
"""

# Creates a Claim node with status ('active' or 'waiting'), linked to the Agent
# that made it and the Symbol it touches.
ADD_CLAIM_QUERY = """
MERGE (a:Agent {id: $agent_id, name: $agent_id})
MERGE (s:Symbol {id: $symbol_id})
CREATE (c:Claim {
    id: $claim_id,
    agent_id: $agent_id,
    symbol_id: $symbol_id,
    change_type: $change_type,
    status: $status,
    created_at: $created_at,
    updated_at: $updated_at
})
CREATE (a)-[:MADE]->(c)
CREATE (c)-[:TOUCHES]->(s)
RETURN c
"""

# Connects a waiting Claim to the active Claim that blocks it.
ADD_BLOCKED_BY_QUERY = """
MATCH (blocked:Claim {id: $blocked_id}), (blocker:Claim {id: $blocker_id})
MERGE (blocked)-[r:BLOCKED_BY]->(blocker)
RETURN r
"""

# Removes BLOCKED_BY relationship once a claim is unblocked.
REMOVE_BLOCKED_BY_QUERY = """
MATCH (c:Claim {id: $claim_id})-[r:BLOCKED_BY]->()
DELETE r
"""

# Updates the status of an existing claim ('active', 'waiting', 'done', 'expired') and refreshes updated_at.
UPDATE_CLAIM_STATUS_QUERY = """
MATCH (c:Claim {id: $claim_id})
SET c.status = $status, c.updated_at = $updated_at
RETURN c
"""

# Marks claims older than cutoff TTL as expired and deletes associated BLOCKED_BY edges.
EXPIRE_CLAIMS_QUERY = """
MATCH (c:Claim)
WHERE c.status IN ['active', 'waiting'] AND c.updated_at < $cutoff
SET c.status = 'expired'
WITH c
OPTIONAL MATCH (c)-[r:BLOCKED_BY]-()
DELETE r
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id, c.change_type AS change_type
"""

# Retrieves a single claim by its ID.
GET_CLAIM_BY_ID_QUERY = """
MATCH (c:Claim {id: $claim_id})
OPTIONAL MATCH (c)-[:BLOCKED_BY]->(blocker:Claim)
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status, c.created_at AS created_at,
       blocker.id AS blocker_id, c.updated_at AS updated_at
"""

# Marks a Claim as 'done' when an agent releases its modification lock.
RELEASE_CLAIM_QUERY = """
MATCH (c:Claim {id: $claim_id})
SET c.status = 'done', c.updated_at = $updated_at
RETURN c
"""

# Retrieves all currently active claims across the entire codebase.
GET_ACTIVE_CLAIMS_QUERY = """
MATCH (c:Claim {status: 'active'})
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status, c.created_at AS created_at,
       c.updated_at AS updated_at
"""

# Retrieves all waiting claims and their blocking claim ID.
GET_WAITING_CLAIMS_QUERY = """
MATCH (c:Claim {status: 'waiting'})
OPTIONAL MATCH (c)-[:BLOCKED_BY]->(blocker:Claim)
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status, c.created_at AS created_at,
       blocker.id AS blocker_id, c.updated_at AS updated_at
"""

# Records a collision event between two symbols for historical precedent,
# storing verdict, path text, timestamp, and both agent IDs.
RECORD_COLLISION_QUERY = """
CREATE (col:Collision {
    id: $collision_id,
    verdict: $verdict,
    path_text: $path_text,
    timestamp: $timestamp,
    agent_a: $agent_a,
    agent_b: $agent_b,
    sym_a: $sym_a,
    sym_b: $sym_b
})
WITH col
MATCH (a:Symbol {id: $sym_a}), (b:Symbol {id: $sym_b})
CREATE (col)-[:INVOLVED]->(a)
CREATE (col)-[:INVOLVED]->(b)
RETURN col
"""

# Counts prior recorded collisions involving the same two symbols (order-independent).
GET_PRECEDENT_QUERY = """
MATCH (col:Collision)-[:INVOLVED]->(a:Symbol {id: $sym_a})
MATCH (col)-[:INVOLVED]->(b:Symbol {id: $sym_b})
WHERE ($exclude_id IS NULL OR col.id <> $exclude_id)
  AND ($before_time IS NULL OR col.timestamp < $before_time)
RETURN count(col) AS count, max(col.timestamp) AS last_seen
"""

# Retrieves all Symbol nodes for dashboard visualization.
GET_CODE_GRAPH_SYMBOLS_QUERY = """
MATCH (s:Symbol)
RETURN s.id AS id, s.name AS name, s.file AS file, s.kind AS kind
"""

# Retrieves all directed CALLS relationships between symbols.
GET_CODE_GRAPH_CALLS_QUERY = """
MATCH (caller:Symbol)-[:CALLS]->(callee:Symbol)
RETURN caller.id AS from_id, callee.id AS to_id
"""

# Retrieves active and waiting claims with status, agent, touched symbol, and any blocker.
GET_ALL_CLAIMS_FOR_DASHBOARD_QUERY = """
MATCH (c:Claim)
WHERE c.status IN ['active', 'waiting']
OPTIONAL MATCH (c)-[:BLOCKED_BY]->(blocker:Claim)
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status,
       c.created_at AS created_at, c.updated_at AS updated_at,
       blocker.id AS blocker_id
"""

# Retrieves recent collision events ordered chronologically, along with involved symbols.
GET_ALL_COLLISIONS_QUERY = """
MATCH (col:Collision)
OPTIONAL MATCH (col)-[:INVOLVED]->(s:Symbol)
WITH col, collect(s.id) AS inv_syms
RETURN col.id AS id, col.verdict AS verdict, col.path_text AS path_text,
       col.timestamp AS timestamp, col.agent_a AS agent_a, col.agent_b AS agent_b,
       col.sym_a AS sym_a, col.sym_b AS sym_b, inv_syms
ORDER BY col.timestamp DESC
"""

# Deletes only transient Claims and Collisions for repeated demo recordings, keeping code graph intact.
RESET_DEMO_CLAIMS_QUERY = """
MATCH (c:Claim) DETACH DELETE c
"""
RESET_DEMO_COLLISIONS_QUERY = """
MATCH (col:Collision) DETACH DELETE col
"""


class GraphDB:
    """FalkorDB Client Wrapper for Conflict Radar.
    
    Supports strict mode (default: True) which asserts a real FalkorDB connection
    and raises descriptive errors on connection or query failures instead of swallowing them.
    Non-strict mode (strict=False) enables an in-memory graph fallback for isolated offline testing.
    """

    def __init__(self, host: str = os.environ.get("FALKORDB_HOST", "127.0.0.1"), port: int = 6379, graph_name: str = "conflict_radar", strict: bool = True):
        self.host = host
        self.port = port
        self.graph_name = graph_name
        self.strict = strict
        self.client = None
        self.graph = None
        self.is_connected = False

        # In-memory storage fallback only utilized when strict=False
        self._mem_nodes: Dict[str, Dict[str, Any]] = {}
        self._mem_edges: List[Dict[str, Any]] = []
        self._mem_claims: Dict[str, Dict[str, Any]] = {}
        self._mem_collisions: List[Dict[str, Any]] = []

        # Real telemetry and execution tracking for inspector
        self.last_query_latency_ms: Optional[float] = None
        self.last_path_latency_ms: Optional[float] = None
        self.last_path_cypher: Optional[str] = None
        self.last_path_params: Optional[Dict[str, Any]] = None
        self.last_collision_params: Optional[Dict[str, Any]] = None

    def connect(self) -> bool:
        """Connect to FalkorDB on Redis port 6379.
        
        In strict mode, raises ConnectionError if connection fails.
        In non-strict mode, returns False and falls back to in-memory store.
        """
        try:
            self.client = FalkorDB(host=self.host, port=self.port)
            self.graph = self.client.select_graph(self.graph_name)
            # Ping test
            self.graph.query("RETURN 1")
            self.is_connected = True
            self._ensure_constraints()
            return True
        except Exception as e:
            self.is_connected = False
            if self.strict:
                raise ConnectionError(
                    f"Failed to connect to FalkorDB on {self.host}:{self.port} in strict mode: {e}. "
                    "Make sure FalkorDB is running via 'docker compose up -d' or pass strict=False for offline testing."
                ) from e
            return False

    @contextmanager
    def distributed_lock(
        self,
        lock_name: str = "claim_lock",
        timeout_s: float = 5.0,
        expire_ms: int = 5000
    ):
        """Acquire a distributed lock backed by FalkorDB's Redis instance with re-entrancy."""
        if not self.is_connected or self.client is None or not hasattr(self.client, "connection"):
            # Fallback for in-memory or disconnected mode
            if not hasattr(self, "_mem_lock"):
                self._mem_lock = threading.RLock()
            with self._mem_lock:
                yield
            return

        lock_key = f"conflict_radar:lock:{self.graph_name}:{lock_name}"
        tls_depth_key = f"depth_{lock_name}"
        tls_token_key = f"token_{lock_name}"

        # Re-entrancy check within the current thread
        current_depth = getattr(_lock_tls, tls_depth_key, 0)
        if current_depth > 0:
            setattr(_lock_tls, tls_depth_key, current_depth + 1)
            try:
                yield
            finally:
                setattr(_lock_tls, tls_depth_key, getattr(_lock_tls, tls_depth_key, 1) - 1)
            return

        token = str(uuid.uuid4())
        start_time = time.perf_counter()
        acquired = False
        redis_conn = self.client.connection

        while (time.perf_counter() - start_time) < timeout_s:
            # Redis SET key token NX PX expire_ms
            if redis_conn.set(lock_key, token, nx=True, px=expire_ms):
                acquired = True
                break
            time.sleep(0.005)

        if not acquired:
            raise TimeoutError(f"Could not acquire distributed lock '{lock_name}' within {timeout_s}s.")

        setattr(_lock_tls, tls_depth_key, 1)
        setattr(_lock_tls, tls_token_key, token)
        try:
            yield
        finally:
            try:
                redis_conn.eval(LUA_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
            except Exception:
                pass
            setattr(_lock_tls, tls_depth_key, 0)
            setattr(_lock_tls, tls_token_key, None)

    def _query(self, cypher: str, params: Optional[Dict[str, Any]] = None):
        """Execute Cypher query against FalkorDB.
        
        In strict mode:
        - Raises ConnectionError if not connected.
        - Raises RuntimeError if query execution fails.
        
        In non-strict mode:
        - Returns query result if connected, otherwise None to allow in-memory fallback.
        """
        t0 = time.perf_counter()
        if self.strict:
            if not self.is_connected or not self.graph:
                raise ConnectionError(
                    f"FalkorDB is not connected on {self.host}:{self.port}. "
                    "Run 'docker compose up -d' or pass strict=False for offline testing."
                )
            try:
                res = self.graph.query(cypher, params)
                self.last_query_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                return res
            except Exception as e:
                raise RuntimeError(f"FalkorDB query failed: {e}\nCypher: {cypher}\nParams: {params}") from e
        else:
            if self.is_connected and self.graph:
                try:
                    res = self.graph.query(cypher, params)
                    self.last_query_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    return res
                except Exception:
                    return None
            return None

    def _ensure_constraints(self):
        """Ensure uniqueness constraints for Symbols, Files, and Claims."""
        if not self.is_connected or not self.graph:
            return
        constraints = [
            "CREATE CONSTRAINT FOR (s:Symbol) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT FOR (f:File) REQUIRE f.path IS UNIQUE",
            "CREATE CONSTRAINT FOR (c:Claim) REQUIRE c.id IS UNIQUE",
        ]
        for c in constraints:
            try:
                self.graph.query(c)
            except Exception:
                pass

    def clear(self):
        """Wipe all nodes and edges."""
        self._query("MATCH (n) DETACH DELETE n")
        self._mem_nodes.clear()
        self._mem_edges.clear()
        self._mem_claims.clear()
        self._mem_collisions.clear()

    # --- Upsert & Graph Building ---

    def upsert_file(self, path: str):
        """Idempotently insert a File node."""
        if not self.strict:
            self._mem_nodes[path] = {"type": "File", "path": path}
        self._query(UPSERT_FILE_QUERY, {"path": path})

    def upsert_symbol(self, sym_id: str, name: str, file: str, kind: str = "function"):
        """Idempotently insert a Symbol node."""
        if not self.strict:
            self._mem_nodes[sym_id] = {"type": "Symbol", "id": sym_id, "name": name, "file": file, "kind": kind}
        self._query(UPSERT_SYMBOL_QUERY, {"id": sym_id, "name": name, "file": file, "kind": kind})

    def get_all_symbol_ids(self) -> List[str]:
        """Return list of all existing Symbol IDs in the graph."""
        res = self._query(GET_CODE_GRAPH_SYMBOLS_QUERY)
        if res is not None:
            return [row[0] for row in res.result_set]
        return [nid for nid, n in self._mem_nodes.items() if n.get("type") == "Symbol"]

    def symbol_exists(self, symbol_id: str) -> bool:
        """Check if a Symbol exists in the graph."""
        res = self._query("MATCH (s:Symbol {id: $id}) RETURN count(s) AS count", {"id": symbol_id})
        if res is not None and res.result_set:
            return (res.result_set[0][0] or 0) > 0
        return symbol_id in self._mem_nodes and self._mem_nodes[symbol_id].get("type") == "Symbol"

    def add_contains(self, file_path: str, sym_id: str):
        """Create a (File)-[:CONTAINS]->(Symbol) relationship."""
        if not self.strict:
            self._mem_edges.append({"type": "CONTAINS", "from": file_path, "to": sym_id})
        self._query(ADD_CONTAINS_QUERY, {"file_path": file_path, "sym_id": sym_id})

    def add_calls(self, caller_id: str, callee_id: str):
        """Create a directed (caller:Symbol)-[:CALLS]->(callee:Symbol) relationship."""
        if not self.strict:
            self._mem_edges.append({"type": "CALLS", "from": caller_id, "to": callee_id})
        self._query(ADD_CALLS_QUERY, {"caller_id": caller_id, "callee_id": callee_id})

    # --- Claims Management ---

    def add_claim(self, agent_id: str, symbol_id: str, change_type: str, status: str = "active", now: Optional[float] = None) -> str:
        """Create a claim for an agent with specified status ('active' or 'waiting')."""
        claim_id = f"claim_{uuid.uuid4().hex[:6]}"
        ts = now if now is not None else time.time()
        if not self.strict:
            self._mem_claims[claim_id] = {
                "id": claim_id,
                "agent_id": agent_id,
                "symbol_id": symbol_id,
                "change_type": change_type,
                "status": status,
                "created_at": ts,
                "updated_at": ts,
                "blocked_by": None
            }
        self._query(ADD_CLAIM_QUERY, {
            "claim_id": claim_id,
            "agent_id": agent_id,
            "symbol_id": symbol_id,
            "change_type": change_type,
            "status": status,
            "created_at": ts,
            "updated_at": ts
        })
        return claim_id

    def add_blocked_by(self, blocked_id: str, blocker_id: str):
        """Create a (blocked:Claim)-[:BLOCKED_BY]->(blocker:Claim) relationship."""
        if not self.strict:
            if blocked_id in self._mem_claims:
                self._mem_claims[blocked_id]["blocked_by"] = blocker_id
            self._mem_edges.append({"type": "BLOCKED_BY", "from": blocked_id, "to": blocker_id})
        self._query(ADD_BLOCKED_BY_QUERY, {
            "blocked_id": blocked_id,
            "blocker_id": blocker_id
        })

    def remove_blocked_by(self, claim_id: str):
        """Remove any BLOCKED_BY relationship originating from claim_id."""
        if not self.strict:
            if claim_id in self._mem_claims:
                self._mem_claims[claim_id]["blocked_by"] = None
            self._mem_edges = [e for e in self._mem_edges if not (e["type"] == "BLOCKED_BY" and e["from"] == claim_id)]
        self._query(REMOVE_BLOCKED_BY_QUERY, {"claim_id": claim_id})

    def update_claim_status(self, claim_id: str, status: str, now: Optional[float] = None):
        """Update claim status ('active', 'waiting', 'done', 'expired') and updated_at."""
        ts = now if now is not None else time.time()
        if not self.strict:
            if claim_id in self._mem_claims:
                self._mem_claims[claim_id]["status"] = status
                self._mem_claims[claim_id]["updated_at"] = ts
        self._query(UPDATE_CLAIM_STATUS_QUERY, {
            "claim_id": claim_id,
            "status": status,
            "updated_at": ts
        })

    def touch_claim(self, claim_id: str, now: Optional[float] = None):
        """Update updated_at timestamp on an active/waiting claim."""
        ts = now if now is not None else time.time()
        if not self.strict:
            if claim_id in self._mem_claims:
                self._mem_claims[claim_id]["updated_at"] = ts
        self._query(TOUCH_CLAIM_QUERY, {"claim_id": claim_id, "ts": ts})

    def expire_stale_claims(self, cutoff: float) -> List[Dict[str, Any]]:
        """Expire all ACTIVE or WAITING claims whose updated_at is older than cutoff."""
        res = self._query(EXPIRE_CLAIMS_QUERY, {"cutoff": cutoff})
        if res is not None:
            expired = []
            for row in res.result_set:
                expired.append({
                    "id": row[0],
                    "agent_id": row[1],
                    "symbol_id": row[2],
                    "change_type": row[3],
                    "status": "expired"
                })
            return expired

        # In-memory fallback
        expired = []
        for c in list(self._mem_claims.values()):
            if c["status"] in ("active", "waiting") and c.get("updated_at", c["created_at"]) < cutoff:
                c["status"] = "expired"
                c["updated_at"] = cutoff
                c["blocked_by"] = None
                self._mem_edges = [
                    e for e in self._mem_edges
                    if not (e["type"] == "BLOCKED_BY" and (e["from"] == c["id"] or e["to"] == c["id"]))
                ]
                expired.append(dict(c))
        return expired

    def get_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a claim by its ID."""
        res = self._query(GET_CLAIM_BY_ID_QUERY, {"claim_id": claim_id})
        if res is not None:
            if res.result_set and len(res.result_set) > 0:
                row = res.result_set[0]
                return {
                    "id": row[0],
                    "agent_id": row[1],
                    "symbol_id": row[2],
                    "change_type": row[3],
                    "status": row[4],
                    "created_at": row[5],
                    "blocked_by": row[6],
                    "updated_at": row[7] if len(row) > 7 else row[5]
                }
            return None
        return self._mem_claims.get(claim_id)

    def release_claim(self, claim_id: str, now: Optional[float] = None) -> bool:
        """Mark a claim as 'done'."""
        ts = now if now is not None else time.time()
        if not self.strict:
            if claim_id in self._mem_claims:
                self._mem_claims[claim_id]["status"] = "done"
                self._mem_claims[claim_id]["updated_at"] = ts
                self._mem_claims[claim_id]["blocked_by"] = None
        self._query(RELEASE_CLAIM_QUERY, {"claim_id": claim_id, "updated_at": ts})
        return True

    def get_active_claims(self) -> List[Dict[str, Any]]:
        """Return all claims with status == 'active'."""
        res = self._query(GET_ACTIVE_CLAIMS_QUERY)
        if res is not None:
            active = []
            for row in res.result_set:
                active.append({
                    "id": row[0],
                    "agent_id": row[1],
                    "symbol_id": row[2],
                    "change_type": row[3],
                    "status": row[4],
                    "created_at": row[5],
                    "updated_at": row[6] if len(row) > 6 else row[5]
                })
            return active
        return [c for c in self._mem_claims.values() if c["status"] == "active"]

    def get_waiting_claims(self) -> List[Dict[str, Any]]:
        """Return all claims with status == 'waiting'."""
        res = self._query(GET_WAITING_CLAIMS_QUERY)
        if res is not None:
            waiting = []
            seen = set()
            for row in res.result_set:
                cid = row[0]
                if cid not in seen:
                    seen.add(cid)
                    waiting.append({
                        "id": cid,
                        "agent_id": row[1],
                        "symbol_id": row[2],
                        "change_type": row[3],
                        "status": row[4],
                        "created_at": row[5],
                        "blocked_by": row[6],
                        "updated_at": row[7] if len(row) > 7 else row[5]
                    })
            return waiting
        return [c for c in self._mem_claims.values() if c["status"] == "waiting"]

    # --- Path Traversal (Both Directions) ---

    def _query_directional_path(self, start_id: str, end_id: str, max_hops: int) -> Optional[List[str]]:
        """Query a single directed path from start_id to end_id.
        
        In openCypher, hop range inside shortestPath must be an integer literal
        rather than a parameter.
        """
        hops_int = int(max_hops)
        q_shortest = f"""
        MATCH (a:Symbol {{id: $start_id}}), (b:Symbol {{id: $end_id}})
        WITH shortestPath((a)-[:CALLS*1..{hops_int}]->(b)) AS p
        RETURN [node IN nodes(p) | node.id] AS path_nodes
        LIMIT 1
        """
        params = {"start_id": start_id, "end_id": end_id}
        t0 = time.perf_counter()
        res = self._query(q_shortest, params)
        self.last_path_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        self.last_path_cypher = q_shortest.strip()
        self.last_path_params = params

        if res is not None:
            if res.result_set and len(res.result_set) > 0 and res.result_set[0][0]:
                return res.result_set[0][0]
            return None

        # In-memory BFS fallback for non-strict mode
        queue: List[List[str]] = [[start_id]]
        visited = {start_id}
        while queue:
            curr_path = queue.pop(0)
            if len(curr_path) - 1 >= hops_int:
                continue
            last = curr_path[-1]
            for edge in self._mem_edges:
                if edge["type"] == "CALLS" and edge["from"] == last:
                    nxt = edge["to"]
                    if nxt == end_id:
                        return curr_path + [nxt]
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(curr_path + [nxt])
        return None

    def find_call_path(self, a: str, b: str, max_hops: int = 3) -> Optional[List[str]]:
        """Search for a directed CALLS path in BOTH directions (A->B or B->A)."""
        path_ab = self._query_directional_path(a, b, max_hops)
        if path_ab:
            return path_ab
        path_ba = self._query_directional_path(b, a, max_hops)
        if path_ba:
            return path_ba
        return None

    @classmethod
    def get_query_templates(cls) -> List[Dict[str, Any]]:
        """Return the real Cypher query templates, schema labels, and relationships used by Conflict Radar."""
        return [
            {
                "id": "path_search",
                "name": "Shortest Directed Call Path",
                "explanation": "Traverses transitive :CALLS dependencies up to max hops using shortestPath (the hop bound is injected as a validated integer, not a parameter).",
                "cypher": (
                    "MATCH (a:Symbol {id: $start_id}), (b:Symbol {id: $end_id})\n"
                    "WITH shortestPath((a)-[:CALLS*1..3]->(b)) AS p\n"
                    "RETURN [node IN nodes(p) | node.id] AS path_nodes\n"
                    "LIMIT 1"
                ),
                "labels": [":Symbol"],
                "relationships": ["CALLS"]
            },
            {
                "id": "claim_create",
                "name": "Claim registration",
                "explanation": "Persists an agent claim with status and links it to the target function symbol.",
                "cypher": ADD_CLAIM_QUERY.strip(),
                "labels": [":Agent", ":Symbol", ":Claim"],
                "relationships": ["MADE", "TOUCHES"]
            },
            {
                "id": "blocked_by_edge",
                "name": "Blocking Dependency Queue",
                "explanation": "Queues a waiting agent behind an active lock by creating a BLOCKED_BY edge.",
                "cypher": ADD_BLOCKED_BY_QUERY.strip(),
                "labels": [":Claim"],
                "relationships": ["BLOCKED_BY"]
            },
            {
                "id": "collision_record",
                "name": "Historical Collision Persistence",
                "explanation": "Records an immutable collision node linked to both involved symbols via :INVOLVED.",
                "cypher": RECORD_COLLISION_QUERY.strip(),
                "labels": [":Collision", ":Symbol"],
                "relationships": ["INVOLVED"]
            },
            {
                "id": "precedent_lookup",
                "name": "Historical Precedent Query",
                "explanation": "Traverses prior collisions across both symbols to calculate frequency and precedent count.",
                "cypher": GET_PRECEDENT_QUERY.strip(),
                "labels": [":Collision", ":Symbol"],
                "relationships": ["INVOLVED"]
            }
        ]

    # --- Precedent & Collision Memory ---

    def record_collision(self, sym_a: str, sym_b: str, verdict: str, path_text: str, agent_a: str = "", agent_b: str = "", now: Optional[float] = None):
        """Record a collision event between two symbols for precedent memory."""
        col_id = f"col_{uuid.uuid4().hex[:6]}"
        ts = now if now is not None else time.time()
        self.last_collision_params = {
            "sym_a": sym_a,
            "sym_b": sym_b,
            "verdict": verdict,
            "path_text": path_text,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "timestamp": ts
        }
        if not self.strict:
            self._mem_collisions.append({
                "id": col_id,
                "sym_a": sym_a,
                "sym_b": sym_b,
                "verdict": verdict,
                "path_text": path_text,
                "agent_a": agent_a,
                "agent_b": agent_b,
                "timestamp": ts
            })

        self._query(RECORD_COLLISION_QUERY, {
            "collision_id": col_id,
            "sym_a": sym_a,
            "sym_b": sym_b,
            "verdict": verdict,
            "path_text": path_text,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "timestamp": ts
        })

    def get_precedent(self, sym_a: str, sym_b: str, before_time: Optional[float] = None, exclude_id: Optional[str] = None) -> Dict[str, Any]:
        """Return count and last collision timestamp for this pair of symbols (order-independent)."""
        res = self._query(GET_PRECEDENT_QUERY, {
            "sym_a": sym_a,
            "sym_b": sym_b,
            "before_time": before_time,
            "exclude_id": exclude_id
        })
        if res is not None:
            if res.result_set and len(res.result_set) > 0:
                count = res.result_set[0][0] or 0
                last_seen = res.result_set[0][1]
                return {"count": count, "last_seen": last_seen}
            return {"count": 0, "last_seen": None}

        # In-memory precedent lookup (order-independent)
        cnt = 0
        last_seen = None
        for col in self._mem_collisions:
            if exclude_id is not None and col.get("id") == exclude_id:
                continue
            if before_time is not None and col["timestamp"] >= before_time:
                continue
            pair = {col["sym_a"], col["sym_b"]}
            if (sym_a == sym_b and col["sym_a"] == sym_a and col["sym_b"] == sym_b) or (sym_a != sym_b and sym_a in pair and sym_b in pair):
                cnt += 1
                if last_seen is None or col["timestamp"] > last_seen:
                    last_seen = col["timestamp"]

        return {"count": cnt, "last_seen": last_seen}

    # --- Dashboard & Demo Reset APIs ---

    def get_full_graph(self) -> Dict[str, Any]:
        """Extract nodes, edges, claims, and blocked_by relationships for the dashboard."""
        res_sym = self._query(GET_CODE_GRAPH_SYMBOLS_QUERY)
        if res_sym is not None:
            nodes: List[Dict[str, Any]] = []
            edges: List[Dict[str, Any]] = []
            claims: List[Dict[str, Any]] = []
            blocked_by: List[Dict[str, Any]] = []

            for row in res_sym.result_set:
                nodes.append({
                    "id": row[0],
                    "name": row[1],
                    "file": row[2],
                    "kind": row[3] or "function"
                })

            calls_res = self._query(GET_CODE_GRAPH_CALLS_QUERY)
            for row in calls_res.result_set:
                edges.append({
                    "from": row[0],
                    "to": row[1],
                    "type": "CALLS"
                })

            claim_res = self._query(GET_ALL_CLAIMS_FOR_DASHBOARD_QUERY)
            for row in claim_res.result_set:
                c_id, agent_id, sym_id, change_type, status, created_at, updated_at, blocker_id = row
                claims.append({
                    "id": c_id,
                    "agent_id": agent_id,
                    "symbol_id": sym_id,
                    "change_type": change_type,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "blocked_by": blocker_id
                })
                if blocker_id:
                    blocked_by.append({"from": c_id, "to": blocker_id})

            recent_cols = self.get_recent_collisions(limit=10)
            for c in recent_cols:
                path_parts = c["path_text"].split(" -> ") if c.get("path_text") else []
                sym_a = c.get("sym_a") or (path_parts[0] if path_parts else "")
                sym_b = c.get("sym_b") or (path_parts[-1] if path_parts else "")
                prec = self.get_precedent(sym_a, sym_b, before_time=c.get("timestamp"), exclude_id=c.get("id"))
                c["precedent_count"] = prec.get("count", 0)

            return {
                "nodes": nodes,
                "edges": edges,
                "claims": claims,
                "blocked_by": blocked_by,
                "collisions": recent_cols
            }

        # In-memory fallback
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        claims: List[Dict[str, Any]] = []
        blocked_by: List[Dict[str, Any]] = []

        for nid, n in self._mem_nodes.items():
            if n.get("type") == "Symbol":
                nodes.append({
                    "id": n["id"],
                    "name": n["name"],
                    "file": n["file"],
                    "kind": n.get("kind", "function")
                })

        for e in self._mem_edges:
            if e["type"] == "CALLS":
                edges.append({"from": e["from"], "to": e["to"], "type": "CALLS"})
            elif e["type"] == "BLOCKED_BY":
                blocked_by.append({"from": e["from"], "to": e["to"]})

        for c in self._mem_claims.values():
            if c.get("status") in ["active", "waiting"]:
                claims.append(dict(c))

        recent_mem_cols = self.get_recent_collisions(limit=10)
        for c in recent_mem_cols:
            path_parts = c["path_text"].split(" -> ") if c.get("path_text") else []
            sym_a = c.get("sym_a") or (path_parts[0] if path_parts else "")
            sym_b = c.get("sym_b") or (path_parts[-1] if path_parts else "")
            prec = self.get_precedent(sym_a, sym_b, before_time=c.get("timestamp"), exclude_id=c.get("id"))
            c["precedent_count"] = prec.get("count", 0)

        return {
            "nodes": nodes,
            "edges": edges,
            "claims": claims,
            "blocked_by": blocked_by,
            "collisions": recent_mem_cols
        }

    def get_recent_collisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent collision records ordered chronologically descending."""
        res = self._query(GET_ALL_COLLISIONS_QUERY)
        if res is not None:
            results = []
            for row in res.result_set[:limit]:
                col_id, verdict, path_text, ts, ag_a, ag_b = row[0], row[1], row[2], row[3], row[4], row[5]
                s_a = row[6] if len(row) > 6 and row[6] else None
                s_b = row[7] if len(row) > 7 and row[7] else None
                inv_syms = row[8] if len(row) > 8 and row[8] else []
                if not s_a and len(inv_syms) > 0:
                    s_a = inv_syms[0]
                if not s_b and len(inv_syms) > 1:
                    s_b = inv_syms[1]
                if (not s_a or not s_b) and path_text:
                    parts = path_text.split(" -> ")
                    if not s_a and parts:
                        s_a = parts[0]
                    if not s_b and parts:
                        s_b = parts[-1]
                results.append({
                    "id": col_id,
                    "verdict": verdict,
                    "path_text": path_text,
                    "timestamp": ts,
                    "agent_a": ag_a,
                    "agent_b": ag_b,
                    "sym_a": s_a,
                    "sym_b": s_b
                })
            return results

        sorted_cols = sorted(self._mem_collisions, key=lambda c: c.get("timestamp", 0), reverse=True)
        return sorted_cols[:limit]

    def reset_demo_state(self) -> Dict[str, Any]:
        """Reset only transient Claims and Collisions for repeated demo recordings."""
        self._query(RESET_DEMO_CLAIMS_QUERY)
        self._query(RESET_DEMO_COLLISIONS_QUERY)
        self._mem_claims.clear()
        self._mem_collisions.clear()
        self._mem_edges = [e for e in self._mem_edges if e.get("type") in ("CONTAINS", "CALLS")]
        return {"status": "ok", "message": "Demo state reset: claims and collisions cleared, code graph preserved."}
