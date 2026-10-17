# Conflict Radar - Video Demo Script (~2:50)

**Hackathon Track:** WeMakeDevs x FalkorDB Graph Hacks (2026)  
**Track Focus:** Track 01 - Multi-Hop Reasoning, MCP, and Explainable Results  
**Presenter:** Mihul Bairagi  
**Target Duration:** ~2 minutes 50 seconds (Strictly under 3:00)  
**Format:** Screen recording (1080p, 60fps) with voiceover / webcam.

---

## Pre-Recording Checklist
1. Docker container `conflict-radar-falkordb` is running: `docker compose up -d`.
2. Dashboard running: `uvicorn dashboard:app --host 0.0.0.0 --port 8000`.
3. Browser open at `http://localhost:8000` (Chrome/Edge in 1920x1080 full window, zoom 100%).
4. Split screen setup ready: Two agent windows side-by-side or prepared terminal clip.
5. README open in browser tab showing `psf/requests` benchmark table.
6. Microphone audio tested.

---

## Timeline & Scene-by-Scene Script

| Time | Screen / Visual | Spoken Script (English) | Spoken Script (Hinglish Option) |
|---|---|---|---|
| **0:00 - 0:15** | Title / Repo Header | *"I'm Mihul. AI coding agents now edit the same codebase in parallel, but they cannot see each other. Git finds the clash hours later. Conflict Radar finds it before the edit."* | *"Main hoon Mihul. Aaj AI coding agents ek hi codebase par parallel kaam karte hain, par wo ek doosre ko dekh nahi sakte. Git clash ko ghanton baad pakadta hai. Conflict Radar edit se pehle hi pakad leta hai."* |
| **0:15 - 0:35** | Dashboard Graph (`localhost:8000`) | *"FalkorDB holds the call graph: functions are nodes, calls are edges, and an agent’s intent to edit is a Claim node attached to a function. Agents talk to it through three MCP tools: claim, check, release."* | *"FalkorDB call graph hold karta hai: functions nodes hain, calls edges hain, aur agent ka edit intent ek Claim node hai jo function se judta hai. Agents 3 MCP tools ke through communicate karte hain: claim, check, release."* |
| **0:35 - 1:25** | **Split screen:** Two Antigravity agent sessions + Dashboard (Real Clip) | *"These are two separate Antigravity agent sessions. I gave both a rule to call claim before editing. Agent 1 claims normalize, body change: SAFE. Agent 2 wants to change the signature of parse_request, two hops upstream. Blocked. Waiting. Agent 1 releases, and Agent 2 is promoted."* *(Show tool-call outputs on screen)* | *"Ye do separate Antigravity agent sessions hain. Maine dono ko edit se pehle claim call karne ka rule diya. Agent 1 normalize claim karta hai (body change): SAFE. Agent 2 do hops door parse_request ka signature change karna chahta hai: Blocked. Waiting. Agent 1 release karta hai, aur Agent 2 promote ho jata hai."* |
| **1:25 - 1:50** | Query Inspector Panel | *"Why was it blocked? The graph answers with one Cypher shortestPath query. Here is the exact path: parse_request, validate_input, normalize. The agent gets the path and the blocker, not just a no."* | *"Agent kyun block hua? Graph ek single Cypher shortestPath query se jawab deta hai. Ye raha exact path: parse_request, validate_input, normalize. Agent ko sirf inkar nahi milta, poora path aur blocker milta hai."* |
| **1:50 - 2:15** | Dashboard Scenario Buttons (Labelled) | *"The dashboard buttons run pre-scripted scenarios: independent changes are SAFE, body-only changes on a shared path WARN, and repeated conflicts show precedent, ‘seen N times before’. That memory is recorded history, not learning."* | *"Dashboard buttons pre-scripted scenarios run karte hain: independent changes SAFE hain, shared path par body-only changes WARN hain, aur repeated conflicts precedent dikhate hain: 'seen N times before'. Ye memory recorded history hai, learning nahi."* |
| **2:15 - 2:40** | README "Tested on psf/requests" Table | *"On the real psf/requests library: 283 symbols, 225 call edges, p95 about one millisecond on 2 and 3 hop queries. I measured edge recall by hand: 52 percent at first, because self-method calls were missed. After fixing that, 90 percent on a separate holdout. Remaining misses are calls on instance variables, which would need type inference."* | *"Real psf/requests library par: 283 symbols, 225 call edges, 2 aur 3 hop queries par p95 lagbhag 1 ms. Maine edge recall haath se naapa: pehle 52 percent tha kyunki self-method calls miss ho rahe the. Use fix karne ke baad holdout par 90 percent recall mila. Baaki bache misses instance variables par hain jinko type inference chahiye."* |
| **2:40 - 2:50** | README Known Limitations | *"Hub functions with many callers can block many symbols, and the Redis lock only works on a single instance. All of this is in the README. Thank you."* | *"Hub functions jinhe bohot saare callers call karte hain wo kaafi symbols block kar sakte hain, aur Redis lock single instance par chalta hai. Ye sab README me documented hai. Thank you."* |

---

## Key Speaking Guidelines
- **Strictly Avoid Fluff Words:** Do not say "production-ready", "zero friction", "lightning fast", or "agents learn". Stick strictly to measured numbers and tested behaviors.
- **Clarify Scripted vs Real:** Explicitly introduce the scenario buttons (1:50 - 2:15) as "pre-scripted scenarios", preserving total transparency for Rule 13 compliance.
