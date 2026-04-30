"""
Tests for RouteArbitrator — Problema 1: Routing Ambiguo.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch


class MockSmartRouter:
    """Mock SmartRouter per test."""

    @classmethod
    def route(cls, user_message: str):
        msg = user_message.lower()
        if "cuore" in msg or "petto" in msg:
            return ("A", {"urgency": 5, "reason": "emergency"})
        if "suicidio" in msg or "voglio morire" in msg:
            return ("B", {"reason": "mental_health"})
        if "orari" in msg or "indirizzo" in msg:
            return ("INFO", {"reason": "info"})
        return ("C", {"reason": "standard"})

    @classmethod
    def check_escalation(cls, user_message: str) -> bool:
        msg = user_message.lower()
        escalation_kw = ["peggiorato", "peggiora", "non respiro bene", "svengo"]
        return any(kw in msg for kw in escalation_kw)


def get_arbitrator():
    from siraya.controllers.route_arbitrator import RouteArbitrator
    return RouteArbitrator(MockSmartRouter)


class TestRouteArbitratorFirstTurn:
    """Test routing al primo turno (nessun branch attivo)."""

    def test_emergency_from_llm_judgment(self):
        arbitrator = get_arbitrator()
        judgment = {"is_emergency": True, "urgency_hint": "emergency"}
        branch, urgency, confidence = arbitrator.route(
            "ho un forte dolore al petto", {}, None, "intake", judgment
        )
        assert branch == "A"
        assert urgency == 5
        assert confidence == 1.0

    def test_mental_health_from_llm_judgment(self):
        arbitrator = get_arbitrator()
        judgment = {"urgency_hint": "mental_health", "is_emergency": False}
        branch, urgency, confidence = arbitrator.route(
            "voglio morire", {}, None, "intake", judgment
        )
        assert branch == "B"
        assert urgency == 4

    def test_info_from_llm_judgment(self):
        arbitrator = get_arbitrator()
        judgment = {"is_info_request": True, "urgency_hint": "info"}
        branch, urgency, confidence = arbitrator.route(
            "quali sono gli orari del pronto soccorso?", {}, None, "intake", judgment
        )
        assert branch == "INFO"

    def test_standard_fallback_to_smart_router(self):
        arbitrator = get_arbitrator()
        judgment = {"is_specific_symptom": True, "urgency_hint": "standard"}
        branch, urgency, confidence = arbitrator.route(
            "ho mal di testa", {}, None, "intake", judgment
        )
        # SmartRouter dovrebbe restituire C per un sintomo generico
        assert branch in ("C", "A", "B")

    def test_no_judgment_uses_smart_router(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "ho mal di testa", {}, None, "intake", None
        )
        assert branch == "C"
        assert confidence < 0.8  # bassa confidenza senza LLMJudge

    def test_emergency_keyword_via_smart_router(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "ho dolore al petto", {}, None, "intake", None
        )
        assert branch == "A"


class TestRouteArbitratorContinuity:
    """Test continuità branch A/B/INFO."""

    def test_emergency_branch_maintained(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "il dolore continua", {}, "A", "fast_triage"
        )
        assert branch == "A"
        assert confidence == 1.0

    def test_mental_health_branch_maintained(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "mi sento un po' meglio", {}, "B", "risk_assessment"
        )
        assert branch == "B"
        assert confidence == 1.0

    def test_info_branch_maintained(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "e il numero di telefono?", {}, "INFO", "outcome"
        )
        assert branch == "INFO"


class TestRouteArbitratorEscalation:
    """Test escalation C → A (threshold=1)."""

    def test_escalation_on_single_keyword(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "il dolore è peggiorato molto", {}, "C", "clinical_triage"
        )
        assert branch == "A"
        assert urgency == 5

    def test_no_escalation_without_keywords(self):
        arbitrator = get_arbitrator()
        branch, urgency, confidence = arbitrator.route(
            "il dolore è stabile", {}, "C", "clinical_triage"
        )
        assert branch == "C"

    def test_escalation_threshold_single_keyword(self):
        """Con threshold=1, anche una sola keyword di escalation deve triggerare."""
        arbitrator = get_arbitrator()
        branch, _, _ = arbitrator.route(
            "non respiro bene", {}, "C", "clinical_triage"
        )
        assert branch == "A"


class TestSmartRouterEscalationThreshold:
    """Verifica che check_escalation abbia soglia=1."""

    def test_single_escalation_keyword_triggers(self):
        from siraya.controllers.smart_router import SmartRouter
        # Una sola keyword di escalation deve triggerare
        assert SmartRouter.check_escalation("il dolore è peggiorato") is True

    def test_single_emergency_keyword_triggers(self):
        from siraya.controllers.smart_router import SmartRouter
        # Anche una sola emergency keyword pesa 2 → ≥ 1
        assert SmartRouter.check_escalation("non respiro bene") is True

    def test_neutral_message_no_escalation(self):
        from siraya.controllers.smart_router import SmartRouter
        assert SmartRouter.check_escalation("ho mal di testa lieve") is False
