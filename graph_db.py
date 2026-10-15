"""graph_db.py - FalkorDB Graph Layer and Cypher Query Engine for Conflict Radar.

Wraps the falkordb Python client to manage codebase graph topology,
claims, call-chain traversals, and precedent memory.
"""

import time
import uuid
from typing import Dict, List, Optional, Any, Tuple
from falkordb import FalkorDB

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

# Creates an active Claim node, linked to the Agent that made it
# and the Symbol it touches.
ADD_CLAIM_QUERY = """
MERGE (a:Agent {id: $agent_id, name: $agent_id})
MERGE (s:Symbol {id: $symbol_id})
CREATE (c:Claim {
    id: $claim_id,
    agent_id: $agent_id,
    symbol_id: $symbol_id,
    change_type: $change_type,
    status: 'active',
    created_at: $created_at
})
CREATE (a)-[:MADE]->(c)
CREATE (c)-[:TOUCHES]->(s)
RETURN c
"""

# Marks a Claim as 'done' when an agent releases its modification lock.
RELEASE_CLAIM_QUERY = """
MATCH (c:Claim {id: $claim_id})
SET c.status = 'done'
RETURN c
"""

# Retrieves all currently active claims across the entire codebase.
GET_ACTIVE_CLAIMS_QUERY = """
MATCH (c:Claim {status: 'active'})
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status, c.created_at AS created_at
"""

# Records a collision event between two symbols for historical precedent.
RECORD_COLLISION_QUERY = """
CREATE (col:Collision {
    id: $collision_id,
    verdict: $verdict,
    path_text: $path_text,
    timestamp: $timestamp
})
WITH col
MATCH (a:Symbol {id: $sym_a}), (b:Symbol {id: $sym_b})
CREATE (col)-[:INVOLVED]->(a)
CREATE (col)-[:INVOLVED]->(b)
RETURN col
"""

# Counts prior recorded collisions involving the same two symbols.
GET_PRECEDENT_QUERY = """
MATCH (col:Collision)-[:INVOLVED]->(a:Symbol {id: $sym_a})
MATCH (col)-[:INVOLVED]->(b:Symbol {id: $sym_b})
RETURN count(col) AS count, max(col.timestamp) AS last_seen
"""


