# Conflict Radar - Project Guidelines for AI Agents

Welcome to Conflict Radar! This repository coordinates multi-agent coding sessions using FalkorDB call-graph analysis.

## Agent Coordination Protocol

When working in this repository, you must coordinate code modifications with peer agents using the configured `conflict-radar` MCP tools:

1. **Pre-Commit Intent Check:**
   Before modifying any symbol (function or class), declare your intent by calling the `claim` tool:
   - `agent_id`: Your unique agent name (e.g., `"claude-agent"`).
   - `symbol_id`: The fully qualified symbol identifier (e.g., `"utils.py::normalize"` or `"handler.py::parse_request"`).
   - `change_type`: `"SIGNATURE"` (if modifying parameters, renaming, or deleting) or `"BODY"` (if updating internal logic only).

2. **Respect Verdicts:**
   - **`SAFE`**: No dependency conflict. Proceed with your edits.
   - **`WARN`**: Transitive body-edit dependency. Proceed with caution.
   - **`BLOCK`**: A conflicting lock is held by a peer agent. **DO NOT** edit the file. Enter a waiting state and periodically call `check(claim_id)` until your status becomes `active`.

3. **Release Lock:**
   - Once your code edits are applied and saved, always call `release(claim_id)` to unblock any waiting peer agents.
