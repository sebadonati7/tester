"""
Tests for ProtocolDrivenExecutor — Problema 3: Path BLACK Protocol-Driven.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock


def get_executor(supabase=None):
    from siraya.controllers.protocol_driven_executor import ProtocolDrivenExecutor
    rag_mock = MagicMock()
    rag_mock.retrieve_context.return_value = []
    llm_mock = MagicMock()
    llm_mock.generate_with_json_parse.return_value = {
        "question": "Come ti senti in questo momento?",
        "completed": False,
    }
    return ProtocolDrivenExecutor(rag_mock, llm_mock, supabase)


class TestIdentifyConcernType:
    """Test identificazione tipo di problematica psicologica."""

    def test_suicidal_ideation_detected(self):
        executor = get_executor()
        assert executor.identify_concern_type("voglio morire", "") == "suicidal_ideation"

    def test_suicidal_explicit(self):
        executor = get_executor()
        assert executor.identify_concern_type("sto pensando al suicidio", "") == "suicidal_ideation"

    def test_depression_detected(self):
        executor = get_executor()
        assert executor.identify_concern_type("sono depresso da mesi", "") == "depression"

    def test_eating_disorder_detected(self):
        executor = get_executor()
        assert executor.identify_concern_type("non riesco a mangiare", "") == "eating_disorder"

    def test_substance_abuse_detected(self):
        executor = get_executor()
        assert executor.identify_concern_type("uso cocaina da un anno", "") == "substance_abuse"

    def test_general_fallback(self):
        executor = get_executor()
        result = executor.identify_concern_type("mi sento triste a volte", "")
        assert result == "general_mental_health"

    def test_suicidal_ideation_takes_priority_over_depression(self):
        executor = get_executor()
        # Entrambi presenti: suicidal_ideation ha priorità
        result = executor.identify_concern_type("sono depresso e voglio morire", "")
        assert result == "suicidal_ideation"


class TestLoadProtocol:
    """Test caricamento protocollo (con fallback embedded)."""

    def test_load_suicidal_ideation_fallback(self):
        executor = get_executor(supabase=None)
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        assert "chunks" in protocol
        assert len(protocol["chunks"]) > 0
        assert "ASQ" in protocol["protocol_name"]

    def test_load_depression_fallback(self):
        executor = get_executor(supabase=None)
        protocol = executor.load_protocol_from_supabase("depression")
        assert "PHQ" in protocol["protocol_name"]

    def test_load_eating_disorder_fallback(self):
        executor = get_executor(supabase=None)
        protocol = executor.load_protocol_from_supabase("eating_disorder")
        assert "DCA" in protocol["protocol_name"]

    def test_load_substance_abuse_fallback(self):
        executor = get_executor(supabase=None)
        protocol = executor.load_protocol_from_supabase("substance_abuse")
        assert "SERT" in protocol["protocol_name"]

    def test_protocol_has_risk_stratification(self):
        executor = get_executor(supabase=None)
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        assert "risk_stratification_levels" in protocol
        assert len(protocol["risk_stratification_levels"]) > 0


class TestExecuteProtocol:
    """Test esecuzione protocollo step-by-step."""

    def test_first_turn_returns_question(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        result = executor.execute_protocol(protocol, [])
        assert result["completed"] is False
        assert len(result["next_question"]) > 0

    def test_progress_increases(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")

        # Primo turno
        r1 = executor.execute_protocol(protocol, [])
        # Secondo turno
        history = [
            {"role": "assistant", "content": r1["next_question"]},
            {"role": "user", "content": "No, non ho pensieri di farmi del male"},
        ]
        r2 = executor.execute_protocol(protocol, history)
        assert r2["protocol_progress"] > r1["protocol_progress"]

    def test_protocol_completes_after_all_chunks(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        n_chunks = len(protocol["chunks"])

        # Simula tanti turni quanti i chunk del protocollo
        history = []
        for i in range(n_chunks):
            history.append({"role": "assistant", "content": f"Domanda {i}"})
            history.append({"role": "user", "content": f"Risposta {i}"})

        result = executor.execute_protocol(protocol, history)
        assert result["completed"] is True


class TestStratifyRisk:
    """Test stratificazione rischio."""

    def test_suicidal_with_plan_is_immediate(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        conversation = [
            {"role": "user", "content": "ho un piano concreto e ho accesso ai mezzi"},
        ]
        risk = executor.stratify_risk(protocol, conversation, "suicidal_ideation")
        assert risk["risk_level"] == "immediate"
        assert risk["urgent_action"] is True
        assert "118" in risk["recommended_actions"]

    def test_suicidal_without_plan_is_high(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("suicidal_ideation")
        conversation = [
            {"role": "user", "content": "voglio morire ma non so come"},
        ]
        risk = executor.stratify_risk(protocol, conversation, "suicidal_ideation")
        assert risk["risk_level"] == "high"

    def test_depression_moderate(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("depression")
        conversation = [
            {"role": "user", "content": "sono triste, non dormo, mi sento vuoto, senza speranza"},
        ]
        risk = executor.stratify_risk(protocol, conversation, "depression")
        assert risk["risk_level"] in ("moderate", "high")
        assert "CSM" in risk["recommended_facility"]

    def test_eating_disorder_goes_to_centro_dca(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("eating_disorder")
        conversation = [{"role": "user", "content": "mi faccio vomitare spesso"}]
        risk = executor.stratify_risk(protocol, conversation, "eating_disorder")
        assert "DCA" in risk["recommended_facility"]

    def test_substance_abuse_goes_to_sert(self):
        executor = get_executor()
        protocol = executor.load_protocol_from_supabase("substance_abuse")
        conversation = [{"role": "user", "content": "uso eroina da anni"}]
        risk = executor.stratify_risk(protocol, conversation, "substance_abuse")
        assert "SERT" in risk["recommended_facility"] or "SerD" in risk["recommended_facility"]
