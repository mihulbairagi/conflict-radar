# Conflict Radar - 3-Minute Video Demo Script

**Hackathon Track:** WeMakeDevs x FalkorDB Graph Hacks (2026)  
**Presenter:** Mihul Bairagi  
**Target Duration:** 2 minutes 45 seconds to 3 minutes  
**Format:** Screen recording (1080p, 60fps) with picture-in-picture webcam or voiceover.

---

## Pre-Recording Checklist
1. Docker container `conflict-radar-falkordb` is running: `docker compose up -d`.
2. Dashboard running: `uvicorn dashboard:app --host 0.0.0.0 --port 8000`.
3. Browser open at `http://localhost:8000` (Chrome/Edge in 1920x1080 full window, zoom 100%).
4. Terminal open side-by-side or ready to switch: `python demo.py`.
5. Microphone and desktop audio tested.

---

## Section 1: The Hook & The Problem (0:00 - 0:35)

| Time | Visual / Screen | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **0:00 - 0:15** | Facecam + Title Slide or GitHub Repo Header | *"Hi everyone, I'm Mihul. In 2026, autonomous AI coding agents like Claude Code, Cursor, and Devin are writing software in parallel. But they have a major blind spot: silent code collisions."* | *"Hello everyone, main hoon Mihul. Aaj AI coding agents parallel me software develop kar rahe hain, lekin unke beech me ek bohot bada blind spot hai: silent code collisions."* |
| **0:15 - 0:35** | Graphic or split screen of git conflict vs Conflict Radar | *"Git merge conflicts are detected hours later at PR time, wasting thousands of LLM tokens. File-level locks are too blunt because agents can safely edit independent functions in the same file. To solve this, I built **Conflict Radar**—a real-time pre-commit traffic controller and call-graph query engine powered by FalkorDB."* | *"Git conflicts PR ke time aate hain jo bohot late hota hai, jisse tokens aur time dono waste hote hain. File lock bekaar hai kyunki do agents ek hi file ke alag functions pe kaam kar sakte hain. Iska solution hai **Conflict Radar**—ek real-time pre-commit traffic controller jo FalkorDB graph par chalta hai."* |

---

## Section 2: Architecture & Live Dashboard Tour (0:35 - 1:05)

| Time | Visual / Screen | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **0:35 - 1:05** | Browser showing `http://localhost:8000` (Cytoscape Graph) | *"Here is our live visual dashboard. Using Python AST parsing, Conflict Radar maps entire codebases into FalkorDB as a directed call graph. Functions are `Symbol` nodes connected by `CALLS` edges. Locks are fine-grained `Claim` nodes that attach directly to symbols, rather than locking entire files. Let's see it in action."* | *"Ye hai hamara live dashboard. Python AST se hum code ko FalkorDB me directed graph banate hain. Functions `Symbol` nodes hain aur relationships `CALLS` edges hain. Locks fine-grained `Claim` nodes hain jo functions se judte hain na ki poori file se. Chaliye ise live action me dekhte hain."* |

---

## Section 3: Live Demo - The 4 Scripted Scenarios (1:05 - 2:15)

| Time | Visual / Screen | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **1:05 - 1:25** | Run `python demo.py` (Scripted Scenario 1: SAFE) | *"In scripted Scenario 1, Agent 1 edits `billing.compute_tax` while Agent 2 edits `handler.parse_request`. Because they belong to disjoint call chains, FalkorDB instantly returns **SAFE**. Both agents work in parallel with zero friction."* | *"Scripted Scenario 1 me Agent 1 billing function edit karta hai aur Agent 2 request handler. Dono ke call-chains bilkul alag hain, isliye FalkorDB turant **SAFE** verdict deta hai aur dono parallel kaam karte hain."* |
| **1:25 - 1:45** | Terminal + Dashboard (Scripted Scenario 2: WARN) | *"In scripted Scenario 2, both agents modify internal function bodies two hops apart. The graph highlights the dependency path in red: `parse_request -> validate_input -> normalize`. Because function signatures remain intact, Radar issues a non-blocking **WARN**."* | *"Scripted Scenario 2 me dono agents 2 hops door body changes karte hain. Dashboard path ko red me highlight karta hai. Signatures safe hain, isliye Radar non-blocking **WARN** deta hai."* |
| **1:45 - 2:05** | Terminal + Dashboard (Scripted Scenario 3: BLOCK & Auto-Promotion) | *"Now, scripted Scenario 3: The Killer Feature. Agent 1 is modifying `normalize`. Agent 2 attempts a breaking **SIGNATURE** change on `parse_request`. Radar detects caller risk: Agent 2 is **BLOCKED** and placed in `WAITING` status (amber node). Watch what happens when Agent 1 finishes and calls `release()`... instantly, FalkorDB removes the blocking edge and promotes Agent 2 to `ACTIVE` in real-time!"* | *"Ab dekhiye scripted Scenario 3: Agent 1 `normalize` edit kar raha hai, aur Agent 2 `parse_request` ka **SIGNATURE** badal raha hai. Radar risk detect karta hai aur Agent 2 ko **BLOCKED** karke amber `WAITING` me daal deta hai. Aur jaise hi Agent 1 release karta hai, FalkorDB live SSE stream ke through Agent 2 ko instantly `ACTIVE` promote kar deta hai!"* |
| **2:05 - 2:20** | Terminal + Dashboard (Scripted Scenario 4: PRECEDENT) | *"Finally, scripted Scenario 4 shows **Graph Memory**. Every collision creates a persistent `:Collision` node linked via symmetric `[:INVOLVED]` edges. When the same symbols conflict again, Radar looks up historical precedent, reporting: 'Seen 2 times before'."* | *"Scripted Scenario 4 dikhata hai **Graph Memory**. Har collision FalkorDB me `:Collision` node banata hai. Jab wahi functions dubara ladte hain, Radar purana record dekh kar batata hai: 'Seen 2 times before'."* |

---

## Section 4: FalkorDB Power & Scale Benchmarks (2:20 - 2:45)

| Time | Visual / Screen | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **2:20 - 2:45** | Terminal showing `scripts/benchmark_scale.py` or README Cypher section | *"Under the hood, all collision detection runs in FalkorDB using Cypher `shortestPath` queries. We indexed this project's own 156 symbols: indexing latency was 539 milliseconds, and bidirectional path queries consistently clocked in at **2.27 milliseconds p95**."* | *"Under the hood, saari detection FalkorDB ki Cypher `shortestPath` query se hoti hai. Humne is project ke apne 156 symbols index kiye: indexing latency 539 ms thi, aur bidirectional path queries **2.27 ms p95** latency pe run hui."* |

---

## Section 5: Standard MCP & Conclusion (2:45 - 3:00)

| Time | Visual / Screen | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **2:45 - 3:00** | VS Code / GitHub repo showing `.mcp.json` + Facecam | *"Conflict Radar runs as a standard Model Context Protocol (MCP) server. Any MCP client can connect via a standard stdio MCP config. That is Conflict Radar: stopping multi-agent collisions before they happen, on FalkorDB. Thank you!"* | *"Conflict Radar standard Model Context Protocol (MCP) server ke roop me chalta hai. Koi bhi MCP client standard stdio MCP config se connect ho sakta hai. Ye tha Conflict Radar: AI agents ke collisions pre-commit rokna FalkorDB ke sath. Thank you!"* |
