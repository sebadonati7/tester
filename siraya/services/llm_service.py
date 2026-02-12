"""
SIRAYA Health Navigator — LLM Service  (The State Machine)
V3.0: generate_response() è l'UNICO entry-point per il frontend.

Architettura a 3 macro-fasi:
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
import difflib
import os
from typing import Dict, List, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from groq import Groq
import google.generativeai as genai

from ..config.settings import EMERGENCY_RULES, ClinicalMappings, RAGConfig, SupabaseConfig

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# SYMPTOM NORMALIZER  (medicalizzazione input libero)
# ============================================================================

class SymptomNormalizer:
    """Normalizza i sintomi descritti dall'utente usando fuzzy matching."""

    def __init__(self):
        self.canonical_kb = ClinicalMappings.CANONICAL_KB
        self.stop_words = ClinicalMappings.STOP_WORDS
        logger.info("SymptomNormalizer initialized")

    def _preprocess(self, text: str) -> str:
        text = text.lower().strip()
        words = text.split()
        filtered_words = [w for w in words if w not in self.stop_words]
        return " ".join(filtered_words)

    def normalize(self, user_symptom: str, threshold: float = 0.6) -> str:
        """
        Normalizza il sintomo usando fuzzy matching.

        Returns:
            Sintomo medicalizzato (es. "mi gira la testa" → "Vertigini")
        """
        preprocessed = self._preprocess(user_symptom)
        best_match = None
        best_score = 0.0

        for canonical_term, medical_term in self.canonical_kb.items():
            score = difflib.SequenceMatcher(
                None, preprocessed, canonical_term.lower()
            ).ratio()
            if score > best_score:
                best_score = score
                best_match = canonical_term

        if best_score >= threshold and best_match:
            medical = self.canonical_kb.get(best_match, best_match)
            logger.info(
                f"Normalized '{user_symptom}' → '{medical}' (score: {best_score:.2f})"
            )
            return medical

        return user_symptom


# ============================================================================
# DIAGNOSIS SANITIZER
# ============================================================================

class DiagnosisSanitizer:
    """Blocca diagnosi e prescrizioni non autorizzate."""

    FORBIDDEN_PATTERNS = [
        r'\bhai\s+(la|il|un|una)\b.*\b(malattia|patologia|sindrome|infezione|disturbo)\b',
        r'\bsoffri\s+di\b',
        r'\bdiagnosi\s+(di|è)\b',
        r'\bprescrivo\b',
        r'\bterapia\s+(con|di)\b',
        r'\bdevi\s+prendere\b',
        r'\bti\s+consiglio\s+(di\s+prendere|il\s+farmaco)\b',
        r'\bassumi\s+(questo|il)\s+farmaco\b',
        r'\b(è|sei)\s+(sicuramente|probabilmente)\s+un[ao]?\b',
    ]

    WARNING_MESSAGE = (
        "\n\n⚠️ **ATTENZIONE**: Questa risposta potrebbe contenere elementi "
        "non autorizzati.  SIRAYA non può fornire diagnosi o prescrizioni "
        "mediche.  Consulta sempre un professionista sanitario qualificato."
    )

    @staticmethod
    def sanitize(response_text: str) -> str:
        if not response_text:
            return response_text
        text_lower = response_text.lower()
        for pattern in DiagnosisSanitizer.FORBIDDEN_PATTERNS:
            if re.search(pattern, text_lower):
                logger.warning(f"Forbidden pattern detected: {pattern}")
                return response_text + DiagnosisSanitizer.WARNING_MESSAGE
        return response_text


# ============================================================================
# LLM SERVICE  — THE STATE MACHINE
# ============================================================================