class GraphDB:
    """FalkorDB Client Wrapper for Conflict Radar."""

    def __init__(self, host: str = "localhost", port: int = 6379, graph_name: str = "conflict_radar"):
        self.host = host
        self.port = port
        self.graph_name = graph_name
        self.client = None
        self.graph = None
        self.is_connected = False

        # In-memory storage fallback for unit testing if FalkorDB daemon is offline
        self._mem_nodes: Dict[str, Dict[str, Any]] = {}
        self._mem_edges: List[Dict[str, Any]] = []
        self._mem_claims: Dict[str, Dict[str, Any]] = {}
        self._mem_collisions: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        """Connect to FalkorDB on Redis port 6379."""
        try:
            self.client = FalkorDB(host=self.host, port=self.port)
            self.graph = self.client.select_graph(self.graph_name)
            # Ping test
            self.graph.query("RETURN 1")
            self.is_connected = True
            self._ensure_constraints()
            return True
        except Exception:
            self.is_connected = False
            return False

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
        """Wipe all nodes and edges (useful for tests)."""
        if self.is_connected and self.graph:
            try:
                self.graph.query("MATCH (n) DETACH DELETE n")
            except Exception:
                pass
        self._mem_nodes.clear()
        self._mem_edges.clear()
        self._mem_claims.clear()
        self._mem_collisions.clear()

    # --- Upsert & Graph Building ---

    def upsert_file(self, path: str):
        """Idempotently insert a File node."""
        self._mem_nodes[path] = {"type": "File", "path": path}
        if self.is_connected and self.graph:
            self.graph.query(UPSERT_FILE_QUERY, {"path": path})

    def upsert_symbol(self, sym_id: str, name: str, file: str, kind: str = "function"):
        """Idempotently insert a Symbol node."""
        self._mem_nodes[sym_id] = {"type": "Symbol", "id": sym_id, "name": name, "file": file, "kind": kind}
        if self.is_connected and self.graph:
            self.graph.query(UPSERT_SYMBOL_QUERY, {"id": sym_id, "name": name, "file": file, "kind": kind})

    def add_contains(self, file_path: str, sym_id: str):
        """Create a (File)-[:CONTAINS]->(Symbol) relationship."""
        self._mem_edges.append({"type": "CONTAINS", "from": file_path, "to": sym_id})
        if self.is_connected and self.graph:
            self.graph.query(ADD_CONTAINS_QUERY, {"file_path": file_path, "sym_id": sym_id})

    def add_calls(self, caller_id: str, callee_id: str):
        """Create a directed (caller:Symbol)-[:CALLS]->(callee:Symbol) relationship."""
        self._mem_edges.append({"type": "CALLS", "from": caller_id, "to": callee_id})
        if self.is_connected and self.graph:
            self.graph.query(ADD_CALLS_QUERY, {"caller_id": caller_id, "callee_id": callee_id})

    # --- Claims Management ---

    def add_claim(self, agent_id: str, symbol_id: str, change_type: str) -> str:
        """Create an active claim for an agent and return the claim ID."""
        claim_id = f"claim_{uuid.uuid4().hex[:6]}"
        now = time.time()
        record = {
            "id": claim_id,
            "agent_id": agent_id,
            "symbol_id": symbol_id,
            "change_type": change_type,
            "status": "active",
            "created_at": now
        }
        self._mem_claims[claim_id] = record

        if self.is_connected and self.graph:
            self.graph.query(ADD_CLAIM_QUERY, {
                "claim_id": claim_id,
                "agent_id": agent_id,
                "symbol_id": symbol_id,
                "change_type": change_type,
                "created_at": now
            })
        return claim_id

    def release_claim(self, claim_id: str) -> bool:
        """Mark a claim as 'done'."""
        if claim_id in self._mem_claims:
            self._mem_claims[claim_id]["status"] = "done"

        if self.is_connected and self.graph:
            self.graph.query(RELEASE_CLAIM_QUERY, {"claim_id": claim_id})
        return True

    def get_active_claims(self) -> List[Dict[str, Any]]:
        """Return all claims with status == 'active'."""
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_ACTIVE_CLAIMS_QUERY)
                active = []
                for row in res.result_set:
                    active.append({
                        "id": row[0],
                        "agent_id": row[1],
                        "symbol_id": row[2],
                        "change_type": row[3],
                        "status": row[4],
                        "created_at": row[5]
                    })
                return active
            except Exception:
                pass
        return [c for c in self._mem_claims.values() if c["status"] == "active"]

    # --- Path Traversal (Both Directions) ---

    def _query_directional_path(self, start_id: str, end_id: str, max_hops: int) -> Optional[List[str]]:
        """Query a single directed path from start_id to end_id.
        
        Note on FalkorDB syntax:
        In openCypher, hop range inside shortestPath must be an integer literal
        (e.g. *1..3) rather than a query parameter ($hops).
        """
        hops_int = int(max_hops)

        if self.is_connected and self.graph:
            # Primary: shortestPath query
            q_shortest = f"""
            MATCH (a:Symbol {{id: $start_id}}), (b:Symbol {{id: $end_id}})
            MATCH p = shortestPath((a)-[:CALLS*1..{hops_int}]->(b))
            RETURN [node IN nodes(p) | node.id] AS path_nodes
            LIMIT 1
            """
            try:
                res = self.graph.query(q_shortest, {"start_id": start_id, "end_id": end_id})
                if res.result_set and len(res.result_set) > 0 and res.result_set[0][0]:
                    return res.result_set[0][0]
            except Exception:
                # Fallback: standard variable-length path ordered by length
                q_fallback = f"""
                MATCH (a:Symbol {{id: $start_id}}), (b:Symbol {{id: $end_id}})
                MATCH p = (a)-[:CALLS*1..{hops_int}]->(b)
                RETURN [node IN nodes(p) | node.id] AS path_nodes
                ORDER BY length(p) ASC
                LIMIT 1
                """
                try:
                    res = self.graph.query(q_fallback, {"start_id": start_id, "end_id": end_id})
                    if res.result_set and len(res.result_set) > 0 and res.result_set[0][0]:
                        return res.result_set[0][0]
                except Exception:
                    pass

        # In-memory BFS fallback
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
        """Search for a directed CALLS path in BOTH directions (A->B or B->A).
        
        Returns the path as a list of symbol IDs, or None if no path exists.
        """
        # Direction 1: a calls ... calls b
        path_ab = self._query_directional_path(a, b, max_hops)
        if path_ab:
            return path_ab

        # Direction 2: b calls ... calls a
        path_ba = self._query_directional_path(b, a, max_hops)
        if path_ba:
            return path_ba

        return None

    # --- Precedent & Collision Memory ---

    def record_collision(self, sym_a: str, sym_b: str, verdict: str, path_text: str):
        """Record a collision event between two symbols for precedent memory."""
        col_id = f"col_{uuid.uuid4().hex[:6]}"
        now = time.time()
        record = {
            "id": col_id,
            "sym_a": sym_a,
            "sym_b": sym_b,
            "verdict": verdict,
            "path_text": path_text,
            "timestamp": now
        }
        self._mem_collisions.append(record)

        if self.is_connected and self.graph:
            try:
                self.graph.query(RECORD_COLLISION_QUERY, {
                    "collision_id": col_id,
                    "sym_a": sym_a,
                    "sym_b": sym_b,
                    "verdict": verdict,
                    "path_text": path_text,
                    "timestamp": now
                })
            except Exception:
                pass

    def get_precedent(self, sym_a: str, sym_b: str) -> Dict[str, Any]:
        """Return count and last collision timestamp for this pair of symbols."""
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_PRECEDENT_QUERY, {"sym_a": sym_a, "sym_b": sym_b})
                if res.result_set and len(res.result_set) > 0:
                    count = res.result_set[0][0] or 0
                    last_seen = res.result_set[0][1]
                    return {"count": count, "last_seen": last_seen}
            except Exception:
                pass

        # In-memory precedent lookup
        cnt = 0
        last_seen = None
        for col in self._mem_collisions:
            pair = {col["sym_a"], col["sym_b"]}
            if sym_a in pair and sym_b in pair:
                cnt += 1
                if last_seen is None or col["timestamp"] > last_seen:
                    last_seen = col["timestamp"]

        return {"count": cnt, "last_seen": last_seen}
