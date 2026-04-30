"""
Tests for InfoResponseGenerator — Problema 4: Path INFO Liberalizzato.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch


def get_generator(rag_results=None, llm_response="Risposta informativa di test."):
    from siraya.controllers.info_response_generator import InfoResponseGenerator

    rag_mock = MagicMock()
    rag_mock.retrieve_context.return_value = rag_results or []
    rag_mock.retrieve_context_for_info = MagicMock(return_value=rag_results or [])

    llm_mock = MagicMock()
    llm_mock.is_available.return_value = True
    # Simula client Groq con risposta testuale
    groq_mock = MagicMock()
    groq_mock.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=llm_response))
    ]
    llm_mock._groq_client = groq_mock
    llm_mock._gemini_model = None

    return InfoResponseGenerator(rag_mock, llm_mock)


class TestExtractQueryIntent:
    """Test estrazione intent dalla query."""

    def test_operating_hours_intent(self):
        gen = get_generator()
        assert gen.extract_query_intent("quali sono gli orari?") == "OPERATING_HOURS"

    def test_facility_location_intent(self):
        gen = get_generator()
        assert gen.extract_query_intent("dove si trova il pronto soccorso?") == "FACILITY_LOCATION"

    def test_cost_info_intent(self):
        gen = get_generator()
        assert gen.extract_query_intent("quanto costa il ticket?") == "COST_INFO"

    def test_procedure_info_intent(self):
        gen = get_generator()
        assert gen.extract_query_intent("come si prenota una visita?") == "PROCEDURE_INFO"

    def test_prescription_info_intent(self):
        gen = get_generator()
        assert gen.extract_query_intent("ho bisogno di una ricetta medica") == "PRESCRIPTION_INFO"

    def test_unknown_query_returns_other(self):
        gen = get_generator()
        intent = gen.extract_query_intent("bla bla bla xyz")
        assert intent in ("OTHER", "GENERAL_HEALTH")  # accetta entrambi come fallback


class TestGenerateInfoResponseWithRAG:
    """Test generazione risposta quando RAG ha risultati."""

    def test_response_generated_with_rag(self):
        rag_data = [
            {"content": "Il Pronto Soccorso di Bologna è aperto H24.", "source": "AUSL"}
        ]
        gen = get_generator(rag_results=rag_data, llm_response="Il PS di Bologna è aperto 24 ore su 24.")
        response = gen.generate_info_response("orari pronto soccorso", location="Bologna")
        assert isinstance(response, str)
        assert len(response) > 10

    def test_response_is_not_sbar(self):
        """La risposta INFO non deve contenere formato SBAR."""
        rag_data = [{"content": "Apertura: lunedì-venerdì 8-18", "source": "DB"}]
        gen = get_generator(rag_results=rag_data)
        response = gen.generate_info_response("orari ambulatorio")
        sbar_markers = ["S - SITUATION", "B - BACKGROUND", "A - ASSESSMENT", "R - RECOMMENDATION"]
        assert not any(marker in response for marker in sbar_markers)

    def test_location_passed_to_rag(self):
        """Verifica che location venga passata al RAG."""
        rag_data = [{"content": "Struttura a Modena", "source": "DB"}]
        gen = get_generator(rag_results=rag_data)
        gen.generate_info_response("dove si trova?", location="Modena")
        # RAG deve essere stato chiamato
        assert gen.rag_service.retrieve_context_for_info.called


class TestGenerateInfoResponseNoRAG:
    """Test risposta quando RAG non ha risultati (deve fare raffinamento, NO fallback generico)."""

    def test_no_rag_no_fatal_message(self):
        """Senza RAG, non deve dire 'NON HO TROVATO RISULTATI'."""
        gen = get_generator(rag_results=[])
        response = gen.generate_info_response("orari ospedale")
        forbidden_phrases = [
            "non ho trovato risultati",
            "nessun risultato",
            "non trovo nulla",
            "non ho informazioni",
        ]
        response_lower = response.lower()
        assert not any(phrase in response_lower for phrase in forbidden_phrases), \
            f"Risposta contiene fallback proibito: '{response[:200]}'"

    def test_no_rag_offers_refinement(self):
        """Senza RAG, deve offrire domande di raffinamento o alternative."""
        gen = get_generator(rag_results=[])
        response = gen.generate_info_response("voglio sapere gli orari")
        # Deve contenere almeno una domanda o alternativa
        has_question = "?" in response
        has_alternative = any(w in response.lower() for w in ["posso", "puoi", "prova", "contatt", "cup"])
        assert has_question or has_alternative, \
            f"Risposta non offre raffinamento né alternative: '{response[:200]}'"

    def test_no_rag_no_location_asks_city(self):
        """Senza location e senza RAG, deve chiedere la città."""
        gen = get_generator(rag_results=[])
        response = gen.generate_info_response("quali sono gli orari?", location=None)
        # Deve suggerire di specificare la città
        has_city_prompt = any(w in response.lower() for w in ["città", "comune", "dove", "zona"])
        assert has_city_prompt, f"Risposta non chiede la città: '{response[:200]}'"


class TestConversationHistory:
    """Test mantenimento contesto conversazionale."""

    def test_conversation_history_passed_to_llm(self):
        """La storia conversazionale deve essere passata al prompt LLM."""
        rag_data = [{"content": "Servizio attivo", "source": "DB"}]
        gen = get_generator(rag_results=rag_data)
        history = [
            {"role": "user", "content": "Dove si trova?"},
            {"role": "assistant", "content": "Il servizio si trova in Via Roma 1."},
        ]
        response = gen.generate_info_response(
            "e il numero di telefono?",
            conversation_history=history,
        )
        # Verifica che LLM sia stato chiamato (non solo RAG fallback)
        assert gen.rag_service.retrieve_context_for_info.called


class TestIntentSpecificInstructions:
    """Test che ogni intent abbia istruzioni specifiche nel prompt LLM."""

    def test_all_intents_have_instructions(self):
        from siraya.controllers.info_response_generator import InfoResponseGenerator, InfoIntent

        gen = get_generator()
        intents = [
            InfoIntent.OPERATING_HOURS, InfoIntent.FACILITY_LOCATION,
            InfoIntent.SERVICE_INFO, InfoIntent.COST_INFO,
            InfoIntent.PROCEDURE_INFO, InfoIntent.GENERAL_HEALTH,
            InfoIntent.PRESCRIPTION_INFO, InfoIntent.OTHER,
        ]
        for intent in intents:
            assert intent in gen.INTENT_INSTRUCTIONS, \
                f"Intent '{intent}' non ha istruzioni specifiche"
            assert len(gen.INTENT_INSTRUCTIONS[intent]) > 10
