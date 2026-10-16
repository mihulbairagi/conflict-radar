"""tests/test_dashboard.py - Pytest suite for Step 6 Dashboard API.

Tests FastAPI endpoints:
1. GET /: Serves index.html UI
2. GET /graph: Returns nodes, edges, claims, blocked_by
3. POST /demo/reset: Clears claims & collisions while preserving code symbols & edges
4. GET /events: SSE streaming endpoint yields valid event stream
"""

import pytest
from fastapi.testclient import TestClient
import dashboard
from dashboard import app
from graph_db import GraphDB


@pytest.fixture
def client_fixture():
    """Setup test fixture with seeded code graph and TestClient."""
    dashboard.db = GraphDB(strict=False)
    dashboard.db.connect()
    dashboard.db.clear()

    # Seed code symbols
    dashboard.db.upsert_symbol("pkg/mod.py::func_a", "func_a", "pkg/mod.py", "function")
    dashboard.db.upsert_symbol("pkg/mod.py::func_b", "func_b", "pkg/mod.py", "function")
    dashboard.db.add_calls("pkg/mod.py::func_a", "pkg/mod.py::func_b")

    client = TestClient(app)
    yield client

    dashboard.db.clear()


def test_serve_index_html(client_fixture):
    """Test that GET / returns the HTML dashboard."""
    client = client_fixture
    response = client.get("/")
    assert response.status_code == 200
    assert "CONFLICT RADAR" in response.text
    assert "cytoscape" in response.text


def test_get_graph_endpoint(client_fixture):
    """Test GET /graph returns symbols, CALLS edges, claims, and blocked_by."""
    client = client_fixture

    # Add a claim
    cid = dashboard.db.add_claim("agent_1", "pkg/mod.py::func_a", "SIGNATURE", status="active")

    response = client.get("/graph")
    assert response.status_code == 200
    data = response.json()

    assert "nodes" in data
    assert "edges" in data
    assert "claims" in data
    assert "blocked_by" in data

    # Verify nodes and edges
    node_ids = [n["id"] for n in data["nodes"]]
    assert "pkg/mod.py::func_a" in node_ids
    assert "pkg/mod.py::func_b" in node_ids

    # Verify claim presence
    claim_ids = [c["id"] for c in data["claims"]]
    assert cid in claim_ids


def test_demo_reset_preserves_code_graph(client_fixture):
    """Test POST /demo/reset clears claims and collisions but preserves code graph."""
    client = client_fixture

    # Add claims and collisions
    cid1 = dashboard.db.add_claim("agent_1", "pkg/mod.py::func_b", "SIGNATURE", status="active")
    cid2 = dashboard.db.add_claim("agent_2", "pkg/mod.py::func_a", "BODY", status="waiting")
    dashboard.db.add_blocked_by(cid2, cid1)
    dashboard.db.record_collision("pkg/mod.py::func_a", "pkg/mod.py::func_b", "BLOCK", "func_a -> func_b")

    # Verify graph has claims
    graph_before = client.get("/graph").json()
    assert len(graph_before["claims"]) == 2

    # Call reset
    reset_resp = client.post("/demo/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "ok"

    # Verify graph after reset: claims empty, code symbols intact
    graph_after = client.get("/graph").json()
    assert len(graph_after["claims"]) == 0
    assert len(graph_after["blocked_by"]) == 0
    assert len(graph_after["nodes"]) >= 2
    assert len(graph_after["edges"]) >= 1


def test_events_sse_stream(client_fixture):
    """Test GET /events connects and streams SSE events."""
    client = client_fixture

    with client.stream("GET", "/events?limit=1") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Read the initial event from the stream
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert "init" in line or "Connected" in line
                break


def test_get_queries_endpoint(client_fixture):
    """Test GET /queries exposes real Cypher queries, explanations, and schema labels."""
    client = client_fixture
    response = client.get("/queries")
    assert response.status_code == 200
    data = response.json()

    assert "queries" in data
    assert "recent_execution" in data
    assert "telemetry" in data

    templates = {q["id"]: q for q in data["queries"]}
    assert "path_search" in templates
    assert "claim_create" in templates
    assert templates["claim_create"]["name"] == "Claim registration"
    assert "blocked_by_edge" in templates
    assert "collision_record" in templates
    assert "precedent_lookup" in templates

    # Verify real labels and relationships
    assert ":Symbol" in templates["path_search"]["cypher"]
    assert "CALLS" in templates["path_search"]["cypher"]
    assert ":Claim" in templates["claim_create"]["cypher"]
    assert "BLOCKED_BY" in templates["blocked_by_edge"]["cypher"]
    assert ":Collision" in templates["collision_record"]["cypher"]
    assert "INVOLVED" in templates["collision_record"]["cypher"]

    # Verify telemetry structure
    assert "active_claims_count" in data["telemetry"]
    assert "waiting_claims_count" in data["telemetry"]
    assert "collisions_recorded" in data["telemetry"]


def test_demo_scenario_endpoints(client_fixture):
    """Test POST /demo/scenario/{n} runs scenarios 1-4 through real radar and database."""
    client = client_fixture

    # Seed required symbols and call chain: handler.py::parse_request -> validator.py::validate_input -> utils.py::normalize
    dashboard.db.upsert_symbol("handler.py::parse_request", "parse_request", "handler.py", "function")
    dashboard.db.upsert_symbol("validator.py::validate_input", "validate_input", "validator.py", "function")
    dashboard.db.upsert_symbol("utils.py::normalize", "normalize", "utils.py", "function")
    dashboard.db.upsert_symbol("billing.py::compute_tax", "compute_tax", "billing.py", "function")

    dashboard.db.add_calls("handler.py::parse_request", "validator.py::validate_input")
    dashboard.db.add_calls("validator.py::validate_input", "utils.py::normalize")

    # Scenario 1: SAFE
    resp1 = client.post("/demo/scenario/1")
    assert resp1.status_code == 200
    d1 = resp1.json()
    assert d1["scenario"] == 1
    assert any(v["verdict"] == "SAFE" for v in d1["verdicts"])

    # Scenario 2: WARN
    resp2 = client.post("/demo/scenario/2")
    assert resp2.status_code == 200
    d2 = resp2.json()
    assert d2["scenario"] == 2
    assert any(v["verdict"] == "WARN" for v in d2["verdicts"])

    # Scenario 3: BLOCK & UNBLOCK
    resp3 = client.post("/demo/scenario/3")
    assert resp3.status_code == 200
    d3 = resp3.json()
    assert d3["scenario"] == 3
    assert any(v["verdict"] == "BLOCK" for v in d3["verdicts"])

    # Scenario 4: PRECEDENT
    resp4 = client.post("/demo/scenario/4")
    assert resp4.status_code == 200
    d4 = resp4.json()
    assert d4["scenario"] == 4
    assert any(v.get("precedent_count", 0) >= 1 for v in d4["verdicts"])

    # Invalid scenario ID returns 400
    resp_inv = client.post("/demo/scenario/99")
    assert resp_inv.status_code == 400
