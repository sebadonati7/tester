"""
SIRAYA Health Navigator — LLM Service (REFACTORED)
V5.0: Cleaned — only active methods retained for V3 controller flow.

Active methods:
    __init__(), _init_clients(), is_available(),
    generate_with_json_parse(), test_api_connections()
"""

import re
import json
import logging
from typing import Dict, Any, Optional

import streamlit as st
from groq import Groq
import google.generativeai as genai

from ..config.settings import SupabaseConfig

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# LLM SERVICE — REFACTORED (only active methods)
# ============================================================================

class LLMService:
    """
    Servizio LLM — client wrapper.

    Metodi attivi (usati da TriageControllerV3 / QuestionGenerator):
        generate_with_json_parse(prompt, temperature, max_tokens) → Dict
        test_api_connections() → Dict[str, bool]
        is_available → bool
    """

    def __init__(self):
        self._groq_client: Optional[Groq] = None
        self._gemini_model = None
        self._init_clients()
        logger.info("LLMService V5.0 initialized (refactored)")

    # ------------------------------------------------------------------
    # CLIENT INIT
    # ------------------------------------------------------------------

    def _init_clients(self) -> None:
        """Inizializza i client Groq e Gemini tramite APIConfig (nested + flat)."""
        from ..config.settings import APIConfig

        # ── Groq ──
        groq_api_key = APIConfig.get_groq_key()
        if groq_api_key:
            try:
                self._groq_client = Groq(api_key=groq_api_key)
                self._groq_client.models.list()
                logger.info("✅ Groq client initialized and connected")
            except Exception as e:
                logger.error(
                    f"❌ Groq init/test failed: {type(e).__name__} - {e}"
                )
                self._groq_client = None
        else:
            logger.warning("⚠️ GROQ_API_KEY not found in secrets or env")

        # ── Gemini ──
        gemini_api_key = APIConfig.get_gemini_key()
        if gemini_api_key:
            try:
                genai.configure(api_key=gemini_api_key)
                self._gemini_model = genai.GenerativeModel(APIConfig.GEMINI_MODEL)
                logger.info("✅ Gemini client initialized")
            except Exception as e:
                logger.error(
                    f"❌ Gemini init failed: {type(e).__name__} - {e}"
                )
                self._gemini_model = None
        else:
            logger.warning("⚠️ GEMINI_API_KEY not found in secrets or env")

    def is_available(self) -> bool:
        """Almeno un LLM disponibile?"""
        return self._groq_client is not None or self._gemini_model is not None

    # ------------------------------------------------------------------
    # GENERATE WITH JSON PARSE  (used by QuestionGenerator in V3)
    # ------------------------------------------------------------------

    def generate_with_json_parse(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 500
    ) -> Dict[str, Any]:
        """
        Genera risposta da LLM con parsing JSON robusto.

        ENFORCEMENT: Se prompt richiede multiple_choice, FORZA presenza di "options".

        Args:
            prompt: Prompt completo che chiede JSON
            temperature: 0.0-1.0 (creatività)
            max_tokens: Lunghezza max risposta

        Returns:
            Dizionario parsed o {} in caso di errore
        """
        # Aggiungi enforcement al prompt se richiede multiple_choice
        if "multiple_choice" in prompt.lower():
            prompt += "\n\n⚠️ CRITICAL: Se type='multiple_choice', DEVI includere 'options' array con 2-4 opzioni."

        response_text = ""
        try:
            # Chiamata LLM standard
            if self._groq_client:
                response = self._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                response_text = response.choices[0].message.content
            elif self._gemini_model:
                response = self._gemini_model.generate_content(prompt)
                response_text = response.text
            else:
                logger.error("❌ Nessun LLM disponibile per generate_with_json_parse")
                return {}

            # Estrai JSON da markdown code blocks se presente
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(1)

            # Parse JSON
            parsed = json.loads(response_text)

            # VALIDATION: Se type='multiple_choice' ma mancano options, fallback
            if parsed.get("type") == "multiple_choice" and not parsed.get("options"):
                logger.warning("⚠️ AI ha restituito multiple_choice senza options, converto a open_text")
                parsed["type"] = "open_text"
                parsed["options"] = None

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parsing error: {e}")
            logger.error(f"Response text: {response_text[:500] if response_text else 'N/A'}")
            return {}
        except Exception as e:
            logger.error(f"❌ LLM generate_with_json_parse error: {type(e).__name__} - {e}")
            return {}

    # ------------------------------------------------------------------
    # TEST API CONNECTIONS (used by sidebar debug)
    # ------------------------------------------------------------------

    def test_api_connections(self) -> Dict[str, bool]:
        """
        Testa tutte le connessioni API.  Utile per debug / sidebar.

        Returns:
            {"groq": bool, "gemini": bool, "supabase": bool}
        """
        results = {"groq": False, "gemini": False, "supabase": False}

        if self._groq_client:
            try:
                self._groq_client.models.list()
                results["groq"] = True
                logger.info("✅ Groq connection test: OK")
            except Exception as e:
                logger.error(f"❌ Groq test: {type(e).__name__} - {e}")

        if self._gemini_model:
            try:
                self._gemini_model.generate_content("Rispondi solo: OK")
                results["gemini"] = True
                logger.info("✅ Gemini connection test: OK")
            except Exception as e:
                logger.error(f"❌ Gemini test: {type(e).__name__} - {e}")

        try:
            if SupabaseConfig.is_configured():
                from supabase import create_client
                client = create_client(
                    SupabaseConfig.get_url(), SupabaseConfig.get_key()
                )
                client.table(SupabaseConfig.TABLE_LOGS).select(
                    "id"
                ).limit(1).execute()
                results["supabase"] = True
                logger.info("✅ Supabase connection test: OK")
        except Exception as e:
            logger.error(f"❌ Supabase test: {type(e).__name__} - {e}")

        return results


# ============================================================================
# SINGLETON
# ============================================================================

_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Restituisce l'istanza singleton di LLMService."""
    global _llm_service
    if _llm_service is None:
        logger.info("Creating new LLMService instance")
        _llm_service = LLMService()
    return _llm_service
