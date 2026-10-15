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
    agent_b: $agent_b
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
WHERE col.timestamp < $before_time
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

# Retrieves all claims with status, agent, touched symbol, and any blocker.
GET_ALL_CLAIMS_FOR_DASHBOARD_QUERY = """
MATCH (c:Claim)
OPTIONAL MATCH (c)-[:BLOCKED_BY]->(blocker:Claim)
RETURN c.id AS id, c.agent_id AS agent_id, c.symbol_id AS symbol_id,
       c.change_type AS change_type, c.status AS status,
       c.created_at AS created_at, c.updated_at AS updated_at,
       blocker.id AS blocker_id
"""

# Retrieves recent collision events ordered chronologically.
GET_ALL_COLLISIONS_QUERY = """
MATCH (col:Collision)
RETURN col.id AS id, col.verdict AS verdict, col.path_text AS path_text,
       col.timestamp AS timestamp, col.agent_a AS agent_a, col.agent_b AS agent_b
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

    def add_claim(self, agent_id: str, symbol_id: str, change_type: str, status: str = "active", now: Optional[float] = None) -> str:
        """Create a claim for an agent with specified status ('active' or 'waiting')."""
        claim_id = f"claim_{uuid.uuid4().hex[:6]}"
        ts = now if now is not None else time.time()
        record = {
            "id": claim_id,
            "agent_id": agent_id,
            "symbol_id": symbol_id,
            "change_type": change_type,
            "status": status,
            "created_at": ts,
            "updated_at": ts,
            "blocked_by": None
        }
        self._mem_claims[claim_id] = record

        if self.is_connected and self.graph:
            self.graph.query(ADD_CLAIM_QUERY, {
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
        if blocked_id in self._mem_claims:
            self._mem_claims[blocked_id]["blocked_by"] = blocker_id
        self._mem_edges.append({"type": "BLOCKED_BY", "from": blocked_id, "to": blocker_id})

        if self.is_connected and self.graph:
            self.graph.query(ADD_BLOCKED_BY_QUERY, {
                "blocked_id": blocked_id,
                "blocker_id": blocker_id
            })

    def remove_blocked_by(self, claim_id: str):
        """Remove any BLOCKED_BY relationship originating from claim_id."""
        if claim_id in self._mem_claims:
            self._mem_claims[claim_id]["blocked_by"] = None
        self._mem_edges = [e for e in self._mem_edges if not (e["type"] == "BLOCKED_BY" and e["from"] == claim_id)]

        if self.is_connected and self.graph:
            self.graph.query(REMOVE_BLOCKED_BY_QUERY, {"claim_id": claim_id})

    def update_claim_status(self, claim_id: str, status: str, now: Optional[float] = None):
        """Update claim status ('active', 'waiting', 'done', 'expired') and updated_at."""
        ts = now if now is not None else time.time()
        if claim_id in self._mem_claims:
            self._mem_claims[claim_id]["status"] = status
            self._mem_claims[claim_id]["updated_at"] = ts

        if self.is_connected and self.graph:
            self.graph.query(UPDATE_CLAIM_STATUS_QUERY, {
                "claim_id": claim_id,
                "status": status,
                "updated_at": ts
            })

    def expire_stale_claims(self, cutoff: float) -> List[Dict[str, Any]]:
        """Expire all ACTIVE or WAITING claims whose updated_at is older than cutoff."""
        expired = []
        # In-memory
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

        if self.is_connected and self.graph:
            try:
                res = self.graph.query(EXPIRE_CLAIMS_QUERY, {"cutoff": cutoff})
                # If connected, result set holds the expired claims
                db_expired = []
                for row in res.result_set:
                    db_expired.append({
                        "id": row[0],
                        "agent_id": row[1],
                        "symbol_id": row[2],
                        "change_type": row[3],
                        "status": "expired"
                    })
                return db_expired
            except Exception:
                pass
        return expired

    def get_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a claim by its ID."""
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_CLAIM_BY_ID_QUERY, {"claim_id": claim_id})
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
            except Exception:
                pass
        return self._mem_claims.get(claim_id)

    def release_claim(self, claim_id: str, now: Optional[float] = None) -> bool:
        """Mark a claim as 'done'."""
        ts = now if now is not None else time.time()
        if claim_id in self._mem_claims:
            self._mem_claims[claim_id]["status"] = "done"
            self._mem_claims[claim_id]["updated_at"] = ts

        if self.is_connected and self.graph:
            self.graph.query(RELEASE_CLAIM_QUERY, {"claim_id": claim_id, "updated_at": ts})
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
                        "created_at": row[5],
                        "updated_at": row[6] if len(row) > 6 else row[5]
                    })
                return active
            except Exception:
                pass
        return [c for c in self._mem_claims.values() if c["status"] == "active"]

    def get_waiting_claims(self) -> List[Dict[str, Any]]:
        """Return all claims with status == 'waiting'."""
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_WAITING_CLAIMS_QUERY)
                waiting = []
                for row in res.result_set:
                    waiting.append({
                        "id": row[0],
                        "agent_id": row[1],
                        "symbol_id": row[2],
                        "change_type": row[3],
                        "status": row[4],
                        "created_at": row[5],
                        "blocked_by": row[6]
                    })
                return waiting
            except Exception:
                pass
        return [c for c in self._mem_claims.values() if c["status"] == "waiting"]

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

    def record_collision(self, sym_a: str, sym_b: str, verdict: str, path_text: str, agent_a: str = "", agent_b: str = "", now: Optional[float] = None):
        """Record a collision event between two symbols for precedent memory."""
        col_id = f"col_{uuid.uuid4().hex[:6]}"
        ts = now if now is not None else time.time()
        record = {
            "id": col_id,
            "sym_a": sym_a,
            "sym_b": sym_b,
            "verdict": verdict,
            "path_text": path_text,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "timestamp": ts
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
                    "agent_a": agent_a,
                    "agent_b": agent_b,
                    "timestamp": ts
                })
            except Exception:
                pass

    def get_precedent(self, sym_a: str, sym_b: str, before_time: Optional[float] = None) -> Dict[str, Any]:
        """Return count and last collision timestamp for this pair of symbols (order-independent)."""
        cutoff = before_time if before_time is not None else float("inf")
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_PRECEDENT_QUERY, {"sym_a": sym_a, "sym_b": sym_b, "before_time": cutoff})
                if res.result_set and len(res.result_set) > 0:
                    count = res.result_set[0][0] or 0
                    last_seen = res.result_set[0][1]
                    return {"count": count, "last_seen": last_seen}
            except Exception:
                pass

        # In-memory precedent lookup (order-independent)
        cnt = 0
        last_seen = None
        for col in self._mem_collisions:
            if col["timestamp"] >= cutoff:
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
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        claims: List[Dict[str, Any]] = []
        blocked_by: List[Dict[str, Any]] = []

        if self.is_connected and self.graph:
            try:
                # 1. Symbols
                sym_res = self.graph.query(GET_CODE_GRAPH_SYMBOLS_QUERY)
                for row in sym_res.result_set:
                    nodes.append({
                        "id": row[0],
                        "name": row[1],
                        "file": row[2],
                        "kind": row[3] or "function"
                    })

                # 2. CALLS Edges
                calls_res = self.graph.query(GET_CODE_GRAPH_CALLS_QUERY)
                for row in calls_res.result_set:
                    edges.append({
                        "from": row[0],
                        "to": row[1],
                        "type": "CALLS"
                    })

                # 3. Claims
                claim_res = self.graph.query(GET_ALL_CLAIMS_FOR_DASHBOARD_QUERY)
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

                return {
                    "nodes": nodes,
                    "edges": edges,
                    "claims": claims,
                    "blocked_by": blocked_by
                }
            except Exception:
                pass

        # In-memory fallback
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
            claims.append(dict(c))

        return {
            "nodes": nodes,
            "edges": edges,
            "claims": claims,
            "blocked_by": blocked_by
        }

    def get_recent_collisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent collision records ordered chronologically descending."""
        if self.is_connected and self.graph:
            try:
                res = self.graph.query(GET_ALL_COLLISIONS_QUERY)
                results = []
                for row in res.result_set[:limit]:
                    results.append({
                        "id": row[0],
                        "verdict": row[1],
                        "path_text": row[2],
                        "timestamp": row[3],
                        "agent_a": row[4],
                        "agent_b": row[5]
                    })
                return results
            except Exception:
                pass

        # In-memory sorted descending
        sorted_cols = sorted(self._mem_collisions, key=lambda c: c.get("timestamp", 0), reverse=True)
        return sorted_cols[:limit]

    def reset_demo_state(self) -> Dict[str, Any]:
        """Reset only transient Claims and Collisions for repeated demo recordings."""
        if self.is_connected and self.graph:
            try:
                self.graph.query(RESET_DEMO_CLAIMS_QUERY)
                self.graph.query(RESET_DEMO_COLLISIONS_QUERY)
            except Exception:
                pass

        self._mem_claims.clear()
        self._mem_collisions.clear()
        self._mem_edges = [e for e in self._mem_edges if e.get("type") in ("CONTAINS", "CALLS")]
        return {"status": "ok", "message": "Demo state reset: claims and collisions cleared, code graph preserved."}
