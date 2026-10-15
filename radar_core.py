"""radar_core.py - Deterministic Verdict Engine for Conflict Radar.

Evaluates proposed code claims against active claims using FalkorDB call-graph
traversal rules. Zero LLM reliance: verdicts are 100% deterministic and explainable.
Step 5: Includes precedent collision memory and lazy claim expiry with injectable clock.
"""

import os
import time
import difflib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple, Callable
from graph_db import GraphDB


@dataclass
class Verdict:
    """Represents the safety verdict and graph evidence for a proposed claim."""
    verdict: str  # "SAFE", "WARN", "BLOCK", "ERROR"
    claim_id: Optional[str] = None
    conflicting_claim_id: Optional[str] = None
    path: List[str] = field(default_factory=list)
    hops: int = 0
    reason: str = ""
    precedent_count: int = 0
    last_collision_time: Optional[float] = None
    error: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)


class ConflictRadar:
    """The central traffic controller for AI coding agents."""

    def __init__(
        self,
        db: GraphDB,
        max_hops: int = 3,
        ttl_seconds: Optional[int] = None,
        clock: Callable[[], float] = time.time
    ):
        self.db = db
        self.max_hops = max_hops
        self.clock = clock
        if ttl_seconds is not None:
            self.ttl_seconds = ttl_seconds
        else:
            self.ttl_seconds = int(os.environ.get("CLAIM_TTL_SECONDS", 300))

    def _expire_stale(self) -> List[Dict[str, Any]]:
        """Lazy expiry: expire claims older than TTL and unblock waiting claims if active claims expired."""
        now = self.clock()
        cutoff = now - self.ttl_seconds
        expired = self.db.expire_stale_claims(cutoff)
        
        # If any claims were expired, re-evaluate waiting claims
        if expired:
            self._unblock_eligible_waiters()
        return expired

    def _unblock_eligible_waiters(self) -> List[Dict[str, Any]]:
        """Re-evaluate all WAITING claims sequentially against the active claims pool.
        
        If a waiting claim becomes ACTIVE, it is immediately added to the active set
        so that downstream waiting claims are checked against it in the same pass.
        """
        now = self.clock()
        unblocked: List[Dict[str, Any]] = []
        waiting_claims = self.db.get_waiting_claims()
        active_claims = list(self.db.get_active_claims())

        for w in waiting_claims:
            # Re-evaluate against current active pool (pure, side-effect free)
            v_str, conf_id, path, hops, reason, _, _, _, _, _ = self._evaluate(
                w["agent_id"], w["symbol_id"], w["change_type"], active_claims
            )

            if v_str in ("SAFE", "WARN"):
                # Promote to active
                self.db.update_claim_status(w["id"], "active", now=now)
                self.db.remove_blocked_by(w["id"])

                # Add to local active pool for subsequent waiting evaluations
                promoted = dict(w)
                promoted["status"] = "active"
                promoted["updated_at"] = now
                active_claims.append(promoted)

                unblocked.append({
                    "claim_id": w["id"],
                    "agent_id": w["agent_id"],
                    "symbol_id": w["symbol_id"],
                    "new_status": "active",
                    "verdict": v_str
                })
            else:
                # Remains blocked; update blocker if changed
                if conf_id and conf_id != w.get("blocked_by"):
                    self.db.remove_blocked_by(w["id"])
                    self.db.add_blocked_by(w["id"], conf_id)

        return unblocked

    def _evaluate(
        self,
        agent_id: str,
        symbol_id: str,
        c_type: str,
        active_claims: List[Dict[str, Any]]
    ) -> Tuple[str, Optional[str], List[str], int, str, int, Optional[float], Optional[str], Optional[str], str]:
        """Internal pure evaluation of a proposed symbol modification against active claims.
        
        Side-effect free: does NOT record collisions or mutate graph state.
        Returns:
            verdict, conflicting_id, path, hops, reason, precedent_count,
            last_collision_time, other_symbol, other_agent, path_str
        """
        now = self.clock()

        for other in active_claims:
            if other["agent_id"] == agent_id:
                continue

            other_id = other["id"]
            other_agent = other["agent_id"]
            other_symbol = other["symbol_id"]
            other_type = other["change_type"].upper()

            # Check Condition 1: Identical Symbols (0 hops)
            if symbol_id == other_symbol:
                is_signature = (c_type == "SIGNATURE" or other_type == "SIGNATURE")
                v_str = "BLOCK" if is_signature else "WARN"
                path = [symbol_id]

                # Precedent query (strictly BEFORE recording this new collision)
                prec = self.db.get_precedent(symbol_id, other_symbol, before_time=now)
                p_count = prec["count"]
                p_last = prec["last_seen"]

                reason = (f"Direct conflict (0 hops): Agent '{agent_id}' ({c_type}) and "
                          f"Agent '{other_agent}' ({other_type}) are editing the exact same symbol '{symbol_id}'.")
                if p_count > 0 and p_last:
                    time_str = datetime.fromtimestamp(p_last).strftime("%Y-%m-%d %H:%M:%S")
                    reason += f" These symbols have collided {p_count} times before; last on {time_str}."

                path_str = f"{symbol_id} (direct)"
                return v_str, other_id, path, 0, reason, p_count, p_last, other_symbol, other_agent, path_str

            # Check Condition 2: Multi-Hop Call Path in Either Direction (<= max_hops)
            detected_path = self.db.find_call_path(symbol_id, other_symbol, max_hops=self.max_hops)
            if detected_path:
                hops = len(detected_path) - 1
                is_signature = (c_type == "SIGNATURE" or other_type == "SIGNATURE")
                v_str = "BLOCK" if is_signature else "WARN"
                path_str = " -> ".join(detected_path)

                # Precedent query (strictly BEFORE recording this new collision)
                prec = self.db.get_precedent(symbol_id, other_symbol, before_time=now)
                p_count = prec["count"]
                p_last = prec["last_seen"]

                reason = (f"Dependency conflict ({hops} hops): Agent '{agent_id}' ({c_type}) and "
                          f"Agent '{other_agent}' ({other_type}) along call path {path_str}.")
                if p_count > 0 and p_last:
                    time_str = datetime.fromtimestamp(p_last).strftime("%Y-%m-%d %H:%M:%S")
                    reason += f" These symbols have collided {p_count} times before; last on {time_str}."

                return v_str, other_id, detected_path, hops, reason, p_count, p_last, other_symbol, other_agent, path_str

        return "SAFE", None, [], 0, f"Safe: No call-chain dependency found between Agent '{agent_id}' and active claims.", 0, None, None, None, ""

    def claim(self, agent_id: str, symbol_id: str, change_type: str = "BODY") -> Verdict:
        """Register a claim and evaluate it against other agents' active claims."""
        # 1. Validate agent_id
        if not agent_id or not isinstance(agent_id, str) or not agent_id.strip():
            return Verdict(
                verdict="ERROR",
                claim_id=None,
                conflicting_claim_id=None,
                reason="agent_id must be a non-empty string.",
                error="INVALID_AGENT_ID"
            )
        agent_id = agent_id.strip()

        # 2. Validate change_type
        raw_type = (change_type or "").strip().upper() if isinstance(change_type, str) else ""
        if raw_type in ("SIGNATURE", "RENAME", "DELETE"):
            c_type = "SIGNATURE"
        elif raw_type == "BODY":
            c_type = "BODY"
        else:
            return Verdict(
                verdict="ERROR",
                claim_id=None,
                conflicting_claim_id=None,
                reason=f"Invalid change_type '{change_type}'. Allowed values are 'SIGNATURE', 'BODY', 'RENAME', or 'DELETE'.",
                error="INVALID_CHANGE_TYPE"
            )

        # 3. Validate symbol existence
        if not self.db.symbol_exists(symbol_id):
            all_syms = self.db.get_all_symbol_ids()
            suggestions = difflib.get_close_matches(symbol_id, all_syms, n=3, cutoff=0.2)
            if not suggestions and all_syms:
                base = symbol_id.split("::")[-1]
                suggestions = [s for s in all_syms if base in s][:3]
                if not suggestions:
                    suggestions = all_syms[:3]
            sugg_str = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            return Verdict(
                verdict="ERROR",
                claim_id=None,
                conflicting_claim_id=None,
                reason=f"Unknown symbol '{symbol_id}' not found in codebase graph.{sugg_str}",
                error="UNKNOWN_SYMBOL",
                suggestions=suggestions
            )

        self._expire_stale()

        now = self.clock()
        active_claims = self.db.get_active_claims()

        v_str, conflicting_id, path, hops, reason, p_count, p_last, other_sym, other_ag, path_str = self._evaluate(
            agent_id, symbol_id, c_type, active_claims
        )

        status = "waiting" if v_str == "BLOCK" else "active"
        claim_id = self.db.add_claim(agent_id, symbol_id, c_type, status=status, now=now)

        # Record collision EXACTLY ONCE on new claim when verdict is WARN or BLOCK
        if v_str in ("WARN", "BLOCK") and conflicting_id and other_sym:
            self.db.record_collision(
                symbol_id, other_sym, v_str, path_str,
                agent_a=agent_id, agent_b=other_ag, now=now
            )

        if v_str == "BLOCK" and conflicting_id:
            self.db.add_blocked_by(claim_id, conflicting_id)

        return Verdict(
            verdict=v_str,
            claim_id=claim_id,
            conflicting_claim_id=conflicting_id,
            path=path,
            hops=hops,
            reason=reason,
            precedent_count=p_count,
            last_collision_time=p_last
        )

    def check(self, claim_id: str) -> Dict[str, Any]:
        """Check current status of a claim and, if WAITING, return which claim blocks it."""
        self._expire_stale()

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
        """Release a claim lock and re-evaluate waiting claims."""
        self._expire_stale()
        now = self.clock()

        self.db.release_claim(claim_id, now=now)
        unblocked = self._unblock_eligible_waiters()

        return {
            "status": "released",
            "claim_id": claim_id,
            "unblocked_claims": unblocked
        }
