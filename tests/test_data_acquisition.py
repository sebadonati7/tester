"""
Tests for DataAcquisitionManager — Problema 2: Allucinazione Dati.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def get_dam():
    from siraya.controllers.data_acquisition_manager import DataAcquisitionManager
    return DataAcquisitionManager()


class TestExplicitLocation:
    """Test rilevamento location esplicitamente dichiarata dall'utente."""

    def test_explicit_mi_trovo_a(self):
        dam = get_dam()
        result = dam.extract_and_validate("mi trovo a Bologna", {}, None)
        assert "location" in result["confirmed"]
        assert result["confirmed"]["location"] == "Bologna"

    def test_explicit_sono_a(self):
        dam = get_dam()
        result = dam.extract_and_validate("sono a Modena", {}, None)
        assert "location" in result["confirmed"]

    def test_location_not_in_current_data_no_override(self):
        """Se location già in collected, non deve sovrascrivere."""
        dam = get_dam()
        result = dam.extract_and_validate(
            "mi trovo a Parma", {"location": "Bologna"}, None
        )
        # Location già in current_data → non deve essere né confirmed né pending
        assert "location" not in result["confirmed"]
        assert "location" not in result["pending_validation"]


class TestInferredLocation:
    """Test che location estratta da LLM vada in pending_validation."""

    def test_llm_extracted_location_goes_to_pending(self):
        dam = get_dam()
        llm_output = {"extracted_location": "Ferrara"}
        # Input senza menzione esplicita della città
        result = dam.extract_and_validate("ho mal di testa", {}, llm_output)

        assert "location" in result["pending_validation"]
        assert result["pending_validation"]["location"]["value"] == "Ferrara"
        assert result["pending_validation"]["location"]["source"] == "llm_extracted"
        assert result["validation_required"] is True

    def test_confirmation_question_generated(self):
        dam = get_dam()
        llm_output = {"extracted_location": "Bologna"}
        result = dam.extract_and_validate("ho la febbre", {}, llm_output)
        assert len(result["confirmation_questions"]) > 0
        assert "Bologna" in result["confirmation_questions"][0]


class TestExplicitAge:
    """Test rilevamento età esplicitamente dichiarata dall'utente."""

    def test_ho_anni_explicit(self):
        dam = get_dam()
        result = dam.extract_and_validate("ho 35 anni", {}, None)
        assert "age" in result["confirmed"]
        assert result["confirmed"]["age"] == 35

    def test_ne_ho_explicit(self):
        dam = get_dam()
        result = dam.extract_and_validate("ne ho 42", {}, None)
        assert "age" in result["confirmed"]
        assert result["confirmed"]["age"] == 42

    def test_invalid_age_excluded(self):
        dam = get_dam()
        result = dam.extract_and_validate("ho 200 anni", {}, None)
        assert "age" not in result["confirmed"]


class TestInferredAge:
    """Test che età estratta da LLM vada in pending_validation."""

    def test_llm_extracted_age_goes_to_pending(self):
        dam = get_dam()
        llm_output = {"extracted_age": 45}
        result = dam.extract_and_validate("dolore alla schiena", {}, llm_output)

        assert "age" in result["pending_validation"]
        assert result["pending_validation"]["age"]["value"] == 45
        assert result["validation_required"] is True

    def test_llm_invalid_age_excluded(self):
        dam = get_dam()
        llm_output = {"extracted_age": 999}
        result = dam.extract_and_validate("dolore", {}, llm_output)
        assert "age" not in result["pending_validation"]


class TestValidationResponse:
    """Test processo di validazione tramite risposta utente."""

    def test_positive_confirmation(self):
        dam = get_dam()
        pending = {
            "location": {"value": "Bologna", "source": "llm_extracted", "question": "Sei a Bologna?"},
        }
        result = dam.process_validation_response("sì, esatto", pending)
        assert "location" in result["confirmed"]
        assert result["confirmed"]["location"] == "Bologna"
        assert len(result["rejected"]) == 0

    def test_negative_rejection(self):
        dam = get_dam()
        pending = {
            "location": {"value": "Bologna", "source": "llm_extracted", "question": "Sei a Bologna?"},
        }
        result = dam.process_validation_response("no, sono a Modena", pending)
        assert "location" in result["rejected"]
        assert "location" not in result["confirmed"]

    def test_multiple_pending_all_confirmed(self):
        dam = get_dam()
        pending = {
            "location": {"value": "Parma", "source": "llm_extracted", "question": "Sei a Parma?"},
            "age": {"value": 30, "source": "llm_extracted", "question": "Hai 30 anni?"},
        }
        result = dam.process_validation_response("sì, confermo tutto", pending)
        assert "location" in result["confirmed"]
        assert "age" in result["confirmed"]


class TestNoDataNoHallucination:
    """Verifica che nessun dato venga aggiunto senza fonte esplicita."""

    def test_generic_symptom_no_location_or_age(self):
        dam = get_dam()
        result = dam.extract_and_validate("sto male", {}, {"urgency_hint": "standard"})
        assert len(result["confirmed"]) == 0
        assert len(result["pending_validation"]) == 0
        assert result["validation_required"] is False