class LLMService:
    """
    Servizio LLM autonomo.

    Metodo pubblico principale:
        generate_response(user_input, session_state) → str

    Internamente gestisce:
        - Emergency detection
        - Smart routing  (Percorso A / B / C / INFO)
        - Fasi INTAKE → CLINICAL_TRIAGE → RECOMMENDATION
        - Lazy RAG (solo nella fase clinica)
        - Generazione domande A/B/C e medicalizzazione
        - SBAR + ricerca struttura
        - Logging Supabase
    """

    # Max domande per percorso
    MAX_QUESTIONS = {"A": 4, "B": 6, "C": 7, "INFO": 0}

    def __init__(self):
        self._groq_client: Optional[Groq] = None
        self._gemini_model = None
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._symptom_normalizer = SymptomNormalizer()
        self._prompts = self._load_prompts()
        self._init_clients()
        logger.info("LLMService initialized")

    # ------------------------------------------------------------------
    # CLIENT INIT
    # ------------------------------------------------------------------

    def _init_clients(self) -> None:
        """Inizializza i client Groq e Gemini."""
        # Groq
        try:
            groq_api_key = None
            if hasattr(st, "secrets"):
                try:
                    groq_api_key = st.secrets["groq"]["api_key"]
                except Exception:
                    groq_api_key = st.secrets.get("GROQ_API_KEY")
            if not groq_api_key:
                groq_api_key = os.getenv("GROQ_API_KEY")
            if groq_api_key:
                self._groq_client = Groq(api_key=groq_api_key)
                logger.info("Groq client initialized")
        except Exception as e:
            logger.error(f"Groq init failed: {e}")

        # Gemini
        try:
            gemini_api_key = None
            if hasattr(st, "secrets"):
                try:
                    gemini_api_key = st.secrets["gemini"]["api_key"]
                except Exception:
                    gemini_api_key = st.secrets.get("GEMINI_API_KEY")
            if not gemini_api_key:
                gemini_api_key = os.getenv("GEMINI_API_KEY")
            if gemini_api_key:
                genai.configure(api_key=gemini_api_key)
                self._gemini_model = genai.GenerativeModel("gemini-pro")
                logger.info("Gemini client initialized")
        except Exception as e:
            logger.error(f"Gemini init failed: {e}")

    def is_available(self) -> bool:
        """Almeno un LLM disponibile?"""
        return self._groq_client is not None or self._gemini_model is not None

    # ------------------------------------------------------------------
    # PROMPT TEMPLATES
    # ------------------------------------------------------------------

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "base_rules": (
                "Sei SIRAYA, un assistente AI per il triage sanitario in Emilia-Romagna.\n"
                "IMPORTANTE:\n"
                "- Fai UNA sola domanda alla volta\n"
                "- NON fornire diagnosi mediche\n"
                "- NON prescrivere farmaci o terapie\n"
                "- Usa linguaggio chiaro e professionale\n"
                "- Mostra empatia e rassicurazione\n"
                "- Rispondi SEMPRE in italiano"
            ),
            "percorso_a": (
                "PERCORSO A — EMERGENZA (Red/Orange)\n"
                "Gestisci situazioni ad alta urgenza.\n"
                "Fai 3-4 domande RAPIDE per confermare l'emergenza.\n"
                "Formato: domande chiuse SI/NO o scelta singola.\n"
                "Sii diretto, professionale, veloce.\n"
                "Obiettivo: confermare/escludere Codice Rosso/Arancione."
            ),
            "percorso_b": (
                "PERCORSO B — SALUTE MENTALE (Black)\n"
                "Gestisci crisi psichiatriche e disagio mentale.\n"
                "Tono: empatico, non giudicante, rassicurante.\n"
                "Fase 1: Chiedi il CONSENSO per domande personali.\n"
                "Fase 2: Chiedi se l'utente ha percorsi terapeutici/farmaci.\n"
                "Fase 3: Valuta il rischio con domande dal protocollo.\n"
                "Se emergenza → suggerisci 118 + hotline (1522, Telefono Amico).\n"
                "Se non emergenza → guida verso servizio territoriale (CSM/Consultorio)."
            ),
            "percorso_c": (
                "PERCORSO C — STANDARD (Green/Yellow)\n"
                "Gestisci situazioni non urgenti.\n"
                "Genera domande con 3 opzioni A, B, C.\n"
                "Range ottimale: 5-7 domande totali.\n"
                "Se l'utente scrive testo libero, MEDICALIZZA il termine "
                "(es. 'mi gira la testa' → 'Vertigini') e rigenera 3 nuove opzioni.\n"
                "Se l'utente aggiunge sintomi gravi → considera Percorso A ed escalation."
            ),
            "disposition_sbar": (
                "Genera un report SBAR per il personale sanitario.\n"
                "S) Situation — sintomo principale e urgenza\n"
                "B) Background — età, sesso, storia rilevante\n"
                "A) Assessment — valutazione codice colore e rischi\n"
                "R) Recommendation — struttura consigliata e azioni"
            ),
        }

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

        Side-effects su session_state:
            - current_phase, triage_path, question_count
            - collected_data, pending_survey_options
            - urgency_level, specialization, red_flags
        """
        start_time = time.time()

        # ── 0. ENSURE DEFAULTS ──────────────────────────────────────
        self._ensure_session_defaults(session_state)

        # ── 1. EMERGENCY CHECK (sempre prioritario) ─────────────────
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

        # ── 2. EXTRACT DATA FROM USER INPUT ─────────────────────────
        self._extract_inline_data(user_input, session_state)

        # ── 3. SMART ROUTING (percorso A/B/C/INFO) ──────────────────
        self._smart_route(user_input, session_state)

        # ── 4. DETERMINE MACRO PHASE ────────────────────────────────
        phase = self._determine_phase(session_state, user_input)
        session_state["current_phase"] = phase

        # ── 5. PHASE DISPATCH ───────────────────────────────────────
        if phase == "INTAKE":
            response = self._phase_intake(user_input, session_state)
        elif phase == "CLINICAL_TRIAGE":
            response = self._phase_clinical(user_input, session_state)
        elif phase == "RECOMMENDATION":
            response = self._phase_recommendation(session_state)
        elif phase == "INFO":
            response = self._phase_info(user_input, session_state)
        else:
            response = self._phase_intake(user_input, session_state)

        # ── 6. SANITIZE ────────────────────────────────────────────
        response = DiagnosisSanitizer.sanitize(response)

        # ── 7. PARSE A/B/C OPTIONS ─────────────────────────────────
        options = self._parse_options(response)
        session_state["pending_survey_options"] = options

        # ── 8. INCREMENT QUESTION COUNT ─────────────────────────────
        session_state["question_count"] = (
            session_state.get("question_count", 0) + 1
        )

        # ── 9. SUPABASE LOGGING ────────────────────────────────────
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

        # Se il percorso non è ancora assegnato, o siamo ancora in INTAKE
        if current_path is None or ss.get("current_phase") == "INTAKE":
            percorso, meta = SmartRouter.route(user_input)

            if percorso in ("A", "B"):
                # Emergenza o salute mentale: assegna subito
                ss["triage_path"] = percorso
            elif percorso == "INFO":
                ss["triage_path"] = "INFO"
            elif self._has_symptom_keywords(user_input):
                # Standard triage — c'è un sintomo
                ss["triage_path"] = "C"
            # else: resta None → siamo ancora in INTAKE

            # Estrai località se presente
            location = SmartRouter.extract_location(user_input)
            if location != "Non specificato":
                ss["collected_data"]["location"] = location
                ss["patient_location"] = location

        # ── Escalation dinamica durante il triage ──
        # Se durante il flusso C emergono sintomi gravi → switch a A
        if current_path == "C":
            percorso_check, _ = SmartRouter.route(user_input)
            if percorso_check == "A":
                logger.warning("⚡ Escalation: C → A")
                ss["triage_path"] = "A"
                ss["urgency_level"] = max(ss.get("urgency_level", 3), 4)

    # ==================================================================
    # PHASE DETECTION
    # ==================================================================

    def _determine_phase(self, ss, user_input: str) -> str:
        """
        Mappa lo stato corrente in INTAKE / CLINICAL_TRIAGE /
        RECOMMENDATION / INFO.
        """
        triage_path = ss.get("triage_path")
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)

        # Branch info
        if triage_path == "INFO":
            return "INFO"

        # Se la fase attuale è già RECOMMENDATION, resta lì
        if ss.get("current_phase") == "RECOMMENDATION":
            return "RECOMMENDATION"

        # Se non abbiamo ancora un percorso → INTAKE
        if triage_path is None:
            return "INTAKE"

        # Controlla se il triage è concluso per numero domande
        max_q = self.MAX_QUESTIONS.get(triage_path, 7)
        if q_count >= max_q and collected.get("chief_complaint"):
            return "RECOMMENDATION"

        # Se abbiamo un chief_complaint → CLINICAL_TRIAGE
        if collected.get("chief_complaint"):
            return "CLINICAL_TRIAGE"

        # Se c'è un percorso ma manca il sintomo — INTAKE (raccolta dati)
        return "INTAKE"

    # ==================================================================
    # DATA EXTRACTION
    # ==================================================================

    def _extract_inline_data(self, user_input: str, ss) -> None:
        """Estrae dati strutturati dall'input utente (età, sesso, località, dolore, sintomo)."""
        collected = ss.get("collected_data", {})
        text = user_input.lower().strip()

        # ── Età ──
        age_match = re.search(r'\b(\d{1,3})\s*anni?\b', text)
        if age_match:
            age = int(age_match.group(1))
            if 0 < age < 120:
                collected["age"] = age
                ss["patient_age"] = age

        # ── Sesso ──
        if any(w in text for w in ["maschio", "uomo", "ragazzo", "m "]):
            collected["sex"] = "M"
            ss["patient_sex"] = "M"
        elif any(w in text for w in ["femmina", "donna", "ragazza", "f "]):
            collected["sex"] = "F"
            ss["patient_sex"] = "F"

        # ── Scala dolore ──
        pain_match = re.search(r'\b([0-9]|10)\s*/?\s*(?:su\s*)?10\b', text)
        if pain_match:
            pain = int(pain_match.group(1))
            collected["pain_scale"] = pain
            ss["pain_scale"] = pain

        # ── Località (fallback semplice) ──
        loc_match = re.search(
            r'(?:mi trovo|sono|abito|vivo)\s+a\s+([A-Za-zÀ-ù]+)', user_input
        )
        if loc_match and not collected.get("location"):
            loc = loc_match.group(1).title()
            collected["location"] = loc
            ss["patient_location"] = loc

        # ── Sintomo principale (medicalizzazione) ──
        if not collected.get("chief_complaint"):
            normalized = self._symptom_normalizer.normalize(user_input)
            if normalized != user_input:
                # Il normalizzatore ha riconosciuto un sintomo clinico
                collected["chief_complaint"] = normalized
                ss["chief_complaint"] = normalized
            elif self._has_symptom_keywords(user_input):
                # Keyword diretta
                collected["chief_complaint"] = user_input.strip()[:120]
                ss["chief_complaint"] = collected["chief_complaint"]

        ss["collected_data"] = collected

    def _has_symptom_keywords(self, text: str) -> bool:
        """Verifica se il testo contiene keyword sintomatiche note."""
        text_lower = text.lower()
        for symptom in ClinicalMappings.SINTOMI_COMUNI:
            if symptom in text_lower:
                return True
        for term in ClinicalMappings.CANONICAL_KB:
            if term in text_lower:
                return True
        # Pattern generici ("ho mal di …", "mi fa male …")
        if re.search(r'\b(mal di|dolore|mi fa male|bruciore|nausea|febbre)\b', text_lower):
            return True
        return False

    # ==================================================================
    # PHASE HANDLERS
    # ==================================================================

    # ── FASE 1: INTAKE (accoglienza, dati base, ZERO RAG) ────────────

    def _phase_intake(self, user_input: str, ss) -> str:
        """
        FASE 1 — ACCOGLIENZA & INTAKE.
        Usa SOLO la conoscenza generale dell'LLM.
        Raccoglie: nome/età, sesso, comune, sintomo iniziale.
        """
        collected = ss.get("collected_data", {})
        conv_ctx = self._get_conversation_ctx(ss)

        missing = []
        if not collected.get("age"):
            missing.append("età")
        if not collected.get("sex"):
            missing.append("sesso")
        if not collected.get("location"):
            missing.append("comune di residenza (Emilia-Romagna)")
        if not collected.get("chief_complaint"):
            missing.append("sintomo principale o motivo del contatto")

        prompt = f"""{self._prompts['base_rules']}

## FASE 1 — ACCOGLIENZA & INTAKE (SENZA RAG)
NON usare protocolli clinici. NON citare fonti tecniche.
NON assegnare codici colore. NON fare domande cliniche approfondite.

Il tuo obiettivo ora è raccogliere le informazioni base del paziente.

Dati già raccolti:
- Età: {collected.get('age', 'non nota')}
- Sesso: {collected.get('sex', 'non noto')}
- Località: {collected.get('location', 'non nota')}
- Sintomo: {collected.get('chief_complaint', 'non ancora dichiarato')}

Dati ancora mancanti: {', '.join(missing) if missing else 'TUTTI RACCOLTI'}

Chiedi UNA SOLA delle informazioni mancanti per volta.
Se tutti i dati sono raccolti, conferma i dati e chiedi se vuole procedere al triage.
Sii professionale ma caloroso.

{conv_ctx}
"""
        user_msg = f"Utente: {user_input}"
        return self._call_llm(prompt, user_msg)

    # ── FASE 2: CLINICAL TRIAGE (Lazy RAG + Percorsi A/B/C) ─────────

    def _phase_clinical(self, user_input: str, ss) -> str:
        """
        FASE 2 — TRIAGE CLINICO.
        Attiva RAG solo qui. Logica dei percorsi A/B/C.
        """
        triage_path = ss.get("triage_path", "C")

        if triage_path == "A":
            return self._clinical_path_a(user_input, ss)
        elif triage_path == "B":
            return self._clinical_path_b(user_input, ss)
        else:
            return self._clinical_path_c(user_input, ss)

    # ── Percorso A: Emergenza (Red/Orange) ───────────────────────────

    def _clinical_path_a(self, user_input: str, ss) -> str:
        """
        Percorso A — EMERGENZA.
        3-4 domande veloci per confermare urgenza.
        Formato: SI/NO o scelta singola.
        Alla fine → RECOMMENDATION automatica.
        """
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = self._get_conversation_ctx(ss)
        rag_ctx = self._get_rag_context(user_input, "FAST_TRIAGE_A")

        # Se raggiungiamo il max domande → switch a RECOMMENDATION
        if q_count >= self.MAX_QUESTIONS["A"]:
            ss["current_phase"] = "RECOMMENDATION"
            return self._phase_recommendation(ss)

        prompt = f"""{self._prompts['base_rules']}

{self._prompts['percorso_a']}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Sintomo: {collected.get('chief_complaint', 'N/D')}
- Domande poste finora: {q_count}/{self.MAX_QUESTIONS['A']}

{rag_ctx}

ISTRUZIONI:
Genera UNA domanda rapida per confermare/escludere l'emergenza.
Formato: domanda + 2 opzioni (SI / NO) oppure 2-3 opzioni sintetiche.

Esempio:
"Il dolore si irradia al braccio sinistro o alla mascella?"
A) SÌ
B) NO

Genera ora la domanda più critica.

{conv_ctx}
"""
        user_msg = f"Utente: {user_input}"
        return self._call_llm(prompt, user_msg)

    # ── Percorso B: Salute Mentale (Black) ───────────────────────────

    def _clinical_path_b(self, user_input: str, ss) -> str:
        """
        Percorso B — SALUTE MENTALE.
        Sotto-fasi: CONSENSO → ANAMNESI → VALUTAZIONE RISCHIO.

        CASO 1 (Emergenza): → 118 + hotline.
        CASO 2 (Non emergenza): → CSM / Consultorio.
        """
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = self._get_conversation_ctx(ss)
        rag_ctx = self._get_rag_context(user_input, "VALUTAZIONE_RISCHIO_B")

        # Sotto-fase basata su q_count
        if q_count == 0:
            sub_phase = "CONSENSO"
        elif q_count == 1:
            sub_phase = "PERCORSI_FARMACI"
        elif q_count < self.MAX_QUESTIONS["B"]:
            sub_phase = "VALUTAZIONE_RISCHIO"
        else:
            ss["current_phase"] = "RECOMMENDATION"
            return self._phase_recommendation(ss)

        sub_instructions = {
            "CONSENSO": (
                "Chiedi il CONSENSO con empatia:\n"
                '"Mi sembra di capire che stai attraversando un momento '
                'difficile. Se sei d\'accordo, vorrei farti alcune domande '
                'personali per capire come esserti utile."\n'
                "Opzioni: A) ACCETTO  B) NO, preferisco non rispondere"
            ),
            "PERCORSI_FARMACI": (
                "Chiedi se l'utente ha già intrapreso percorsi terapeutici "
                "o sta assumendo farmaci.\n"
                "Input aperto (testo libero)."
            ),
            "VALUTAZIONE_RISCHIO": (
                "Usa i protocolli del Knowledge Base per formulare "
                "domande di valutazione rischio.\n"
                "Se rilevi emergenza → tono asciutto: "
                "'Stiamo analizzando una situazione delicata che merita "
                "supporto specifico.' → Suggerisci 118 e hotline "
                "(1522, Telefono Amico 02 2327 2327).\n"
                "Se escludi emergenza → 'Ti ringrazio per aver condiviso "
                "questo con me. Vorrei farti qualche altra domanda per "
                "capire quale servizio consigliarti.' → Richiedi Età → "
                "Suggerisci CSM o Consultorio basato su residenza."
            ),
        }

        prompt = f"""{self._prompts['base_rules']}

{self._prompts['percorso_b']}

## SOTTO-FASE: {sub_phase}
{sub_instructions[sub_phase]}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Domande poste: {q_count}/{self.MAX_QUESTIONS['B']}

{rag_ctx}

{conv_ctx}
"""
        user_msg = f"Utente: {user_input}"
        return self._call_llm(prompt, user_msg)

    # ── Percorso C: Standard (Green/Yellow) ──────────────────────────

    def _clinical_path_c(self, user_input: str, ss) -> str:
        """
        Percorso C — TRIAGE STANDARD.

        Logica A/B/C:
        - Ogni domanda ha 3 opzioni (A, B, C).
        - Se l'utente scrive testo libero → medicalizza + rigenera opzioni.
        - Range ottimale: 5-7 domande.
        - Se emergono sintomi gravi → escalation a Percorso A (gestito in _smart_route).
        """
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = self._get_conversation_ctx(ss)
        rag_ctx = self._get_rag_context(user_input, "FASE_4_TRIAGE")

        # Se raggiungiamo il max domande → switch a RECOMMENDATION
        if q_count >= self.MAX_QUESTIONS["C"]:
            ss["current_phase"] = "RECOMMENDATION"
            return self._phase_recommendation(ss)

        # Medicalizzazione dell'input libero
        normalized_input = self._symptom_normalizer.normalize(user_input)
        medicalized_note = ""
        if normalized_input != user_input:
            medicalized_note = (
                f'\n(Nota: il paziente ha detto "{user_input}" '
                f'→ termine medico: "{normalized_input}")\n'
            )

        prompt = f"""{self._prompts['base_rules']}

{self._prompts['percorso_c']}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Sesso: {collected.get('sex', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Sintomo principale: {collected.get('chief_complaint', 'N/D')}
- Scala dolore: {collected.get('pain_scale', 'N/D')}
- Domande poste finora: {q_count}/{self.MAX_QUESTIONS['C']}
{medicalized_note}

{rag_ctx}

ISTRUZIONI:
Genera ESATTAMENTE UNA domanda diagnostica basata sui protocolli clinici.

**Formato obbligatorio:**
Testo della domanda, poi 3 opzioni:

A) [opzione 1]
B) [opzione 2]
C) [opzione 3]

Se il paziente ha risposto con testo libero, MEDICALIZZA il termine
e rigenera 3 opzioni specifiche.

Se emergono nuovi sintomi gravi → segnala l'escalation.

{conv_ctx}
"""
        user_msg = f"Utente: {normalized_input}"
        return self._call_llm(prompt, user_msg)

    # ── FASE 3: RECOMMENDATION (SBAR + struttura) ───────────────────

    def _phase_recommendation(self, ss) -> str:
        """
        FASE 3 — DISPOSITION & SBAR.
        Cerca la struttura via data_loader e genera report SBAR.
        """
        collected = ss.get("collected_data", {})

        # ── Ricerca struttura ──
        facility_text = ""
        try:
            from ..services.data_loader import get_data_loader
            dl = get_data_loader()
            location = collected.get("location", "Bologna")
            spec = ss.get("specialization", "Generale")

            facilities = dl.find_facilities_smart(spec, location, limit=3)
            if facilities:
                top = facilities[0]
                facility_text = (
                    f"\n\n📍 **STRUTTURA CONSIGLIATA:**\n"
                    f"**{top.get('nome', 'N/D')}**\n"
                    f"{top.get('indirizzo', 'N/D')}, {top.get('comune', 'N/D')}\n"
                    f"📞 {top.get('contatti', {}).get('telefono', 'N/D') if isinstance(top.get('contatti'), dict) else 'N/D'}"
                )
        except Exception as e:
            logger.error(f"Facility search failed: {e}")

        # ── Genera SBAR via LLM ──
        urgency = ss.get("urgency_level", 3)
        red_flags = ss.get("red_flags", [])
        triage_path = ss.get("triage_path", "C")

        prompt = f"""{self._prompts['base_rules']}

{self._prompts['disposition_sbar']}

## DATI PAZIENTE (COMPLETI)
- Età: {collected.get('age', 'N/D')}
- Sesso: {collected.get('sex', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Sintomo principale: {collected.get('chief_complaint', 'N/D')}
- Scala dolore: {collected.get('pain_scale', 'N/D')}
- Red flags: {', '.join(red_flags) if red_flags else 'Nessuna'}
- Percorso: {triage_path}
- Urgenza stimata: {urgency}/5
- Specializzazione: {ss.get('specialization', 'Generale')}

Genera:
1) Un messaggio al paziente che riassume cosa fare ora.
2) Un blocco SBAR in italiano (max 8 righe).

NON aggiungere opzioni A/B/C. Questa è la fase finale.
"""
        sbar_response = self._call_llm(prompt, "Genera il report finale.")

        # Aggiungi la struttura al messaggio
        final = sbar_response + facility_text

        # Segna fase come completata
        ss["current_phase"] = "RECOMMENDATION"

        return final

    # ── BRANCH INFO ──────────────────────────────────────────────────

    def _phase_info(self, user_input: str, ss) -> str:
        """
        Branch INFORMAZIONI — interroga master_kb.json per rispondere
        a domande su orari, indirizzi, servizi.
        """
        # Cerca nel JSON locale
        try:
            from ..services.data_loader import get_data_loader
            dl = get_data_loader()
            facilities = dl.get_all_facilities()

            # Ricerca fuzzy sull'input
            results = []
            terms = user_input.lower().split()
            for f in facilities:
                score = 0
                searchable = json.dumps(f, ensure_ascii=False).lower()
                for term in terms:
                    if len(term) > 2 and term in searchable:
                        score += 1
                if score > 0:
                    results.append((score, f))

            results.sort(key=lambda x: x[0], reverse=True)
            top_results = results[:3]

            if top_results:
                info_ctx = "## RISULTATI DA MASTER_KB.JSON\n"
                for _, fac in top_results:
                    info_ctx += (
                        f"- **{fac.get('nome', 'N/D')}** ({fac.get('tipologia', '')})\n"
                        f"  Indirizzo: {fac.get('indirizzo', 'N/D')}, {fac.get('comune', '')}\n"
                        f"  Orari: {json.dumps(fac.get('orari', {}), ensure_ascii=False)[:200]}\n"
                        f"  Telefono: {fac.get('contatti', {}).get('telefono', 'N/D') if isinstance(fac.get('contatti'), dict) else 'N/D'}\n\n"
                    )
            else:
                info_ctx = "(Nessun risultato trovato nel database.)\n"

        except Exception as e:
            logger.error(f"Info search failed: {e}")
            info_ctx = "(Errore nella ricerca.)\n"

        prompt = f"""{self._prompts['base_rules']}

## BRANCH INFORMAZIONI
L'utente chiede informazioni su servizi sanitari.
Rispondi basandoti SOLO sui dati seguenti.

{info_ctx}

Fornisci una risposta chiara e completa.
Se non hai informazioni sufficienti, suggerisci di contattare il CUP regionale.
"""
        return self._call_llm(prompt, f"Utente: {user_input}")

    # ==================================================================
    # RAG HELPER
    # ==================================================================

    def _get_rag_context(self, user_input: str, rag_phase: str) -> str:
        """Recupera contesto RAG se disponibile."""
        try:
            from ..services.rag_service import get_rag_service
            rag = get_rag_service()

            if rag.should_use_rag(rag_phase, user_input):
                docs = rag.retrieve_context(user_input, k=3)
                if docs:
                    return rag.format_context_for_llm(docs, rag_phase)
        except Exception as e:
            logger.warning(f"RAG unavailable: {e}")

        return ""

    # ==================================================================
    # LOW-LEVEL LLM CALL
    # ==================================================================

    def _call_llm(self, system_prompt: str, user_message: str) -> str:
        """
        Chiamata API al modello (Groq primario, Gemini fallback).

        Returns:
            Risposta testuale o fallback statico.
        """
        # ── Groq ──
        if self._groq_client:
            try:
                response = self._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.7,
                    max_tokens=1024,
                )
                text = response.choices[0].message.content
                if text and text.strip():
                    return text
            except Exception as e:
                logger.error(f"Groq call failed: {e}")

        # ── Gemini fallback ──
        if self._gemini_model:
            try:
                full = f"{system_prompt}\n\n{user_message}"
                response = self._gemini_model.generate_content(full)
                text = response.text
                if text and text.strip():
                    return text
            except Exception as e:
                logger.error(f"Gemini call failed: {e}")

        # ── Fallback statico ──
        return (
            "Mi dispiace, si è verificato un problema tecnico. "
            "Puoi riprovare tra qualche secondo?"
        )

    # ==================================================================
    # PARSE A/B/C OPTIONS
    # ==================================================================

    @staticmethod
    def _parse_options(response_text: str) -> Optional[List[str]]:
        """
        Estrae opzioni A/B/C dalla risposta LLM.

        Cerca pattern tipo:
            A) testo  /  A. testo  /  A: testo
        """
        if not response_text:
            return None

        patterns = [
            # A) testo  B) testo  C) testo
            r'[A-C]\)\s*(.+)',
            # A. testo  B. testo
            r'[A-C]\.\s*(.+)',
        ]

        options = []
        for line in response_text.split('\n'):
            stripped = line.strip()
            for pat in patterns:
                match = re.match(pat, stripped)
                if match:
                    options.append(stripped)
                    break

        # Deve avere almeno 2 opzioni per essere valido
        if len(options) >= 2:
            return options

        return None

    # ==================================================================
    # CONVERSATION CONTEXT
    # ==================================================================

    @staticmethod
    def _get_conversation_ctx(ss, max_messages: int = 8) -> str:
        """Formatta gli ultimi messaggi per il prompt LLM."""
        messages = ss.get("messages", [])
        if not messages:
            return ""

        recent = messages[-max_messages:]
        lines = ["## CONVERSAZIONE RECENTE"]
        for msg in recent:
            role = "Paziente" if msg.get("role") == "user" else "SIRAYA"
            content = msg.get("content", "")[:300]
            lines.append(f"{role}: {content}")

        return "\n".join(lines)

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
    # EMERGENCY DETECTION  (preserved from V2)
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
    # LEGACY: get_ai_response  (backward-compatible)
    # ==================================================================

    def get_ai_response(self, user_input: str, context: Dict[str, Any]) -> str:
        """
        Metodo legacy — mantenuto per backward compatibility con
        triage_controller.  Internamente usa _call_llm.
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

            response = self._call_llm(system_prompt, user_msg)
            return DiagnosisSanitizer.sanitize(response)

        except Exception as e:
            logger.error(f"get_ai_response error: {e}", exc_info=True)
            return "Mi dispiace, si è verificato un errore. Riprova."

    # ── Legacy helper: macro-phase detection ──

    def _detect_macro_phase(self, context: Dict[str, Any]) -> str:
        phase = context.get("phase") or context.get("fase") or "INTENT_DETECTION"
        q_count = context.get("question_count", 0)
        cc = context.get("CHIEF_COMPLAINT") or context.get("chief_complaint")

        if phase in {"DISPOSITION", "RECOMMENDATION"} or q_count >= 7:
            return "RECOMMENDATION"
        if cc:
            return "CLINICAL_TRIAGE"
        return "INTAKE"

    # ── Legacy prompt builders ──

    def _build_intake_prompt(self, context: Dict[str, Any]) -> str:
        cd = context.get("collected_data", {})
        return f"""{self._prompts['base_rules']}

## FASE 1 — ACCOGLIENZA (SENZA RAG)
Concentrati su raccogliere età, sesso, comune/località e sintomo.
Dati raccolti: Età={cd.get('age','N/D')}, Sesso={cd.get('sex','N/D')}, Località={cd.get('location','N/D')}
"""

    def _build_system_prompt_with_rag(self, symptoms: str,
                                      context: Dict[str, Any]) -> str:
        phase = context.get("phase", "CHIEF_COMPLAINT")
        rag_ctx = self._get_rag_context(symptoms, phase)
        cd = context.get("collected_data", {})

        return f"""{self._prompts['base_rules']}

## DATI PAZIENTE
- Età: {cd.get('age','N/D')} | Sesso: {cd.get('sex','N/D')} | Località: {cd.get('location','N/D')}
- Percorso: {context.get('percorso','C')}

{rag_ctx}

Genera UNA domanda con 3 opzioni A, B, C.
"""

    def _build_recommendation_prompt(self, context: Dict[str, Any]) -> str:
        cd = context.get("collected_data", {})
        return f"""{self._prompts['base_rules']}

{self._prompts['disposition_sbar']}

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
