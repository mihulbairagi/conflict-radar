"""dashboard.py - Real-Time Visual Traffic Control Dashboard for Conflict Radar.

FastAPI application providing:
1. GET /: Serves interactive Cytoscape.js visualization.
2. GET /graph: Returns nodes, CALLS edges, claims with statuses, and BLOCKED_BY edges.
3. GET /events: Server-Sent Events (SSE) polling FalkorDB and streaming updates when claims
   are created, change status, or collisions occur.
4. POST /demo/reset: Clears all transient Claims and Collisions while preserving the code graph.
"""

import os
import json
import asyncio
from typing import Dict, Any, List, Set, Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from graph_db import GraphDB

app = FastAPI(title="Conflict Radar Live Dashboard")

# Initialize shared database connection in strict mode (reads FalkorDB directly)
db = GraphDB(strict=True)
try:
    db.connect()
except ConnectionError as e:
    import sys
    print(f"FATAL: Conflict Radar Dashboard requires running FalkorDB: {e}", file=sys.stderr)

# Ensure static directory exists
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static folder
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the single-page Cytoscape.js dashboard."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Conflict Radar Dashboard</h1><p>index.html not found.</p>")


@app.get("/graph")
async def get_graph() -> Dict[str, Any]:
    """Return JSON symbols (nodes), CALLS relations (edges), claims, and BLOCKED_BY relationships."""
    return db.get_full_graph()


@app.post("/demo/reset")
async def reset_demo() -> Dict[str, Any]:
    """Wipe transient Claims and Collisions to record repeated demo takes without re-indexing code."""
    return db.reset_demo_state()


@app.get("/events")
async def sse_events(request: Request, limit: Optional[int] = None):
    """Server-Sent Events (SSE) streaming live updates when claims mutate or collisions are logged."""

    async def event_generator():
        # Track state across poll cycles to push delta events
        known_claims: Dict[str, str] = {}  # claim_id -> status
        known_collisions: Set[str] = set()

        # Initial seed of existing claims and collisions so we don't spam initial history as new alerts
        initial_graph = db.get_full_graph()
        for c in initial_graph.get("claims", []):
            known_claims[c["id"]] = c["status"]

        for col in db.get_recent_collisions(limit=100):
            known_collisions.add(col["id"])

        # Send initial connected ping
        yield f"data: {json.dumps({'type': 'init', 'message': 'Connected to Conflict Radar event stream'})}\n\n"

        cycles = 0
        while True:
            # If client disconnected or test cycle limit reached, exit cleanly
            if await request.is_disconnected():
                break
            if limit is not None and cycles >= limit:
                break
            cycles += 1

            try:
                # 1. Check Claims for additions or status transitions (ACTIVE, WAITING, DONE, EXPIRED)
                current_graph = db.get_full_graph()
                for c in current_graph.get("claims", []):
                    c_id = c["id"]
                    c_status = c["status"]
                    old_status = known_claims.get(c_id)

                    if old_status is None:
                        # Newly created claim
                        known_claims[c_id] = c_status
                        event_payload = {
                            "type": "claim_created",
                            "claim_id": c_id,
                            "agent_id": c["agent_id"],
                            "symbol_id": c["symbol_id"],
                            "change_type": c["change_type"],
                            "status": c_status,
                            "blocked_by": c.get("blocked_by")
                        }
                        yield f"data: {json.dumps(event_payload)}\n\n"

                    elif old_status != c_status:
                        # Status mutation (e.g. WAITING -> ACTIVE on unblock, or ACTIVE -> EXPIRED)
                        known_claims[c_id] = c_status
                        event_payload = {
                            "type": "claim_status_changed",
                            "claim_id": c_id,
                            "agent_id": c["agent_id"],
                            "symbol_id": c["symbol_id"],
                            "old_status": old_status,
                            "status": c_status,
                            "blocked_by": c.get("blocked_by")
                        }
                        yield f"data: {json.dumps(event_payload)}\n\n"

                # 2. Check Collisions for new BLOCK or WARN occurrences
                recent_collisions = db.get_recent_collisions(limit=10)
                for col in reversed(recent_collisions):
                    col_id = col["id"]
                    if col_id not in known_collisions:
                        known_collisions.add(col_id)
                        path_parts = col["path_text"].split(" -> ") if col.get("path_text") else []
                        hops = max(0, len(path_parts) - 1)

                        sym_a = col.get("sym_a") or (path_parts[0] if path_parts else "")
                        sym_b = col.get("sym_b") or (path_parts[-1] if path_parts else "")
                        prec = db.get_precedent(sym_a, sym_b, before_time=col.get("timestamp"))
                        p_count = prec.get("count", 0)

                        event_payload = {
                            "type": "collision",
                            "collision_id": col_id,
                            "verdict": col["verdict"],
                            "agents": [col.get("agent_a", "agent_1"), col.get("agent_b", "agent_2")],
                            "path": path_parts,
                            "hops": hops,
                            "reason": f"{col['verdict']} collision: Agent '{col.get('agent_a')}' and Agent '{col.get('agent_b')}' along path {col['path_text']}",
                            "precedent_count": p_count,
                            "timestamp": col["timestamp"]
                        }
                        yield f"data: {json.dumps(event_payload)}\n\n"

            except Exception as e:
                # Keep stream alive despite any transient query interruption
                pass

            # Poll frequency: 1 second interval
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


if __name__ == "__main__":
    import uvicorn
    print("Conflict Radar Live Dashboard starting on http://localhost:8000...")
    uvicorn.run("dashboard:app", host="0.0.0.0", port=8000, reload=True)
