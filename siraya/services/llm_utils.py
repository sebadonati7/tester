"""
SIRAYA Health Navigator — LLM Utilities
V4.0: Shared utilities for the modular LLM architecture.

Contains:
- SymptomNormalizer: medicalizzazione input libero
- DiagnosisSanitizer: blocca diagnosi non autorizzate
- Shared prompt templates
- LLM call helper with retry/backoff
- Conversation context builder
- RAG context helper
- Option parser for A/B/C responses
"""

import re
import time
import logging
import difflib
from typing import Dict, List, Optional

from ..config.settings import ClinicalMappings, APIConfig

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
# PROMPT TEMPLATES
# ============================================================================

PROMPTS: Dict[str, str] = {
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

# Max domande per percorso
MAX_QUESTIONS: Dict[str, int] = {"A": 4, "B": 6, "C": 7, "INFO": 0}


# ============================================================================
# LLM CALL WITH RETRY & BACKOFF
# ============================================================================

def call_llm(groq_client, gemini_model, system_prompt: str,
             user_message: str, max_retries: int = 2) -> str:
    """
    Chiamata API con retry e backoff esponenziale.
    Groq primario → Gemini fallback → fallback statico.
    """
    last_error = None

    # ── Groq (con retry) ──
    if groq_client:
        for attempt in range(max_retries + 1):
            try:
                response = groq_client.chat.completions.create(
                    model=APIConfig.GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=APIConfig.TEMPERATURE,
                    max_tokens=1024,
                )
                text = response.choices[0].message.content
                if text and text.strip():
                    return text
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    f"Groq attempt {attempt + 1}/{max_retries + 1} failed: "
                    f"{type(e).__name__} - {e}"
                )
                if attempt < max_retries:
                    time.sleep(wait)

    # ── Gemini fallback (con retry) ──
    if gemini_model:
        for attempt in range(max_retries + 1):
            try:
                full = f"{system_prompt}\n\n{user_message}"
                response = gemini_model.generate_content(full)
                text = response.text
                if text and text.strip():
                    return text
            except Exception as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    f"Gemini attempt {attempt + 1}/{max_retries + 1} failed: "
                    f"{type(e).__name__} - {e}"
                )
                if attempt < max_retries:
                    time.sleep(wait)

    # ── Fallback statico ──
    logger.error(f"All LLM calls failed. Last error: {last_error}")
    return (
        "Mi dispiace, si è verificato un problema tecnico temporaneo. "
        "Puoi riprovare tra qualche secondo?"
    )


# ============================================================================
# CONVERSATION CONTEXT BUILDER
# ============================================================================

def get_conversation_ctx(ss, max_messages: int = 8) -> str:
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


# ============================================================================
# RAG CONTEXT HELPER
# ============================================================================

def get_rag_context(user_input: str, rag_phase: str) -> str:
    """Recupera contesto RAG se disponibile."""
    try:
        from .rag_service import get_rag_service
        rag = get_rag_service()

        if rag.should_use_rag(rag_phase, user_input):
            docs = rag.retrieve_context(user_input, k=3)
            if docs:
                return rag.format_context_for_llm(docs, rag_phase)
    except Exception as e:
        logger.warning(f"RAG unavailable: {e}")

    return ""


# ============================================================================
# A/B/C OPTION PARSER
# ============================================================================

def parse_options(response_text: str) -> Optional[List[str]]:
    """
    Estrae opzioni A/B/C dalla risposta LLM.
    Cerca pattern tipo: A) testo  /  A. testo  /  A: testo
    """
    if not response_text:
        return None

    patterns = [
        r'[A-C]\)\s*(.+)',
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


# ============================================================================
# SYMPTOM KEYWORD DETECTION
# ============================================================================

def has_symptom_keywords(text: str) -> bool:
    """Verifica se il testo contiene keyword sintomatiche note."""
    text_lower = text.lower()
    for symptom in ClinicalMappings.SINTOMI_COMUNI:
        if symptom in text_lower:
            return True
    for term in ClinicalMappings.CANONICAL_KB:
        if term in text_lower:
            return True
    if re.search(r'\b(mal di|dolore|mi fa male|bruciore|nausea|febbre)\b', text_lower):
        return True
    return False

