"""radar_core.py - Deterministic Verdict Engine for Conflict Radar.

Evaluates proposed code claims against active claims using FalkorDB call-graph
traversal rules. Zero LLM reliance: verdicts are 100% deterministic and explainable.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from graph_db import GraphDB


@dataclass
class Verdict:
    """Represents the safety verdict and graph evidence for a proposed claim."""
    verdict: str  # "SAFE", "WARN", "BLOCK"
    claim_id: str
    conflicting_claim_id: Optional[str]
    path: List[str] = field(default_factory=list)
    hops: int = 0
    reason: str = ""


class ConflictRadar:
    """The central traffic controller for AI coding agents."""

    def __init__(self, db: GraphDB, max_hops: int = 3):
        self.db = db
        self.max_hops = max_hops

    def _evaluate(self, agent_id: str, symbol_id: str, c_type: str, active_claims: List[Dict[str, Any]]) -> Tuple[str, Optional[str], List[str], int, str]:
        """Internal evaluation of a proposed symbol modification against active claims."""
        for other in active_claims:
            if other["agent_id"] == agent_id:
                continue

            other_id = other["id"]
            other_agent = other["agent_id"]
            other_symbol = other["symbol_id"]
            other_type = other["change_type"].upper()

            # Condition 1: Identical Symbols (0 hops)
            if symbol_id == other_symbol:
                is_signature = (c_type == "SIGNATURE" or other_type == "SIGNATURE")
                v_str = "BLOCK" if is_signature else "WARN"
                path = [symbol_id]
                reason = (f"Direct conflict (0 hops): Agent '{agent_id}' ({c_type}) and "
                          f"Agent '{other_agent}' ({other_type}) are editing the exact same symbol '{symbol_id}'.")
                self.db.record_collision(symbol_id, other_symbol, v_str, f"{symbol_id} (direct)")
                return v_str, other_id, path, 0, reason

            # Condition 2: Multi-Hop Call Path in Either Direction (<= max_hops)
            detected_path = self.db.find_call_path(symbol_id, other_symbol, max_hops=self.max_hops)
            if detected_path:
                hops = len(detected_path) - 1
                is_signature = (c_type == "SIGNATURE" or other_type == "SIGNATURE")
                v_str = "BLOCK" if is_signature else "WARN"
                path_str = " -> ".join(detected_path)
                reason = (f"Dependency conflict ({hops} hops): Agent '{agent_id}' ({c_type}) and "
                          f"Agent '{other_agent}' ({other_type}) along call path {path_str}.")
                self.db.record_collision(symbol_id, other_symbol, v_str, path_str)
                return v_str, other_id, detected_path, hops, reason

        return "SAFE", None, [], 0, f"Safe: No call-chain dependency found between Agent '{agent_id}' and active claims."

    def claim(self, agent_id: str, symbol_id: str, change_type: str = "BODY") -> Verdict:
        """Register a claim and evaluate it against other agents' active claims.
        
        Rule:
        - BLOCK verdict -> stored as WAITING with a (:Claim)-[:BLOCKED_BY]->(:Claim) edge.
        - SAFE / WARN verdict -> stored as ACTIVE.
        - Only ACTIVE claims are compared against new claims.
        """
        c_type = change_type.upper()
        active_claims = self.db.get_active_claims()
        
        v_str, conflicting_id, path, hops, reason = self._evaluate(agent_id, symbol_id, c_type, active_claims)

        status = "waiting" if v_str == "BLOCK" else "active"
        claim_id = self.db.add_claim(agent_id, symbol_id, c_type, status=status)

        if v_str == "BLOCK" and conflicting_id:
            self.db.add_blocked_by(claim_id, conflicting_id)

        return Verdict(
            verdict=v_str,
            claim_id=claim_id,
            conflicting_claim_id=conflicting_id,
            path=path,
            hops=hops,
            reason=reason
        )

    def check(self, claim_id: str) -> Dict[str, Any]:
        """Check current status of a claim and, if WAITING, return which claim blocks it."""
        claim = self.db.get_claim(claim_id)
        if not claim:
            return {"status": "not_found", "claim_id": claim_id}

        res = {
            "claim_id": claim_id,
            "agent_id": claim["agent_id"],
            "symbol_id": claim["symbol_id"],
            "change_type": claim["change_type"],
            "status": claim["status"]
        }
        if claim["status"] == "waiting":
            res["blocked_by"] = claim.get("blocked_by")
        return res

    def release(self, claim_id: str) -> Dict[str, Any]:
        """Release a claim lock and re-evaluate waiting claims.
        
        Waiting claims that are now SAFE or WARN become ACTIVE.
        """
        self.db.release_claim(claim_id)

        unblocked: List[Dict[str, Any]] = []
        waiting_claims = self.db.get_waiting_claims()

        for w in waiting_claims:
            # Active claims excluding the released one
            active_claims = [c for c in self.db.get_active_claims() if c["id"] != claim_id]
            v_str, conf_id, path, hops, _ = self._evaluate(
                w["agent_id"], w["symbol_id"], w["change_type"], active_claims
            )

            # If now SAFE or WARN, promote from WAITING to ACTIVE
            if v_str in ("SAFE", "WARN"):
                self.db.update_claim_status(w["id"], "active")
                self.db.remove_blocked_by(w["id"])
                unblocked.append({
                    "claim_id": w["id"],
                    "agent_id": w["agent_id"],
                    "symbol_id": w["symbol_id"],
                    "new_status": "active",
                    "verdict": v_str
                })

        return {
            "status": "released",
            "claim_id": claim_id,
            "unblocked_claims": unblocked
        }
