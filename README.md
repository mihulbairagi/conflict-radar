# Conflict Radar

A real-time traffic controller for AI coding agents working on the same codebase.
Before an agent modifies code, it declares its intent. Conflict Radar queries a FalkorDB call graph to determine if changes conflict with other active agents, returning SAFE, WARN, or BLOCK with the full dependency evidence path.

## Requirements
- Python 3.11+
- Docker & Docker Compose
- Dependencies: `falkordb`, `mcp`

Install Python dependencies:
```bash
pip install falkordb mcp
```

## Running the Components

### 1. Start FalkorDB
Launch the FalkorDB graph database on port 6379:
```bash
docker compose up -d
```

### 2. Index a Codebase
Parse a Python repository and load its call graph into FalkorDB:
```bash
python indexer.py ./path/to/python/repo
```
The indexer uses Python's `ast` module to extract files, functions, classes, and call relationships. All writes use Cypher `MERGE`, making re-runs completely idempotent.

### 3. Run the MCP Server
Start the MCP server over standard I/O for AI agents:
```bash
python mcp_server.py
```
Currently exposes the `ping` tool to verify agent connectivity.
