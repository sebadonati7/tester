"""
SIRAYA — INTAKE PHASE
V4.0: Accoglienza & slot filling base.  ZERO RAG.
"""

import re
import logging
from typing import Dict, Any

from ...config.settings import ClinicalMappings
from ..llm_utils import (
    SymptomNormalizer, PROMPTS, call_llm,
    get_conversation_ctx, has_symptom_keywords,
)

logger = logging.getLogger(__name__)


class IntakePhase:
    """
    FASE 1 — ACCOGLIENZA & INTAKE.
    Usa SOLO la conoscenza generale dell'LLM.
    Raccoglie: nome/età, sesso, comune, sintomo iniziale.
    """

    def __init__(self, groq_client, gemini_model):
        self._groq = groq_client
        self._gemini = gemini_model
        self._normalizer = SymptomNormalizer()

    # ------------------------------------------------------------------
    # MAIN HANDLER
    # ------------------------------------------------------------------

    def handle(self, user_input: str, ss) -> str:
        """Gestisce l'intera fase INTAKE."""
        collected = ss.get("collected_data", {})
        conv_ctx = get_conversation_ctx(ss)

        missing = []
        if not collected.get("age"):
            missing.append("età")
        if not collected.get("sex"):
            missing.append("sesso")
        if not collected.get("location"):
            missing.append("comune di residenza (Emilia-Romagna)")
        if not collected.get("chief_complaint"):
            missing.append("sintomo principale o motivo del contatto")

        prompt = f"""{PROMPTS['base_rules']}

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
        return call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")

    # ------------------------------------------------------------------
    # DATA EXTRACTION
    # ------------------------------------------------------------------

    def extract_inline_data(self, user_input: str, ss) -> None:
        """Estrae dati strutturati dall'input (età, sesso, località, dolore, sintomo)."""
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
            normalized = self._normalizer.normalize(user_input)
            if normalized != user_input:
                collected["chief_complaint"] = normalized
                ss["chief_complaint"] = normalized
            elif has_symptom_keywords(user_input):
                collected["chief_complaint"] = user_input.strip()[:120]
                ss["chief_complaint"] = collected["chief_complaint"]

        ss["collected_data"] = collected

