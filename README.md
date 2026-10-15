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

### 4. Connecting AI Coding Agents (Claude Code / Antigravity)

Add Conflict Radar to your agent's MCP configuration file (e.g. `claude_desktop_config.json` or your project `.mcp.json`):

```json
{
  "mcpServers": {
    "conflict-radar": {
      "command": "python",
      "args": ["mcp_server.py"]
    }
  }
}
```

#### Agent Instruction (Add to agent system prompt or rule file):
```
Before editing any function, call claim with the function id and whether you change its signature or only its body. If the verdict is BLOCK, do not edit; wait and call check later.
```

#### Available MCP Tools:
- `claim(agent_id, symbol_id, change_type)`: Declares intent to edit a symbol. Returns `SAFE` (stored as `ACTIVE`), `WARN` (stored as `ACTIVE`), or `BLOCK` (stored as `WAITING` with a `BLOCKED_BY` edge).
- `check(claim_id)`: Checks the current claim status (`ACTIVE`, `WAITING`, or `DONE`) and indicates which claim is blocking it if waiting.
- `release(claim_id)`: Marks the claim `DONE`, automatically re-evaluates all `WAITING` claims against remaining active locks, and unblocks those that become `SAFE` or `WARN`.
