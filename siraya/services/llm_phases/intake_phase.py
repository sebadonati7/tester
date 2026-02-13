"""
SIRAYA — INTAKE PHASE (SIRAYA PROTOCOL V2)
V2.0: Implementa i 5 pilastri del nuovo flusso.

Pilastri:
1. ENGAGE & MOTIVO: Primo contatto
2. SINTOMO PRINCIPALE: Identificazione chiara
3. LOCALIZZAZIONE & DOLORE: "Dove ti trovi ORA?" (non residenza) + VAS 1-10
4. INDAGINE CLINICA: (gestita in triage_phase.py con RAG)
5. DEMOGRAFIA & CHIUSURA: (gestita alla fine, prima di RECOMMENDATION)
"""

import re
import logging
from typing import Dict, Any, Optional

from ..llm_utils import (
    SymptomNormalizer, PROMPTS, call_llm,
    get_conversation_ctx, has_symptom_keywords,
)

logger = logging.getLogger(__name__)


class IntakePhase:
    """
    FASE 1 — ACCOGLIENZA & INTAKE (SIRAYA PROTOCOL).
    
    Gestisce:
    - ENGAGE & MOTIVO (primo messaggio)
    - SINTOMO PRINCIPALE (identificazione)
    - LOCALIZZAZIONE & DOLORE (dove ti trovi ORA + VAS)
    
    IMPORTANTE: NON chiede residenza, solo "dove ti trovi in questo momento".
    """

    def __init__(self, groq_client, gemini_model):
        self._groq = groq_client
        self._gemini = gemini_model
        self._normalizer = SymptomNormalizer()

    # ------------------------------------------------------------------
    # MAIN HANDLER
    # ------------------------------------------------------------------

    def handle(self, user_input: str, ss) -> str:
        """
        Gestisce l'intera fase INTAKE seguendo SIRAYA PROTOCOL.
        
        Ordine:
        1. ENGAGE (se primo messaggio)
        2. SINTOMO PRINCIPALE
        3. LOCALIZZAZIONE (dove ti trovi ORA)
        4. DOLORE (VAS 1-10)
        """
        collected = ss.get("collected_data", {})
        conv_ctx = get_conversation_ctx(ss)
        
        # Step 1: ENGAGE & MOTIVO (primo messaggio)
        if not collected.get("chief_complaint") and ss.get("question_count", 0) == 0:
            return self._handle_engage(user_input, ss)
        
        # Step 2: SINTOMO PRINCIPALE
        if not collected.get("chief_complaint"):
            return self._handle_symptom_identification(user_input, ss, conv_ctx)
        
        # Step 3: LOCALIZZAZIONE (dove ti trovi ORA, non residenza)
        if not collected.get("current_location"):
            return self._handle_location(user_input, ss, conv_ctx)
        
        # Step 4: DOLORE (VAS 1-10)
        if collected.get("pain_scale") is None:
            return self._handle_pain_assessment(user_input, ss, conv_ctx)
        
        # Intake completo → passa a CLINICAL_TRIAGE
        ss["intake_complete"] = True
        return "Grazie per le informazioni. Ora ti farò alcune domande per capire meglio la situazione."

    # ------------------------------------------------------------------
    # PILASTRO 1: ENGAGE & MOTIVO
    # ------------------------------------------------------------------

    def _handle_engage(self, user_input: str, ss) -> str:
        """Pilastro 1: Primo contatto - ENGAGE & MOTIVO."""
        prompt = f"""{PROMPTS['base_rules']}

## FASE 1 — ENGAGE & MOTIVO

Sei un assistente di triage sanitario professionale e empatico.

Il paziente ha appena iniziato la conversazione.

Il tuo obiettivo è:
1. Accogliere calorosamente
2. Chiedere il motivo del contatto in modo naturale
3. NON fare domande cliniche approfondite
4. NON chiedere dati anagrafici (età, sesso) ancora
5. NON chiedere dove abita (residenza)

Esempio di risposta:
"Ciao! Sono qui per aiutarti. Puoi dirmi brevemente qual è il motivo del tuo contatto oggi?"

Sii professionale ma caloroso. Usa un tono rassicurante.
"""
        response = call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")
        ss["question_count"] = ss.get("question_count", 0) + 1
        return response

    # ------------------------------------------------------------------
    # PILASTRO 2: SINTOMO PRINCIPALE
    # ------------------------------------------------------------------

    def _handle_symptom_identification(self, user_input: str, ss, conv_ctx: str) -> str:
        """Pilastro 2: Identificazione sintomo principale."""
        collected = ss.get("collected_data", {})
        
        # Estrai sintomo dall'input
        self.extract_inline_data(user_input, ss)
        collected = ss.get("collected_data", {})
        
        # Se abbiamo già il sintomo, conferma e passa avanti
        if collected.get("chief_complaint"):
            symptom = collected["chief_complaint"]
            ss["question_count"] = ss.get("question_count", 0) + 1
            return f"Ho capito: {symptom}. Ora ho bisogno di sapere dove ti trovi in questo momento per trovare la struttura più vicina."
        
        # Altrimenti chiedi il sintomo
        prompt = f"""{PROMPTS['base_rules']}

## FASE 2 — IDENTIFICAZIONE SINTOMO PRINCIPALE

Il paziente ha descritto il motivo del contatto, ma devi identificare chiaramente il sintomo principale.

Il tuo obiettivo:
1. Identificare il sintomo principale dal messaggio
2. Se non è chiaro, chiedi una descrizione più specifica
3. NON fare domande cliniche approfondite ancora
4. NON chiedere età, sesso, residenza

Esempio:
- Se dice "ho mal di testa" → conferma: "Capisco, hai mal di testa."
- Se dice "non sto bene" → chiedi: "Puoi descrivermi meglio quale sintomo ti preoccupa di più?"

Sii diretto e chiaro.

{conv_ctx}
"""
        response = call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")
        ss["question_count"] = ss.get("question_count", 0) + 1
        return response

    # ------------------------------------------------------------------
    # PILASTRO 3: LOCALIZZAZIONE (DOVE TI TROVI ORA)
    # ------------------------------------------------------------------

    def _handle_location(self, user_input: str, ss, conv_ctx: str) -> str:
        """
        Pilastro 3: Localizzazione.
        
        IMPORTANTE: Chiede "dove ti trovi ORA", NON "dove abiti".
        Questo è critico per calcolare la distanza reale dal PS.
        """
        collected = ss.get("collected_data", {})
        
        # Estrai località dall'input
        location = self._extract_current_location(user_input)
        
        if location and location != "Non specificato":
            collected["current_location"] = location
            ss["patient_location"] = location  # Per compatibilità
            ss["collected_data"] = collected
            ss["question_count"] = ss.get("question_count", 0) + 1
            return f"Perfetto, sei a {location}. Su una scala da 1 a 10, quanto è intenso il dolore o il disagio che provi? (1 = lieve, 10 = insopportabile)"
        
        # Chiedi localizzazione
        prompt = f"""{PROMPTS['base_rules']}

## FASE 3 — LOCALIZZAZIONE

IMPORTANTE: Devi chiedere "DOVE TI TROVI IN QUESTO MOMENTO", NON "dove abiti" o "dove vivi".

Questo è critico per calcolare la distanza reale dalla struttura sanitaria più vicina.

Il tuo obiettivo:
1. Chiedere dove si trova ORA il paziente
2. NON chiedere la residenza
3. Se possibile, estrarre il comune dal messaggio

Esempio:
"Per trovare la struttura più vicina, dove ti trovi in questo momento? (Indica il comune)"

Sii chiaro e diretto.

{conv_ctx}
"""
        response = call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")
        ss["question_count"] = ss.get("question_count", 0) + 1
        return response

    # ------------------------------------------------------------------
    # PILASTRO 3 (continuazione): DOLORE (VAS 1-10)
    # ------------------------------------------------------------------

    def _handle_pain_assessment(self, user_input: str, ss, conv_ctx: str) -> str:
        """Pilastro 3 (continuazione): Valutazione dolore VAS 1-10."""
        collected = ss.get("collected_data", {})
        
        # Estrai scala dolore dall'input
        pain_scale = self._extract_pain_scale(user_input)
        
        if pain_scale is not None:
            collected["pain_scale"] = pain_scale
            ss["pain_scale"] = pain_scale
            ss["collected_data"] = collected
            ss["question_count"] = ss.get("question_count", 0) + 1
            ss["intake_complete"] = True
            return "Grazie. Ora ti farò alcune domande più specifiche per capire meglio la situazione."
        
        # Chiedi scala dolore
        prompt = f"""{PROMPTS['base_rules']}

## FASE 3 (continuazione) — VALUTAZIONE DOLORE

Devi chiedere la scala del dolore VAS (Visual Analog Scale) da 1 a 10.

Il tuo obiettivo:
1. Chiedere l'intensità del dolore/disagio su scala 1-10
2. Spiegare brevemente la scala (1 = lieve, 10 = insopportabile)
3. Se il paziente non ha dolore, accettare "0" o "nessun dolore"

Esempio:
"Su una scala da 1 a 10, quanto è intenso il dolore o il disagio che provi? (1 = lieve, 10 = insopportabile)"

Sii chiaro e diretto.

{conv_ctx}
"""
        response = call_llm(self._groq, self._gemini, prompt, f"Utente: {user_input}")
        ss["question_count"] = ss.get("question_count", 0) + 1
        return response

    # ------------------------------------------------------------------
    # DATA EXTRACTION HELPERS
    # ------------------------------------------------------------------

    def extract_inline_data(self, user_input: str, ss) -> None:
        """Estrae dati strutturati dall'input (sintomo, località, dolore)."""
        collected = ss.get("collected_data", {})
        text = user_input.lower().strip()

        # ── Sintomo principale (medicalizzazione) ──
        if not collected.get("chief_complaint"):
            normalized = self._normalizer.normalize(user_input)
            if normalized != user_input:
                collected["chief_complaint"] = normalized
                ss["chief_complaint"] = normalized
            elif has_symptom_keywords(user_input):
                collected["chief_complaint"] = user_input.strip()[:120]
                ss["chief_complaint"] = collected["chief_complaint"]

        # ── Località corrente (dove ti trovi ORA) ──
        if not collected.get("current_location"):
            location = self._extract_current_location(user_input)
            if location and location != "Non specificato":
                collected["current_location"] = location
                ss["patient_location"] = location

        # ── Scala dolore ──
        if collected.get("pain_scale") is None:
            pain_scale = self._extract_pain_scale(user_input)
            if pain_scale is not None:
                collected["pain_scale"] = pain_scale
                ss["pain_scale"] = pain_scale

        ss["collected_data"] = collected

    def _extract_current_location(self, user_input: str) -> Optional[str]:
        """
        Estrae località corrente dal messaggio.
        
        Cerca pattern come:
        - "mi trovo a [comune]"
        - "sono a [comune]"
        - "sono in [comune]"
        - "[comune]" (se è un comune noto)
        """
        text = user_input.lower()
        
        # Pattern espliciti
        patterns = [
            r'(?:mi trovo|sono|sto)\s+(?:a|in)\s+([A-Z][a-zà-ù]+(?:\s+[A-Z][a-zà-ù]+)?)',
            r'(?:comune|città)\s+(?:di\s+)?([A-Z][a-zà-ù]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                return match.group(1).title()
        
        # Fallback: cerca comuni noti dell'Emilia-Romagna
        comuni_er = [
            "Bologna", "Modena", "Parma", "Reggio Emilia", "Ferrara",
            "Ravenna", "Rimini", "Forlì", "Cesena", "Piacenza"
        ]
        
        for comune in comuni_er:
            if comune.lower() in text:
                return comune
        
        return None

    def _extract_pain_scale(self, user_input: str) -> Optional[int]:
        """Estrae scala dolore VAS 1-10 dall'input."""
        text = user_input.lower()
        
        # Pattern numerici
        patterns = [
            r'\b([0-9]|10)\s*/?\s*(?:su\s*)?10\b',
            r'\b([0-9]|10)\s+(?:su\s+)?10\b',
            r'dolore\s+([0-9]|10)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                pain = int(match.group(1))
                if 0 <= pain <= 10:
                    return pain
        
        # Pattern testuali
        if any(w in text for w in ["nessun dolore", "zero", "niente", "nulla"]):
            return 0
        elif any(w in text for w in ["lieve", "poco", "1", "uno"]):
            return 1
        elif any(w in text for w in ["moderato", "medio", "5", "cinque"]):
            return 5
        elif any(w in text for w in ["forte", "molto", "8", "otto"]):
            return 8
        elif any(w in text for w in ["insopportabile", "massimo", "10", "dieci"]):
            return 10
        
        return None
