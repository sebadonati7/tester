"""
SIRAYA — CLINICAL TRIAGE PHASE
V4.0: Domande A/B/C con RAG.  Percorsi Emergenza / Salute Mentale / Standard.
"""

import logging
from typing import Dict, Any, Optional

from ..llm_utils import (
    SymptomNormalizer, PROMPTS, MAX_QUESTIONS,
    call_llm, get_conversation_ctx, get_rag_context,
)

logger = logging.getLogger(__name__)


class TriagePhase:
    """
    FASE 2 — TRIAGE CLINICO.
    Attiva RAG.  Logica dei percorsi A/B/C.
    """

    def __init__(self, groq_client, gemini_model):
        self._groq = groq_client
        self._gemini = gemini_model
        self._normalizer = SymptomNormalizer()

    # ------------------------------------------------------------------
    # DISPATCHER
    # ------------------------------------------------------------------

    def handle(self, user_input: str, ss, triage_path: str) -> Optional[str]:
        """
        Route to specific path handler.

        Returns:
            Response string, or None if max questions reached
            (signals orchestrator to switch to RECOMMENDATION).
        """
        path = triage_path or "C"
        if path == "A":
            return self._path_a(user_input, ss)
        elif path == "B":
            return self._path_b(user_input, ss)
        else:
            return self._path_c(user_input, ss)

    # ------------------------------------------------------------------
    # Percorso A: Emergenza (Red/Orange)
    # ------------------------------------------------------------------

    def _path_a(self, user_input: str, ss) -> Optional[str]:
        """3-4 domande veloci per confermare urgenza.  SI/NO o scelta singola."""
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = get_conversation_ctx(ss)
        rag_ctx = get_rag_context(user_input, "FAST_TRIAGE_A")

        if q_count >= MAX_QUESTIONS["A"]:
            return None  # → RECOMMENDATION

        prompt = f"""{PROMPTS['base_rules']}

{PROMPTS['percorso_a']}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Sintomo: {collected.get('chief_complaint', 'N/D')}
- Domande poste finora: {q_count}/{MAX_QUESTIONS['A']}

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
        return call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")

    # ------------------------------------------------------------------
    # Percorso B: Salute Mentale (Black)
    # ------------------------------------------------------------------

    def _path_b(self, user_input: str, ss) -> Optional[str]:
        """Sotto-fasi: CONSENSO → ANAMNESI → VALUTAZIONE RISCHIO."""
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = get_conversation_ctx(ss)
        rag_ctx = get_rag_context(user_input, "VALUTAZIONE_RISCHIO_B")

        # Determine sub-phase
        if q_count == 0:
            sub_phase = "CONSENSO"
        elif q_count == 1:
            sub_phase = "PERCORSI_FARMACI"
        elif q_count < MAX_QUESTIONS["B"]:
            sub_phase = "VALUTAZIONE_RISCHIO"
        else:
            return None  # → RECOMMENDATION

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

        prompt = f"""{PROMPTS['base_rules']}

{PROMPTS['percorso_b']}

## SOTTO-FASE: {sub_phase}
{sub_instructions[sub_phase]}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Domande poste: {q_count}/{MAX_QUESTIONS['B']}

{rag_ctx}

{conv_ctx}
"""
        return call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")

    # ------------------------------------------------------------------
    # Percorso C: Standard (Green/Yellow)
    # ------------------------------------------------------------------

    def _path_c(self, user_input: str, ss) -> Optional[str]:
        """5-7 domande A/B/C.  Medicalizzazione input libero."""
        collected = ss.get("collected_data", {})
        q_count = ss.get("question_count", 0)
        conv_ctx = get_conversation_ctx(ss)
        rag_ctx = get_rag_context(user_input, "FASE_4_TRIAGE")

        if q_count >= MAX_QUESTIONS["C"]:
            return None  # → RECOMMENDATION

        # Medicalizzazione dell'input libero
        normalized_input = self._normalizer.normalize(user_input)
        medicalized_note = ""
        if normalized_input != user_input:
            medicalized_note = (
                f'\n(Nota: il paziente ha detto "{user_input}" '
                f'→ termine medico: "{normalized_input}")\n'
            )

        prompt = f"""{PROMPTS['base_rules']}

{PROMPTS['percorso_c']}

## DATI PAZIENTE
- Età: {collected.get('age', 'N/D')}
- Sesso: {collected.get('sex', 'N/D')}
- Località: {collected.get('location', 'N/D')}
- Sintomo principale: {collected.get('chief_complaint', 'N/D')}
- Scala dolore: {collected.get('pain_scale', 'N/D')}
- Domande poste finora: {q_count}/{MAX_QUESTIONS['C']}
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
        return call_llm(
            self._groq, self._gemini, prompt, f"Utente: {normalized_input}"
        )

