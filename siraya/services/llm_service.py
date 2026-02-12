"""
SIRAYA Health Navigator — LLM Service  (Orchestrator)
V4.0: Modular architecture — phase logic delegated to llm_phases/.

Architettura a 4 macro-fasi:
  INTAKE           → accoglienza & raccolta dati base  (ZERO RAG)
  CLINICAL_TRIAGE  → domande cliniche A/B/C via RAG + Protocollo Siraya
  RECOMMENDATION   → SBAR + ricerca struttura
  INFO             → branch informativo (interroga master_kb.json)

Il frontend deve SOLO chiamare:
    response = llm_service.generate_response(user_input, st.session_state)
"""

import re
import json
import time
import logging
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from groq import Groq
import google.generativeai as genai

from ..config.settings import EMERGENCY_RULES, SupabaseConfig

from .llm_utils import (
    SymptomNormalizer,
    DiagnosisSanitizer,
    PROMPTS,
    MAX_QUESTIONS,
    call_llm,
    get_conversation_ctx,
    get_rag_context,
    parse_options,
    has_symptom_keywords,
)

from .llm_phases import (
    IntakePhase,
    TriagePhase,
    RecommendationPhase,
    InfoPhase,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# LLM SERVICE  — THE ORCHESTRATOR
# ============================================================================

class LLMService:
    """
    Servizio LLM — orchestratore modulare.

    Metodo pubblico principale:
        generate_response(user_input, session_state) → str

    Internamente gestisce:
        - Emergency detection
        - Smart routing  (Percorso A / B / C / INFO)
        - Fasi INTAKE → CLINICAL_TRIAGE → RECOMMENDATION
        - Delegation to phase-specific handlers
        - Diagnosis sanitization
        - Logging Supabase
    """

    def __init__(self):
        self._groq_client: Optional[Groq] = None
        self._gemini_model = None
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._symptom_normalizer = SymptomNormalizer()
        self._init_clients()

        # ── Phase handlers ──
        self.intake = IntakePhase(self._groq_client, self._gemini_model)
        self.triage = TriagePhase(self._groq_client, self._gemini_model)
        self.recommendation = RecommendationPhase(self._groq_client, self._gemini_model)
        self.info = InfoPhase(self._groq_client, self._gemini_model)

        logger.info("LLMService V4.0 initialized (modular)")

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

    # ==================================================================
    #  ★★★  generate_response  —  ENTRY POINT UNICO  ★★★
    # ==================================================================

    def generate_response(self, user_input: str, session_state) -> str:
        """
        Macchina a stati autonoma.  Il frontend chiama SOLO questo metodo.

        Args:
            user_input:    messaggio dell'utente
            session_state: st.session_state (dict-like, mutabile in-place)

        Returns:
            Risposta testuale per l'utente.
        """
        start_time = time.time()

        # ── 0. ENSURE DEFAULTS ──
        self._ensure_session_defaults(session_state)

        # ── 1. EMERGENCY CHECK (sempre prioritario) ──
        emergency = self.check_emergency(user_input)
        if emergency:
            session_state["urgency_level"] = 5
            session_state["current_phase"] = "RECOMMENDATION"
            path = "A" if emergency.get("type") == "critical" else "B"
            session_state["triage_path"] = path
            session_state["pending_survey_options"] = None
            self._log_interaction(session_state, user_input,
                                  emergency["text"], start_time)
            return emergency["text"]

        # ── 2. EXTRACT DATA FROM USER INPUT ──
        self.intake.extract_inline_data(user_input, session_state)

        # ── 3. SMART ROUTING (percorso A/B/C/INFO) ──
        self._smart_route(user_input, session_state)

        # ── 4. DETERMINE MACRO PHASE ──
        phase = self._determine_phase(session_state, user_input)
        session_state["current_phase"] = phase

        # ── 5. PHASE DISPATCH ──
        if phase == "INTAKE":
            response = self.intake.handle(user_input, session_state)
        elif phase == "CLINICAL_TRIAGE":
            triage_path = session_state.get("triage_path", "C")
            response = self.triage.handle(user_input, session_state, triage_path)
            # None → max questions reached → switch to RECOMMENDATION
            if response is None:
                session_state["current_phase"] = "RECOMMENDATION"
                response = self.recommendation.handle(session_state)
        elif phase == "RECOMMENDATION":
            response = self.recommendation.handle(session_state)
        elif phase == "INFO":
            response = self.info.handle(user_input, session_state)
        else:
            response = self.intake.handle(user_input, session_state)

        # ── 6. SANITIZE ──
        response = DiagnosisSanitizer.sanitize(response)

        # ── 7. PARSE A/B/C OPTIONS ──
        options = parse_options(response)
        session_state["pending_survey_options"] = options

        # ── 8. INCREMENT QUESTION COUNT ──
        session_state["question_count"] = (
            session_state.get("question_count", 0) + 1
        )

        # ── 9. SUPABASE LOGGING ──
        self._log_interaction(session_state, user_input, response, start_time)

        return response

    # ==================================================================
    # SESSION DEFAULTS
    # ==================================================================

    @staticmethod
    def _ensure_session_defaults(ss) -> None:
        """Garantisce che le chiavi necessarie esistano in session_state."""
        defaults = {
            "collected_data": {},
            "question_count": 0,
            "triage_path": None,
            "current_phase": "INTAKE",
            "pending_survey_options": None,
            "urgency_level": 3,
            "specialization": "Generale",
            "red_flags": [],
            "messages": [],
        }
        for key, val in defaults.items():
            if key not in ss:
                ss[key] = val if not isinstance(val, (list, dict)) else type(val)()

    # ==================================================================
    # SMART ROUTING
    # ==================================================================

    def _smart_route(self, user_input: str, ss) -> None:
        """Determina o aggiorna il percorso (A/B/C/INFO)."""
        from ..controllers.smart_router import SmartRouter

        current_path = ss.get("triage_path")

        if current_path is None or ss.get("current_phase") == "INTAKE":
            percorso, meta = SmartRouter.route(user_input)

            if percorso in ("A", "B"):
                ss["triage_path"] = percorso
            elif percorso == "INFO":
                ss["triage_path"] = "INFO"
            elif has_symptom_keywords(user_input):
                ss["triage_path"] = "C"

            location = SmartRouter.extract_location(user_input)
            if location != "Non specificato":
                ss["collected_data"]["location"] = location
                ss["patient_location"] = location

        # ── Escalation dinamica C → A ──
        if current_path == "C":
            if SmartRouter.check_escalation(user_input):
                logger.warning("⚡ Escalation: C → A (symptom worsening)")
                ss["triage_path"] = "A"
                ss["urgency_level"] = max(ss.get("urgency_level", 3), 4)

    # ==================================================================
    # PHASE DETECTION
    # ==================================================================

    def _determine_phase(self, ss, user_input: str) -> str:
        """Mappa lo stato corrente nella macro-fase appropriata."""
        triage_path = ss.get("triage_path")
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)

        if triage_path == "INFO":
            return "INFO"

        if ss.get("current_phase") == "RECOMMENDATION":
            return "RECOMMENDATION"

        if triage_path is None:
            return "INTAKE"

        max_q = MAX_QUESTIONS.get(triage_path, 7)
        if q_count >= max_q and collected.get("chief_complaint"):
            return "RECOMMENDATION"

        if collected.get("chief_complaint"):
            return "CLINICAL_TRIAGE"

        return "INTAKE"

    # ==================================================================
    # EMERGENCY DETECTION
    # ==================================================================

    def check_emergency(self, message: str) -> Optional[Dict]:
        """Verifica keyword di emergenza."""
        message_lower = message.lower()

        for keyword in EMERGENCY_RULES.CRITICAL_RED_FLAGS:
            if keyword.lower() in message_lower:
                logger.warning(f"CRITICAL RED FLAG: {keyword}")
                return {
                    "text": self.get_emergency_response(keyword),
                    "urgency": "RED",
                    "type": "critical",
                    "call_118": True,
                }

        for keyword in EMERGENCY_RULES.MENTAL_HEALTH_CRISIS:
            if keyword.lower() in message_lower:
                logger.warning(f"MENTAL HEALTH CRISIS: {keyword}")
                return {
                    "text": self._get_mental_health_crisis_response(),
                    "urgency": "BLACK",
                    "type": "mental_health",
                    "call_118": True,
                }

        return None

    def get_emergency_response(self, symptom: str) -> str:
        return f"""🚨 **EMERGENZA RILEVATA** 🚨

Hai segnalato: **{symptom}**

Questo è un sintomo che richiede intervento immediato.

**CHIAMA SUBITO IL 118**

Mentre aspetti i soccorsi:
- Resta calmo e in un luogo sicuro
- Non muoverti se hai traumi
- Se possibile, fatti assistere da qualcuno
- Tieni il telefono a portata di mano"""

    def _get_mental_health_crisis_response(self) -> str:
        return """🆘 **SUPPORTO IMMEDIATO DISPONIBILE**

Capisco che stai attraversando un momento difficile. Non sei solo/a.

**NUMERI UTILI IMMEDIATI:**
- **118** — Emergenza sanitaria (24/7)
- **1522** — Antiviolenza e stalking (24/7)
- **Telefono Amico** — 02 2327 2327 (tutti i giorni 10-24)
- **Telefono Azzurro** — 19696 (per minori, 24/7)

Se hai bisogno di supporto immediato, contatta uno di questi numeri."""

    def get_fallback_response(self, phase: str) -> str:
        fallback = {
            "LOCATION": "Mi puoi dire in che città o zona ti trovi?",
            "CHIEF_COMPLAINT": "Qual è il problema principale?",
            "PAIN_SCALE": "Su una scala da 1 a 10, quanto è intenso il dolore?",
            "DEMOGRAPHICS": "Quanti anni hai?",
        }
        return fallback.get(phase, "Puoi fornirmi maggiori dettagli?")

    # ==================================================================
    # SUPABASE LOGGING
    # ==================================================================

    @staticmethod
    def _log_interaction(ss, user_input: str, response: str,
                         start_time: float) -> None:
        """Log su Supabase (best-effort, non blocca il flusso)."""
        if not SupabaseConfig.is_configured():
            return

        try:
            from supabase import create_client

            client = create_client(
                SupabaseConfig.get_url(), SupabaseConfig.get_key()
            )

            duration_ms = int((time.time() - start_time) * 1000)
            session_id = ss.get("session_id", "unknown")

            metadata = {
                "percorso": ss.get("triage_path", "N/D"),
                "phase": ss.get("current_phase", "N/D"),
                "urgenza": ss.get("urgency_level", 3),
                "question_count": ss.get("question_count", 0),
                "specializzazione": ss.get("specialization", "Generale"),
            }

            record = {
                "session_id": session_id,
                "user_input": user_input[:500],
                "bot_response": response[:2000],
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "processing_time_ms": duration_ms,
            }

            client.table(SupabaseConfig.TABLE_LOGS).insert(record).execute()
        except Exception as e:
            logger.warning(f"Supabase log failed (non-blocking): {e}")

    # ==================================================================
    # LEGACY: get_ai_response  (backward-compatible)
    # ==================================================================

    def get_ai_response(self, user_input: str, context: Dict[str, Any]) -> str:
        """
        Metodo legacy — mantenuto per backward compatibility con
        triage_controller.  Internamente usa call_llm.
        """
        try:
            normalized_input = self._symptom_normalizer.normalize(user_input)
            macro_phase = self._detect_macro_phase(context)

            if macro_phase == "INTAKE":
                system_prompt = self._build_intake_prompt(context)
            elif macro_phase == "CLINICAL_TRIAGE":
                system_prompt = self._build_system_prompt_with_rag(
                    normalized_input, context
                )
            else:
                system_prompt = self._build_recommendation_prompt(context)

            ctx_section = self._build_context_section(
                context.get("collected_data", {})
            )
            user_msg = f"{ctx_section}\n\nUtente: {normalized_input}"

            response = call_llm(
                self._groq_client, self._gemini_model,
                system_prompt, user_msg
            )
            return DiagnosisSanitizer.sanitize(response)

        except Exception as e:
            logger.error(f"get_ai_response error: {e}", exc_info=True)
            return "Mi dispiace, si è verificato un errore. Riprova."

    # ── Legacy helpers ──

    def _detect_macro_phase(self, context: Dict[str, Any]) -> str:
        phase = context.get("phase") or context.get("fase") or "INTENT_DETECTION"
        q_count = context.get("question_count", 0)
        cc = context.get("CHIEF_COMPLAINT") or context.get("chief_complaint")

        if phase in {"DISPOSITION", "RECOMMENDATION"} or q_count >= 7:
            return "RECOMMENDATION"
        if cc:
            return "CLINICAL_TRIAGE"
        return "INTAKE"

    def _build_intake_prompt(self, context: Dict[str, Any]) -> str:
        cd = context.get("collected_data", {})
        return f"""{PROMPTS['base_rules']}

## FASE 1 — ACCOGLIENZA (SENZA RAG)
Concentrati su raccogliere età, sesso, comune/località e sintomo.
Dati raccolti: Età={cd.get('age','N/D')}, Sesso={cd.get('sex','N/D')}, Località={cd.get('location','N/D')}
"""

    def _build_system_prompt_with_rag(self, symptoms: str,
                                      context: Dict[str, Any]) -> str:
        phase = context.get("phase", "CHIEF_COMPLAINT")
        rag_ctx = get_rag_context(symptoms, phase)
        cd = context.get("collected_data", {})

        return f"""{PROMPTS['base_rules']}

## DATI PAZIENTE
- Età: {cd.get('age','N/D')} | Sesso: {cd.get('sex','N/D')} | Località: {cd.get('location','N/D')}
- Percorso: {context.get('percorso','C')}

{rag_ctx}

Genera UNA domanda con 3 opzioni A, B, C.
"""

    def _build_recommendation_prompt(self, context: Dict[str, Any]) -> str:
        cd = context.get("collected_data", {})
        return f"""{PROMPTS['base_rules']}

{PROMPTS['disposition_sbar']}

## DATI PAZIENTE
Sintomo: {cd.get('chief_complaint','N/D')} | Dolore: {cd.get('pain_scale','N/D')}
Età: {cd.get('age','N/D')} | Sesso: {cd.get('sex','N/D')} | Località: {cd.get('location','N/D')}
Urgenza: {context.get('urgency_level','N/D')}

Genera SBAR conciso.
"""

    def _build_context_section(self, collected_data: Dict) -> str:
        parts = ["=== DATI RACCOLTI ==="]
        for key in ("location", "chief_complaint", "pain_scale",
                     "age", "sex", "onset", "history"):
            if key in collected_data:
                parts.append(f"{key}: {collected_data[key]}")
        parts.append("=" * 20)
        return "\n".join(parts)


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
