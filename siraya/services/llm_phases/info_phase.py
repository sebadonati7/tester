"""
SIRAYA — INFO PHASE
V4.0: Branch INFORMAZIONI — interroga master_kb.json.
"""

import json
import logging
from typing import Dict, Any

from ..llm_utils import PROMPTS, call_llm

logger = logging.getLogger(__name__)


class InfoPhase:
    """Branch INFORMAZIONI — risponde a domande su orari, indirizzi, servizi."""

    def __init__(self, groq_client, gemini_model):
        self._groq = groq_client
        self._gemini = gemini_model

    # ------------------------------------------------------------------
    # MAIN HANDLER
    # ------------------------------------------------------------------

    def handle(self, user_input: str, ss) -> str:
        """Gestisce richieste informative."""
        info_ctx = self._search_kb(user_input)

        prompt = f"""{PROMPTS['base_rules']}

## BRANCH INFORMAZIONI
L'utente chiede informazioni su servizi sanitari.
Rispondi basandoti SOLO sui dati seguenti.

{info_ctx}

Fornisci una risposta chiara e completa.
Se non hai informazioni sufficienti, suggerisci di contattare il CUP regionale.
"""
        return call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")

    # ------------------------------------------------------------------
    # KNOWLEDGE BASE SEARCH
    # ------------------------------------------------------------------

    @staticmethod
    def _search_kb(user_input: str) -> str:
        """Cerca nel master_kb.json strutture corrispondenti alla query."""
        try:
            from ..data_loader import get_data_loader

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
                    contatti = fac.get('contatti', {})
                    telefono = (
                        contatti.get('telefono', 'N/D')
                        if isinstance(contatti, dict) else 'N/D'
                    )
                    info_ctx += (
                        f"- **{fac.get('nome', 'N/D')}** ({fac.get('tipologia', '')})\n"
                        f"  Indirizzo: {fac.get('indirizzo', 'N/D')}, {fac.get('comune', '')}\n"
                        f"  Orari: {json.dumps(fac.get('orari', {}), ensure_ascii=False)[:200]}\n"
                        f"  Telefono: {telefono}\n\n"
                    )
                return info_ctx
            else:
                return "(Nessun risultato trovato nel database.)\n"

        except Exception as e:
            logger.error(f"Info search failed: {e}")
            return "(Errore nella ricerca.)\n"

