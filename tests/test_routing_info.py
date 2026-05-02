"""
Tests per RouteArbitrator e InfoProcessor._extract_location.
Verifica che query INFO vengano riconosciute e la location estratta correttamente.
"""

import unittest


class MockLLMService:
    """Mock LLMService per test unitari (senza chiamate API)."""

    def generate_with_json_parse(self, prompt, **kwargs):
        return {
            "is_specific_symptom": False,
            "is_generic_symptom": True,
            "is_info_request": False,
            "is_emergency": False,
            "urgency_hint": "standard",
        }

    def generate_text(self, prompt, **kwargs):
        return "Risposta di test."


class MockLLMJudge:
    """Mock LLMJudge per test unitari."""

    @staticmethod
    def evaluate_input(llm_service, user_input):
        text = user_input.lower()
        if any(kw in text for kw in ["orari", "dove", "telefono", "prenot"]):
            return {"urgency_hint": "info", "is_info_request": True}
        return {"urgency_hint": "standard"}


class TestInfoQueryRecognition(unittest.TestCase):
    """Verifica che RouteArbitrator riconosca query INFO correttamente."""

    def setUp(self):
        from siraya.controllers.route_arbitrator import RouteArbitrator
        self.arbitrator = RouteArbitrator(MockLLMService(), MockLLMJudge)

    def test_orari_cau(self):
        self.assertTrue(self.arbitrator._is_info_query("Orari del CAU di Ravenna?"))

    def test_orari_ps(self):
        self.assertTrue(self.arbitrator._is_info_query("Mi dai gli orari del PS?"))

    def test_numero_consultorio(self):
        self.assertTrue(self.arbitrator._is_info_query("Numero del consultorio a Bologna?"))

    def test_indirizzo(self):
        self.assertTrue(self.arbitrator._is_info_query("Indirizzo del pronto soccorso di Modena?"))

    def test_prenotazione(self):
        self.assertTrue(self.arbitrator._is_info_query("Come faccio una prenotazione?"))

    def test_dolore_petto_not_info(self):
        self.assertFalse(self.arbitrator._is_info_query("Ho dolore al petto"))

    def test_generic_pain_not_info(self):
        self.assertFalse(self.arbitrator._is_info_query("Sto male, cosa faccio?"))

    def test_dove_si_trova(self):
        self.assertTrue(self.arbitrator._is_info_query("Dove si trova il CAU?"))

    def test_quali_servizi(self):
        self.assertTrue(self.arbitrator._is_info_query("Quali servizi offre il consultorio?"))

    def test_contatti(self):
        self.assertTrue(self.arbitrator._is_info_query("Contatti del centro di salute mentale"))


class TestRouteArbitratorDynamicSwitch(unittest.TestCase):
    """Verifica il routing dinamico verso INFO."""

    def setUp(self):
        from siraya.controllers.route_arbitrator import RouteArbitrator
        self.arbitrator = RouteArbitrator(MockLLMService(), MockLLMJudge)

    def test_info_switch_from_standard_branch(self):
        branch, urgency, confidence = self.arbitrator.route(
            user_input="Orari del CAU di Ravenna?",
            current_data={"chief_complaint": "Cefalea"},
            current_branch="C",
            current_phase="clinical_triage",
        )
        self.assertEqual(branch, "INFO")
        self.assertGreater(confidence, 0.9)

    def test_maintains_emergency_branch(self):
        branch, urgency, confidence = self.arbitrator.route(
            user_input="Ho dolore sempre più forte al petto",
            current_data={"chief_complaint": "Dolore toracico"},
            current_branch="A",
            current_phase="fast_triage",
        )
        self.assertEqual(branch, "A")

    def test_maintains_standard_branch_for_symptom(self):
        branch, urgency, confidence = self.arbitrator.route(
            user_input="Il dolore è sempre presente",
            current_data={"chief_complaint": "Cefalea"},
            current_branch="C",
            current_phase="clinical_triage",
        )
        self.assertEqual(branch, "C")

    def test_first_turn_urgency_override_emergency(self):
        branch, urgency, confidence = self.arbitrator.route(
            user_input="Ho un forte dolore al petto",
            current_data={},
            current_branch=None,
            current_phase="intake",
            urgency_override="emergency",
        )
        self.assertEqual(branch, "A")
        self.assertEqual(confidence, 1.0)

    def test_first_turn_urgency_override_info(self):
        branch, urgency, confidence = self.arbitrator.route(
            user_input="Orari del CAU?",
            current_data={},
            current_branch=None,
            current_phase="intake",
            urgency_override="info",
        )
        self.assertEqual(branch, "INFO")


class TestLocationExtraction(unittest.TestCase):
    """Verifica estrazione location da query naturale."""

    def setUp(self):
        from siraya.controllers.info_processor import InfoProcessor

        class MockRAG:
            def retrieve_info_documents(self, **kwargs):
                return []

        self.processor = InfoProcessor(MockRAG(), MockLLMService())

    def test_di_ravenna(self):
        self.assertEqual(self.processor._extract_location("Orari del CAU di Ravenna?"), "Ravenna")

    def test_a_bologna(self):
        result = self.processor._extract_location("Numero consultorio a Bologna?")
        self.assertEqual(result, "Bologna")

    def test_no_location(self):
        self.assertIsNone(self.processor._extract_location("Quali sono gli orari?"))

    def test_presso(self):
        result = self.processor._extract_location("Indirizzo PS presso Modena")
        self.assertEqual(result, "Modena")


if __name__ == "__main__":
    unittest.main(verbosity=2)
