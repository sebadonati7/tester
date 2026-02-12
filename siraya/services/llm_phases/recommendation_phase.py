"""
SIRAYA — RECOMMENDATION PHASE
V4.0: SBAR generation + facility search.
"""

import json
import logging
from typing import Dict, Any

from ..llm_utils import PROMPTS, call_llm

logger = logging.getLogger(__name__)


class RecommendationPhase:
    """FASE 3 — DISPOSITION & SBAR."""

    def __init__(self, groq_client, gemini_model):
        self._groq = groq_client
        self._gemini = gemini_model

    # ------------------------------------------------------------------
    # MAIN HANDLER
    # ------------------------------------------------------------------

    def handle(self, ss) -> str:
        """Genera SBAR + ricerca struttura."""
        collected = ss.get("collected_data", {})

        # ── Ricerca struttura ──
        facility_text = self._search_facility(collected, ss)

        # ── Genera SBAR via LLM ──
        urgency = ss.get("urgency_level", 3)
        red_flags = ss.get("red_flags", [])
        triage_path = ss.get("triage_path", "C")

        prompt = f"""{PROMPTS['base_rules']}

{PROMPTS['disposition_sbar']}

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
        sbar_response = call_llm(
            self._groq, self._gemini, prompt, "Genera il report finale."
        )

        return sbar_response + facility_text

    # ------------------------------------------------------------------
    # FACILITY SEARCH HELPER
    # ------------------------------------------------------------------

    @staticmethod
    def _search_facility(collected: Dict, ss) -> str:
        """Cerca la struttura più adatta nel data_loader."""
        try:
            from ..data_loader import get_data_loader

            dl = get_data_loader()
            location = collected.get("location", "Bologna")
            spec = ss.get("specialization", "Generale")

            facilities = dl.find_facilities_smart(spec, location, limit=3)
            if facilities:
                top = facilities[0]
                contatti = top.get('contatti', {})
                telefono = (
                    contatti.get('telefono', 'N/D')
                    if isinstance(contatti, dict) else 'N/D'
                )
                return (
                    f"\n\n📍 **STRUTTURA CONSIGLIATA:**\n"
                    f"**{top.get('nome', 'N/D')}**\n"
                    f"{top.get('indirizzo', 'N/D')}, {top.get('comune', 'N/D')}\n"
                    f"📞 {telefono}"
                )
        except Exception as e:
            logger.error(f"Facility search failed: {e}")

        return ""

