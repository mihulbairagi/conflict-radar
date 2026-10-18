#!/bin/bash
set -e

echo "[start.sh] Initializing FalkorDB storage..."
mkdir -p /var/lib/falkordb/data

echo "[start.sh] Starting FalkorDB on 127.0.0.1:6379 (loopback only)..."
redis-server \
    --bind 127.0.0.1 \
    --protected-mode yes \
    --dir /var/lib/falkordb/data \
    --loadmodule /var/lib/falkordb/bin/falkordb.so \
    MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000 &
FALKOR_PID=$!

echo "[start.sh] Waiting for FalkorDB to be ready..."
TIMEOUT=30
ELAPSED=0
until redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q "PONG"; do
    if ! kill -0 "$FALKOR_PID" 2>/dev/null; then
        echo "[start.sh] ERROR: FalkorDB process died unexpectedly during startup." >&2
        exit 1
    fi
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "[start.sh] ERROR: Timed out waiting for FalkorDB to start after ${TIMEOUT}s." >&2
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

echo "[start.sh] FalkorDB ready (ping returned PONG in ${ELAPSED}s)."

echo "[start.sh] Indexing ./sample_repo into FalkorDB..."
python indexer.py ./sample_repo
echo "[start.sh] Indexing complete."

PORT="${PORT:-8000}"
echo "[start.sh] Launching FastAPI dashboard on 0.0.0.0:${PORT}..."
uvicorn dashboard:app --host 0.0.0.0 --port "$PORT" &
UVICORN_PID=$!

cleanup() {
    echo "[start.sh] Received shutdown signal. Terminating children..."
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    kill -TERM "$FALKOR_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
    wait "$FALKOR_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Process supervision: wait for any child to exit
wait -n "$FALKOR_PID" "$UVICORN_PID" || true

echo "[start.sh] CRITICAL: A supervised process exited. Terminating remaining services..." >&2
kill -TERM "$UVICORN_PID" 2>/dev/null || true
kill -TERM "$FALKOR_PID" 2>/dev/null || true
wait "$UVICORN_PID" 2>/dev/null || true
wait "$FALKOR_PID" 2>/dev/null || true

exit 1
