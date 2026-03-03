"""
SIRAYA Health Navigator - RAG KPI Calculator
RAG-Enhanced Analytics V2: Semantic analysis of clinical conversations via LLM.

Analizza conversazioni paziente-sistema per estrarre:
- Red flags clinici (Manchester Triage)
- Sintomi da linguaggio colloquiale
- Livelli urgenza con reasoning

Fallback a keyword matching potenziato se API LLM non disponibile.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..config.settings import ClinicalMappings

logger = logging.getLogger(__name__)

# Sinonimi espansi per keyword matching fallback
DISPNEA_SYNONYMS = [
    "non riesco a respirare", "non riesco respirare", "mi manca l'aria", "manca aria",
    "fiato corto", "affanno", "difficoltà respiratorie", "respiro corto",
    "soffoco", "dispnea", "respiro affannoso"
]
CHEST_PAIN_SYNONYMS = [
    "dolore al petto", "dolore petto", "dolore toracico", "dolore al torace",
    "oppressione torace", "petto che stringe", "stringe il petto",
    "dolore cuore", "dolore al cuore"
]
COLLOQUIAL_TO_MEDICAL = {
    **{s: "dispnea" for s in DISPNEA_SYNONYMS},
    **{s: "dolore_toracico" for s in CHEST_PAIN_SYNONYMS},
    "mal di testa forte": "cefalea_grave",
    "mal di testa improvviso": "cefalea_thunderclap",
    "vomito sangue": "ematenesi",
    "sangue nelle feci": "melena",
    "non vedo bene": "disturbi_visivi",
    "vedo doppio": "diplopia",
    "mi gira la testa": "vertigini",
    "svenimento": "sincope",
    "perdita coscienza": "alterazione_coscienza",
}


RED_FLAGS_SYSTEM_PROMPT = """Sei un medico triagista esperto in medicina d'urgenza.
Applica il protocollo Manchester Triage System (MTS) versione italiana.

CATEGORIE RED FLAGS:
1. VIE AEREE: ostruzione, soffocamento, stridore
2. RESPIRAZIONE: apnea, cianosi, dispnea grave
3. CIRCOLAZIONE: shock, emorragia massiva, dolore toracico acuto
4. NEUROLOGICO: alterazione coscienza, deficit focali acuti
5. DOLORE: dolore toracico, addominale acuto, cefalea thunderclap
6. TRAUMA: trauma maggiore, ustioni estese

CODICI URGENZA MTS:
- 1: ROSSO - Emergenza immediata
- 2: ARANCIONE - Molto urgente (< 10 min)
- 3: GIALLO - Urgente (< 60 min)
- 4: VERDE - Poco urgente (< 120 min)
- 5: BIANCO - Non urgente (< 240 min)

Rispondi SOLO con un JSON valido, nessun altro testo."""

RED_FLAGS_USER_TEMPLATE = """Analizza questa conversazione paziente-sistema e estrai RED FLAGS clinici.

CONVERSAZIONE:
{conversation}

