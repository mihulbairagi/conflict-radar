# Deployment Guide: Single Container Deployment

This guide documents how to build and run **Conflict Radar** inside a self-contained, single Docker container combining FalkorDB and the FastAPI live dashboard.

---

## 1. Architecture Overview

- **Base Image**: Pinned by digest (see Dockerfile).
- **Internal FalkorDB**: Starts in the background listening strictly on loopback interface `127.0.0.1:6379`. Port `6379` is **not published** to the host, protecting raw database queries and mutation commands from external access.
- **FastAPI Dashboard**: Runs on `0.0.0.0:${PORT:-8000}` via `uvicorn dashboard:app`, serving the Cytoscape.js interactive graph, SSE events stream, and demo triggers.
- **Auto-Indexing**: On container boot, `./sample_repo` is automatically indexed into FalkorDB before the dashboard accepts connections (generating 9 symbols and 4 call edges).

---

## 2. Prerequisites

- Docker Engine (Desktop or CLI) installed and running.

---

## 3. Build & Run Commands

### Build Image
```bash
docker build -t conflict-radar .
```
- **Image Size**: ~914 MB uncompressed (content size: ~235 MB).
- **Build Time**: ~45-60s on standard connections.

### Run Locally (Default Port 8000)
```bash
docker run --rm -p 8000:8000 --name conflict-radar conflict-radar
```

### Run on Custom Port via `$PORT`
To bind the container to a custom port (e.g. 8080, or on platforms that inject a PORT environment variable -- not yet deployed on any host):
```bash
docker run --rm -e PORT=8080 -p 8080:8080 --name conflict-radar conflict-radar
```

---

## 4. Verification Endpoints

When the container is running:
1. **Interactive Dashboard**: Open `http://localhost:8000/` in a browser.
2. **Graph Structure**: `GET http://localhost:8000/graph` returns JSON containing `nodes` (9 symbols) and `edges` (4 calls).
3. **Trigger Scenario 3 (BLOCK & Auto-promote)**: `POST http://localhost:8000/demo/scenario/3`
4. **Live Event Stream (SSE)**: `GET http://localhost:8000/events` streams real-time updates as claims transition between ACTIVE, WAITING, and DONE.
5. **Reset State**: `POST http://localhost:8000/demo/reset` clears transient claims and collisions while preserving the indexed code graph.
6. **Port Security**: Connecting to `localhost:6379` from the host will fail / time out, confirming port 6379 is not exposed.

---

## 5. Verified Startup Sequence

1. `start.sh` creates FalkorDB storage directory at `/var/lib/falkordb/data`.
2. Starts `redis-server --bind 127.0.0.1 --protected-mode yes --loadmodule ...`.
3. Polls `redis-cli -h 127.0.0.1 ping` until `PONG` is returned (typically <= 1s, timeout: 30s).
4. Runs `python indexer.py ./sample_repo`, populating the graph.
5. Launches `uvicorn dashboard:app --host 0.0.0.0 --port ${PORT:-8000}` as PID 1.

---

## 6. Known Limitations & Cloud Hosting Caveats

- **Interactive Playground Scope**: The container pre-loads `./sample_repo` (9 symbols, 4 call edges) to serve as a fast interactive playground for the 4 coordination scenarios. Indexing a real repository (psf/requests, 283 symbols, 225 call edges) and the semantic-conflict demo run locally via `python scripts/semantic_conflict_demo.py`.
- **Shared Visitor State**: Dashboard state is global and shared across all active viewers. If two judges or users access the live dashboard concurrently, triggering a scenario or clicking reset immediately mutates the state for both viewers via SSE.
- **Unauthenticated Demo Endpoints**: The endpoints `/demo/scenario/{1-4}` and `/demo/reset` have no authentication. Anyone with network access to the dashboard URL can reset or trigger scenarios.
- **Free Cloud Host Spin-down (Cold Wake-Up)**: Container startup takes about 35 s (measured under 512 MB / 0.1 CPU). On free hosts that spin down when idle, the host's own wake-up time is added on top of that.
- **Single Container Architecture**: Designed for local development and single-instance cloud demos on platforms that inject a PORT environment variable (not yet deployed on any host). Scaling across multiple container instances would require an external FalkorDB cluster.
- **Ephemeral Storage**: FalkorDB runs entirely inside the container. All data is reset and re-indexed on container start.
- **No TLS / Auth in `graph_db.py`**: Connections between `dashboard.py` and FalkorDB occur over plain TCP on localhost `127.0.0.1:6379`.


---

## 7. Resource Benchmarks under Free-Tier Constraints (512 MB RAM / 0.1 CPU)

Measured by executing `docker run --rm --memory=512m --memory-swap=512m --cpus=0.1 -p 8000:8000 conflict-radar`:

- **Startup Time (Container Start $\to$ `GET /` HTTP 200):** 35.49 s (polled once per second; well under 120 s limit).
- **Idle Memory Usage:** 72.35 MiB / 512 MiB (14.13% of budget; >439 MiB headroom).
- **Peak Memory Usage (after 3x full Scenarios 1-4 load with resets):** 83.05 MiB / 512 MiB (16.22% of budget).
- **OOMKilled:** `False` (Container state exit code: 0).
- **Scenario 3 Latency under 0.1 CPU:** 29.58 ms average across 5 consecutive runs (15.4 ms, 17.1 ms, 13.7 ms, 75.8 ms, 26.0 ms).

