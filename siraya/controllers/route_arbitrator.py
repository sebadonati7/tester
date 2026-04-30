"""
RouteArbitrator — Centralized routing with LLMJudge + SmartRouter reconciliation.

Priority:
- First turn: LLMJudge classification (semantic)
- Subsequent turns on branch C: check_escalation (threshold=1)
- Branches A/B/INFO: continuity (no reclassification)
"""

import logging
from typing import Tuple, Optional, Dict

logger = logging.getLogger(__name__)


class RouteArbitrator:
    """
    Centralizes routing decisions between SmartRouter and LLMJudge.

    Returns (branch, urgency_score, confidence) tuple.

    Branches:
        "A" — Emergency (Red/Orange)
        "B" — Mental Health (Black)
        "C" — Standard (Green)
        "INFO" — Informational
    """

    # Confidence scores
    CONFIDENCE_HIGH = 1.0
    CONFIDENCE_MEDIUM = 0.7
    CONFIDENCE_LOW = 0.4

    # Urgency scores by branch
    URGENCY_MAP = {
        "A": 5,
        "B": 4,
        "C": 2,
        "INFO": 1,
    }

    def __init__(self, smart_router, llm_judge_cls=None):
        """
        Args:
            smart_router: SmartRouter class/instance with .route() and .check_escalation()
            llm_judge_cls: LLMJudge class with .evaluate_input() (optional, for future use)
        """
        self.smart_router = smart_router
        self.llm_judge_cls = llm_judge_cls

    def route(
        self,
        user_input: str,
        current_data: Dict,
        current_branch: Optional[str],
        current_phase: str,
        llm_judgment: Optional[Dict] = None,
    ) -> Tuple[str, int, float]:
        """
        Determine the routing branch with priority logic.

        Args:
            user_input: Raw user message
            current_data: Collected session data
            current_branch: Currently active branch (None if first turn)
            current_phase: Current FSM phase string
            llm_judgment: Optional pre-computed LLMJudge evaluation dict

        Returns:
            (branch, urgency_score, confidence)
        """

        # ── CASE 1: First turn — use LLMJudge hint, fall back to SmartRouter ──
        if not current_branch:
            branch, urgency, confidence = self._route_first_turn(user_input, llm_judgment)
            logger.info(
                f"🔀 RouteArbitrator [FIRST TURN]: branch={branch}, "
                f"urgency={urgency}, confidence={confidence:.2f}"
            )
            return branch, urgency, confidence

        # ── CASE 2: Already on A/B/INFO — maintain continuity ──
        if current_branch in ("A", "B", "INFO"):
            urgency = self.URGENCY_MAP.get(current_branch, 2)
            logger.info(f"🔀 RouteArbitrator [CONTINUITY]: branch={current_branch} maintained")
            return current_branch, urgency, self.CONFIDENCE_HIGH

        # ── CASE 3: On branch C — check for escalation to A (threshold=1) ──
        if current_branch == "C":
            if self.smart_router.check_escalation(user_input):
                logger.warning(
                    f"⚠️ RouteArbitrator [ESCALATION C→A]: '{user_input[:60]}'"
                )
                return "A", self.URGENCY_MAP["A"], self.CONFIDENCE_HIGH
            logger.info("🔀 RouteArbitrator [STANDARD]: C maintained")
            return "C", self.URGENCY_MAP["C"], self.CONFIDENCE_HIGH

        # ── FALLBACK ──
        logger.warning(f"⚠️ RouteArbitrator: unknown branch '{current_branch}', defaulting to C")
        return "C", self.URGENCY_MAP["C"], self.CONFIDENCE_LOW

    def _route_first_turn(
        self,
        user_input: str,
        llm_judgment: Optional[Dict],
    ) -> Tuple[str, int, float]:
        """
        Route on first turn using LLMJudge hint (if available) + SmartRouter safety net.
        """

        # ── LLMJudge takes precedence on first turn ──
        if llm_judgment:
            urgency_hint = llm_judgment.get("urgency_hint", "")

            if llm_judgment.get("is_emergency") or urgency_hint == "emergency":
                return "A", self.URGENCY_MAP["A"], self.CONFIDENCE_HIGH

            if urgency_hint == "mental_health":
                return "B", self.URGENCY_MAP["B"], self.CONFIDENCE_HIGH

            if llm_judgment.get("is_info_request") or urgency_hint == "info":
                return "INFO", self.URGENCY_MAP["INFO"], self.CONFIDENCE_HIGH

            if llm_judgment.get("is_specific_symptom") or llm_judgment.get("is_generic_symptom"):
                # Use SmartRouter as a secondary check for keywords the LLM may miss
                sr_path, _ = self.smart_router.route(user_input)
                if sr_path == "A":
                    return "A", self.URGENCY_MAP["A"], self.CONFIDENCE_MEDIUM
                if sr_path == "B":
                    return "B", self.URGENCY_MAP["B"], self.CONFIDENCE_MEDIUM
                return "C", self.URGENCY_MAP["C"], self.CONFIDENCE_MEDIUM

        # ── SmartRouter as sole arbiter when no LLMJudge output ──
        path, metadata = self.smart_router.route(user_input)
        urgency = metadata.get("urgency", self.URGENCY_MAP.get(path, 2))
        logger.info(f"🔀 SmartRouter fallback: path={path}, urgency={urgency}")
        return path, urgency, self.CONFIDENCE_LOW