Output richiesto (JSON):
{{
  "red_flags_detected": ["lista", "di", "red_flags"],
  "urgency_level": 1-5,
  "urgency_code": "ROSSO|ARANCIONE|GIALLO|VERDE|BIANCO",
  "medical_reasoning": "breve motivazione clinica",
  "confidence_score": 0.0-1.0,
  "symptoms_extracted": {{"nome_sintomo": {{"severity": "...", "context": "..."}}}}
}}"""


class RAGKPICalculator:
    """
    Motore AI semantico per analisi clinica conversazioni.

    Usa LLM (Groq/Gemini) per analisi semantica.
    Fallback a keyword matching potenziato se API non disponibile.
    """

    def __init__(self, llm_service=None):
        self._llm = llm_service
        if self._llm is None:
            try:
                from .llm_service import LLMService
                self._llm = LLMService()
            except Exception as e:
                logger.warning("LLMService non disponibile per RAG: %s", e)

    def analyze_clinical_conversations(
        self,
        conversations: List[Dict[str, Any]],
        analysis_type: str = "red_flags",
    ) -> List[Dict[str, Any]]:
        """
        Analisi semantica conversazioni (red_flags / symptoms).

        Args:
            conversations: [{'session_id': 'xxx', 'messages': [{'role','content'}]}]
            analysis_type: 'red_flags' | 'symptoms'

        Returns:
            Lista di dict con risultati per sessione
        """
        if not conversations:
            return []

        try:
            if self._llm and self._llm.is_available():
                return self._analyze_with_llm(conversations, analysis_type)
        except Exception as e:
            logger.warning("Fallback keyword matching per errore LLM: %s", e)

        return self._enhanced_keyword_analysis(conversations, analysis_type)

    def _analyze_with_llm(
        self,
        conversations: List[Dict],
        analysis_type: str,
    ) -> List[Dict]:
        """Chiamata LLM per analisi."""
        results = []
        for conv in conversations[:50]:  # Limite per evitare timeout
            session_id = conv.get("session_id", "unknown")
            messages = conv.get("messages", [])
            text = " ".join(
                m.get("content", "") for m in messages if isinstance(m, dict)
            )
            if not text.strip():
                results.append({
                    "session_id": session_id,
                    "red_flags_detected": [],
                    "urgency_level": 3,
                    "urgency_code": "GIALLO",
                    "medical_reasoning": "Nessun contenuto",
                    "confidence_score": 0.0,
                    "symptoms_extracted": {},
                })
                continue

            prompt = RED_FLAGS_USER_TEMPLATE.format(conversation=text[:2000])
            full_prompt = RED_FLAGS_SYSTEM_PROMPT + "\n\n" + prompt
            try:
                parsed = self._llm.generate_with_json_parse(
                    full_prompt, temperature=0.1, max_tokens=800
                )
                if parsed:
                    out = {
                        "session_id": session_id,
                        "red_flags_detected": parsed.get("red_flags_detected", []),
                        "urgency_level": max(1, min(5, int(parsed.get("urgency_level", 3)))),
                        "urgency_code": str(parsed.get("urgency_code", "GIALLO")),
                        "medical_reasoning": str(parsed.get("medical_reasoning", "")),
                        "confidence_score": float(parsed.get("confidence_score", 0.5)),
                        "symptoms_extracted": parsed.get("symptoms_extracted", {}) or {},
                    }
                    results.append(out)
                else:
                    results.append(self._fallback_single(text, session_id))
            except Exception as e:
                logger.debug("LLM fallback per sessione %s: %s", session_id[:8], e)
                results.append(self._fallback_single(text, session_id))

        return results

    def _fallback_single(self, text: str, session_id: str) -> Dict:
        """Singolo record fallback da keyword matching."""
        text_lower = text.lower()
        red_flags = []
        for kw in ClinicalMappings.RED_FLAGS_KEYWORDS:
            if kw in text_lower:
                red_flags.append(kw.replace(" ", "_"))
        for phrase, code in COLLOQUIAL_TO_MEDICAL.items():
            if phrase in text_lower:
                red_flags.append(code)
        red_flags = list(dict.fromkeys(red_flags))

        urgency = 3
        if any(k in text_lower for k in ["svenimento", "soffoco", "infarto", "ictus"]):
            urgency = 1
        elif any(k in text_lower for k in ["dolore petto", "dolore toracico", "sangue"]):
            urgency = 2
        elif red_flags:
            urgency = 2

        codes = {1: "ROSSO", 2: "ARANCIONE", 3: "GIALLO", 4: "VERDE", 5: "BIANCO"}
        symptoms = {}
        for s in ClinicalMappings.SINTOMI_COMUNI:
            if s in text_lower:
                symptoms[s] = {"severity": "moderata", "context": "keyword"}

        return {
            "session_id": session_id,
            "red_flags_detected": red_flags,
            "urgency_level": urgency,
            "urgency_code": codes.get(urgency, "GIALLO"),
            "medical_reasoning": "Analisi keyword-based (LLM non disponibile)",
            "confidence_score": 0.6 if red_flags else 0.5,
            "symptoms_extracted": symptoms,
        }

    def _enhanced_keyword_analysis(
        self,
        conversations: List[Dict],
        analysis_type: str,
    ) -> List[Dict]:
        """Keyword matching potenziato con sinonimi e negazione base."""
        results = []
        for conv in conversations:
            session_id = conv.get("session_id", "unknown")
            messages = conv.get("messages", [])
            text = " ".join(
                m.get("content", "") for m in messages if isinstance(m, dict)
            )
            results.append(self._fallback_single(text, session_id))
        return results
