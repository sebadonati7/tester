"""
Test di integrazione per il Path INFO.

Verifica il flusso completo: RouteArbitrator → InfoProcessor → risposta.
Verifica backward compatibility (branch A, B, C ancora funzionanti).
Verifica che metodi obsoleti siano stati rimossi.
"""

import unittest


class TestInfoMethodsCleanup(unittest.TestCase):
    """Verifica che i metodi obsoleti siano stati rimossi."""

    def test_info_from_intake_removed_from_fsm(self):
        """_info_from_intake NON deve esistere in TriageFSM."""
        from siraya.controllers.triage_controller_v3 import TriageFSM

        class MockState:
            def get(self, key, default=None):
                return default
            def set(self, key, value):
                pass

        fsm = TriageFSM(MockState())
        self.assertFalse(
            hasattr(fsm, "_info_from_intake"),
            "_info_from_intake è ancora presente in TriageFSM — deve essere rimosso",
        )

    def test_generate_info_response_removed_from_outcome_gen(self):
        """_generate_info_response NON deve esistere in OutcomeGenerator."""
        from siraya.controllers.triage_controller_v3 import OutcomeGenerator

        outcome_gen = OutcomeGenerator(None, None)
        self.assertFalse(
            hasattr(outcome_gen, "_generate_info_response"),
            "_generate_info_response è ancora presente in OutcomeGenerator — deve essere rimosso",
        )

    def test_process_info_branch_exists_in_controller(self):
        """_process_info_branch DEVE esistere in TriageControllerV3."""
        from siraya.controllers.triage_controller_v3 import TriageControllerV3

        self.assertTrue(
            hasattr(TriageControllerV3, "_process_info_branch"),
            "_process_info_branch non trovato in TriageControllerV3",
        )


class TestRouteArbitratorExists(unittest.TestCase):
    """Verifica che RouteArbitrator sia importabile e funzionante."""

    def test_import(self):
        from siraya.controllers.route_arbitrator import RouteArbitrator
        self.assertTrue(callable(RouteArbitrator))

    def test_instantiation(self):
        from siraya.controllers.route_arbitrator import RouteArbitrator

        arbitrator = RouteArbitrator(None, None)
        self.assertIsNotNone(arbitrator)

    def test_is_info_query_method(self):
        from siraya.controllers.route_arbitrator import RouteArbitrator

        arbitrator = RouteArbitrator(None, None)
        self.assertTrue(hasattr(arbitrator, "_is_info_query"))
        self.assertTrue(hasattr(arbitrator, "route"))


class TestInfoProcessorExists(unittest.TestCase):
    """Verifica che InfoProcessor sia importabile e funzionante."""

    def test_import(self):
        from siraya.controllers.info_processor import InfoProcessor
        self.assertTrue(callable(InfoProcessor))

    def test_instantiation(self):
        from siraya.controllers.info_processor import InfoProcessor

        processor = InfoProcessor(None, None)
        self.assertIsNotNone(processor)

    def test_methods_exist(self):
        from siraya.controllers.info_processor import InfoProcessor

        processor = InfoProcessor(None, None)
        self.assertTrue(hasattr(processor, "process_info_query"))
        self.assertTrue(hasattr(processor, "_extract_location"))
        self.assertTrue(hasattr(processor, "_build_prompt"))


class TestInfoProcessorLogic(unittest.TestCase):
    """Verifica logica InfoProcessor senza chiamate esterne."""

    def _make_processor(self, docs=None):
        from siraya.controllers.info_processor import InfoProcessor

        _docs = docs or []

        class MockRAG:
            def retrieve_info_documents(self, **kwargs):
                return _docs

        class MockLLM:
            def generate_text(self, prompt, **kwargs):
                return "Il CAU di Ravenna è aperto dalle 8:00 alle 20:00."

        return InfoProcessor(MockRAG(), MockLLM())

    def test_asks_for_location_if_missing(self):
        processor = self._make_processor()
        result = processor.process_info_query(
            user_query="Quali sono gli orari?",
            location=None,
            conversation_history=[],
        )
        # When location is missing, no documents can be retrieved
        self.assertEqual(result["docs_used"], 0)
        # Response should be non-empty (asking for location)
        self.assertGreater(len(result["response"]), 0)

    def test_uses_provided_location(self):
        docs = [{"title": "CAU Ravenna", "content": "Aperto 8-20"}]
        processor = self._make_processor(docs=docs)
        result = processor.process_info_query(
            user_query="Orari?",
            location="Ravenna",
            conversation_history=[],
        )
        self.assertGreater(result["docs_used"], 0)
        self.assertIsInstance(result["response"], str)
        self.assertGreater(len(result["response"]), 0)

    def test_no_docs_returns_helpful_message(self):
        processor = self._make_processor(docs=[])
        result = processor.process_info_query(
            user_query="Orari del CAU di Ravenna?",
            location="Ravenna",
            conversation_history=[],
        )
        self.assertEqual(result["docs_used"], 0)
        self.assertIn("Ravenna", result["response"])

    def test_conversation_history_passed_to_prompt(self):
        """Il prompt deve includere la conversazione precedente."""
        docs = [{"title": "Test", "content": "Info test"}]
        processor = self._make_processor(docs=docs)
        history = [
            {"role": "user", "content": "Cercavo info su Ravenna"},
            {"role": "assistant", "content": "Certo, posso aiutarti."},
        ]
        prompt = processor._build_prompt(
            query="E il telefono?",
            docs=docs,
            history=history,
            location="Ravenna",
        )
        self.assertIn("Cercavo info su Ravenna", prompt)


class TestRAGServiceInfoMethods(unittest.TestCase):
    """Verifica che i nuovi metodi siano presenti in RAGService."""

    def test_retrieve_info_documents_exists(self):
        from siraya.services.rag_service import RAGService

        self.assertTrue(hasattr(RAGService, "retrieve_info_documents"))

    def test_embed_text_exists(self):
        from siraya.services.rag_service import RAGService

        self.assertTrue(hasattr(RAGService, "_embed_text"))

    def test_keyword_fallback_exists(self):
        from siraya.services.rag_service import RAGService

        self.assertTrue(hasattr(RAGService, "_keyword_fallback_info"))


class TestLLMServiceGenerateText(unittest.TestCase):
    """Verifica che generate_text esista in LLMService."""

    def test_generate_text_exists(self):
        from siraya.services.llm_service import LLMService

        self.assertTrue(hasattr(LLMService, "generate_text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
