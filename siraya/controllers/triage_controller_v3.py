"""
SIRAYA Triage Controller V4.0
Paradigm shift: LLM-as-a-Judge + Structured Outputs + Metadata Pre-Filtering.

Architecture:
- LLMJudge: Semantic evaluation of user input via structured JSON
- UnifiedSlotFiller: Hybrid (LLM for symptoms, Regex for structured data)
- TriageFSM: Tabular state machine (dict lookup, unchanged)
- QuestionGenerator: RAG-driven clinical questions
- OutcomeGenerator: Structured SBAR via LLM JSON Schema
"""

import re
import time
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

from ..core.state_manager import StateKeys

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS (unchanged)
# ============================================================================

class TriageBranch(Enum):
    EMERGENCY = "A"
    MENTAL_HEALTH = "B"
    STANDARD = "C"
    INFO = "INFO"


class TriagePhase(Enum):
    INTAKE = "intake"
    CHIEF_COMPLAINT = "chief_complaint"
    LOCALIZATION = "localization"
    CONSENT = "consent"
    FAST_TRIAGE = "fast_triage"
    PAIN_SCALE = "pain_scale"
    DEMOGRAPHICS = "demographics"
    CLINICAL_TRIAGE = "clinical_triage"
    RISK_ASSESSMENT = "risk_assessment"
    OUTCOME = "outcome"
    SBAR_GENERATION = "sbar"


# ============================================================================
# LLM-AS-A-JUDGE — DIRETTIVA 1
# Replaces ALL regex-based symptom classification
# ============================================================================

class LLMJudge:
    """
    Uses LLM with structured JSON output to evaluate user input semantically.
    Called ONCE per user turn during intake/chief_complaint phases.
    """

    # ═══ FIX 3a: Typo normalization map (before LLM call) ═══
    TYPO_CORRECTIONS = {
        # Common Italian symptom typos → normalized form
        "malwe": "male", "malee": "male", "mle": "male", "amle": "male",
        "mael": "male", "malle": "male", "mla": "male",
        "dolroe": "dolore", "doloer": "dolore", "dlore": "dolore",
        "dolroe": "dolore", "doore": "dolore",
        "febr": "febbre", "febre": "febbre", "fbbr": "febbre",
        "tsta": "testa", "tesa": "testa", "tetsa": "testa",
        "pcnai": "pancia", "panaci": "pancia", "pacnia": "pancia",
        "sceina": "schiena", "schenia": "schiena", "scheina": "schiena",
        "repsiro": "respiro", "rspiro": "respiro",
        "stmaco": "stomaco", "stoamco": "stomaco",
    }

    @staticmethod
    def _normalize_typos(text: str) -> str:
        """Pre-process user input to fix common typos before LLM evaluation."""
        words = text.split()
        corrected = []
        for word in words:
            clean = word.lower().strip(".,!?;:")
            replacement = LLMJudge.TYPO_CORRECTIONS.get(clean)
            if replacement:
                # Preserve original casing pattern approximately
                corrected.append(replacement)
                logger.info(f"🔧 Typo fix: '{word}' → '{replacement}'")
            else:
                corrected.append(word)
        return " ".join(corrected)

    # ═══ FIX 3b: Enhanced prompt with typo handling instructions ═══
    INTAKE_EVAL_PROMPT = """Sei un medico triagista esperto. Valuta l'input del paziente e rispondi SOLO con JSON.

INPUT PAZIENTE: "{user_input}"

RISPONDI con questo JSON ESATTO (tutti i campi obbligatori):
{{
  "is_specific_symptom": false,
  "is_generic_symptom": false,
  "is_location_info": false,
  "is_age_info": false,
  "is_info_request": false,
  "is_emergency": false,
  "extracted_symptom": null,
  "body_part": null,
  "extracted_location": null,
  "extracted_age": null,
  "clarification_question": "Dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena...",
  "urgency_hint": "standard"
}}

REGOLE DI CLASSIFICAZIONE:
1. GENERICO (is_generic_symptom=true): "ho male", "sto male", "non sto bene", "mi sento male", "sento dolore", "ho un dolore", "mi fa male", "non mi sento bene", "mi fa malissimo", "sento una fitta", qualsiasi lamentela SENZA localizzazione anatomica specifica.
2. SPECIFICO (is_specific_symptom=true): "mal di testa", "dolore alla pancia", "mi fa male la schiena", "ho la febbre", "mi brucia lo stomaco", qualsiasi sintomo CON parte del corpo o patologia identificabile. In questo caso compila extracted_symptom con il TERMINE MEDICO (es: "Cefalea", "Lombalgia", "Dolore addominale") e body_part.
3. EMERGENZA (is_emergency=true): "dolore al petto", "non riesco a respirare", "emorragia", "svenimento", "convulsioni", "paralisi". urgency_hint="emergency".
4. SALUTE MENTALE: "voglio morire", "mi voglio fare del male", "depressione grave", "attacco di panico". urgency_hint="mental_health".
5. INFO: "orari", "dove si trova", "come prenoto", "numero telefono". urgency_hint="info", is_info_request=true.
6. Se generico, clarification_question DEVE essere una domanda specifica come "Dove provi dolore o fastidio?" o "Puoi descrivermi meglio il sintomo?"

⚠️ ATTENZIONE ERRORI DI BATTITURA: L'utente potrebbe scrivere con errori di digitazione (es: "ho maLWE" = "ho male", "ho mle" = "ho male", "doloer" = "dolore"). Interpreta SEMPRE l'intenzione dietro il testo, NON la lettera esatta. Se il messaggio sembra esprimere dolore/malessere generico ma contiene typo, classificalo come GENERICO.
⚠️ "ho male" e TUTTE le sue varianti con typo (malwe, malee, mle, mael, ecc.) sono SEMPRE GENERICI perché NON specificano una parte del corpo.

RISPONDI SOLO CON IL JSON, NESSUN TESTO PRIMA O DOPO."""

    @staticmethod
    def evaluate_input(llm_service, user_input: str) -> Dict[str, Any]:
        """
        Evaluate user input using LLM structured output.
        Returns classification dict. Falls back to heuristics if LLM fails.
        """
        # FIX 3a: Normalize typos before sending to LLM
        normalized_input = LLMJudge._normalize_typos(user_input)
        if normalized_input.lower() != user_input.lower():
            logger.info(f"🔧 Input normalizzato: '{user_input}' → '{normalized_input}'")

        prompt = LLMJudge.INTAKE_EVAL_PROMPT.format(user_input=normalized_input)

        try:
            result = llm_service.generate_with_json_parse(prompt, temperature=0.0, max_tokens=350)

            if result and isinstance(result, dict):
                required = ["is_specific_symptom", "is_generic_symptom"]
                if all(k in result for k in required):
                    logger.info(
                        f"🧠 LLM Judge: specific={result.get('is_specific_symptom')}, "
                        f"generic={result.get('is_generic_symptom')}, "
                        f"symptom={result.get('extracted_symptom')}, "
                        f"urgency={result.get('urgency_hint')}"
                    )
                    return result

            logger.warning("⚠️ LLM Judge: risposta incompleta, uso fallback")
        except Exception as e:
            logger.error(f"❌ LLM Judge failed: {e}")

        return LLMJudge._heuristic_fallback(normalized_input)

    @staticmethod
    def _heuristic_fallback(user_input: str) -> Dict[str, Any]:
        """Minimal conservative fallback when LLM is unavailable."""
        text = user_input.lower().strip()
        words = text.split()

        # Emergency keywords check
        emergency_kw = ["petto", "respiro", "svengo", "sangue", "convulsion", "paralisi"]
        is_emg = any(k in text for k in emergency_kw)

        # FIX 3c: Generic symptom detection (keyword list)
        generic_indicators = [
            "ho male", "sto male", "non sto bene", "mi sento male",
            "sento dolore", "ho un dolore", "mi fa male", "fa malissimo",
            "sento una fitta", "non mi sento", "male", "malessere"
        ]
        is_generic_pain = any(gi in text for gi in generic_indicators)

        # Body parts for SPECIFIC detection
        body_parts = [
            "testa", "pancia", "stomaco", "schiena", "gola", "petto",
            "gamba", "braccio", "ginocchio", "spalla", "piede", "mano",
            "occhio", "orecchio", "collo", "addome", "torace", "fianco"
        ]
        has_body = any(bp in text for bp in body_parts)

        # FIX 3c: If generic pain AND no body part → always GENERIC
        if is_generic_pain and not has_body:
            return {
                "is_specific_symptom": False,
                "is_generic_symptom": True,
                "is_location_info": False,
                "is_age_info": False,
                "is_info_request": False,
                "is_emergency": False,
                "extracted_symptom": None,
                "body_part": None,
                "extracted_location": None,
                "extracted_age": None,
                "clarification_question": "Puoi dirmi dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena...",
                "urgency_hint": "standard"
            }

        # Build extracted_symptom from body part if detected
        detected_bp = None
        extracted_symptom = None
        if has_body:
            for bp in body_parts:
                if bp in text:
                    detected_bp = bp
                    extracted_symptom = UnifiedSlotFiller.BODY_PARTS_MAP.get(
                        bp, f"Dolore al {bp}"
                    )
                    break

        return {
            "is_specific_symptom": has_body and not is_emg,
            "is_generic_symptom": not has_body and not is_emg and not is_generic_pain,
            "is_location_info": False,
            "is_age_info": any(w.isdigit() and len(w) <= 3 for w in words),
            "is_info_request": any(k in text for k in ["orari", "dove", "prenot", "telefono"]),
            "is_emergency": is_emg,
            "extracted_symptom": extracted_symptom,
            "body_part": detected_bp,
            "extracted_location": None,
            "extracted_age": None,
            "clarification_question": "Puoi dirmi dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena...",
            "urgency_hint": "emergency" if is_emg else "standard"
        }


# ============================================================================
# UNIFIED SLOT FILLER — Hybrid: LLM for semantics, Regex for structured data
# ============================================================================

class UnifiedSlotFiller:
    """
    Hybrid slot filler:
    - LLM Judge for symptom classification (Directive 1)
    - Regex for structured data: age (number), pain (scale), location (known list)
    """

    KEYS = {
        "symptom": "chief_complaint",
        "details": "symptom_details",
        "location": "location",
        "pain": "pain_scale",
        "age": "age",
        "gender": "gender",
        "onset": "onset"
    }

    # Known body parts for direct matching (clarification responses)
    BODY_PARTS_MAP = {
        "testa": "Cefalea", "pancia": "Dolore addominale", "stomaco": "Dolore gastrico",
        "schiena": "Lombalgia", "gola": "Faringodinia", "petto": "Dolore toracico",
        "gamba": "Dolore alla gamba", "braccio": "Dolore al braccio",
        "ginocchio": "Gonalgia", "spalla": "Dolore alla spalla",
        "piede": "Dolore al piede", "mano": "Dolore alla mano",
        "occhio": "Dolore oculare", "orecchio": "Otalgia",
        "collo": "Cervicalgia", "addome": "Dolore addominale",
        "torace": "Dolore toracico", "fianco": "Dolore al fianco",
        "anca": "Coxalgia", "dente": "Odontalgia", "denti": "Odontalgia",
        "naso": "Rinorrea", "caviglia": "Dolore alla caviglia",
        "polso": "Dolore al polso", "seno": "Dolore al seno",
        "costole": "Dolore costale", "gluteo": "Dolore al gluteo",
    }

    @classmethod
    def extract(cls, user_input: str, current_data: Dict, llm_service=None, current_phase: str = "") -> Dict[str, Any]:
        """
        Extract data from user input.
        Uses LLM Judge for symptom evaluation, regex for structured fields.
        """
        extracted = {}
        user_lower = user_input.lower().strip()

        # ═══════════════════════════════════════════════════════════════════════
        # PRIORITY 1: If we asked "where does it hurt?" and user gave a body part,
        # combine it with the previous generic symptom to create chief_complaint.
        # This MUST run BEFORE the LLM Judge to avoid re-classifying the body part.
        # ═══════════════════════════════════════════════════════════════════════
        if (current_phase == "chief_complaint"
                and current_data.get("_generic_symptom")
                and "chief_complaint" not in current_data):

            # Check if user input contains a known body part
            matched_bp = None
            matched_term = None
            for bp, medical_term in cls.BODY_PARTS_MAP.items():
                if bp in user_lower:
                    matched_bp = bp
                    matched_term = medical_term
                    break

            if matched_bp:
                extracted[cls.KEYS["symptom"]] = matched_term
                extracted["_body_part"] = matched_bp
                # Clear generic flags — symptom is now specific
                extracted["_generic_symptom"] = False
                logger.info(f"✅ Body part '{matched_bp}' + generico → chief_complaint='{matched_term}'")
                # Skip LLM Judge — we already have what we need
            else:
                # Body part not in our list — use LLM with CONTEXT
                if llm_service:
                    raw_symptom = current_data.get("_raw_symptom", "dolore generico")
                    context_prompt = LLMJudge.INTAKE_EVAL_PROMPT.format(
                        user_input=f"Il paziente aveva detto '{raw_symptom}'. Ora specifica: '{user_input}'. Combinali in un sintomo SPECIFICO."
                    )
                    try:
                        result = llm_service.generate_with_json_parse(context_prompt, temperature=0.0, max_tokens=350)
                        if result and result.get("extracted_symptom"):
                            extracted[cls.KEYS["symptom"]] = result["extracted_symptom"]
                            extracted["_generic_symptom"] = False
                            if result.get("body_part"):
                                extracted["_body_part"] = result["body_part"]
                            logger.info(f"✅ LLM context combine: '{raw_symptom}' + '{user_input}' → '{result['extracted_symptom']}'")
                        elif result and result.get("is_specific_symptom"):
                            # LLM said specific but no extracted_symptom — build one
                            extracted[cls.KEYS["symptom"]] = f"Dolore: {user_input.strip()}"
                            extracted["_generic_symptom"] = False
                            logger.info(f"✅ LLM specific (no term): → 'Dolore: {user_input.strip()}'")
                        else:
                            # LLM couldn't classify — use raw input as symptom
                            extracted[cls.KEYS["symptom"]] = f"Dolore ({user_input.strip()})"
                            extracted["_generic_symptom"] = False
                            logger.info(f"⚠️ Fallback: generic + '{user_input}' → 'Dolore ({user_input.strip()})'")
                    except Exception as e:
                        logger.error(f"❌ LLM context combine error: {e}")
                        extracted[cls.KEYS["symptom"]] = f"Dolore ({user_input.strip()})"
                        extracted["_generic_symptom"] = False
                else:
                    # No LLM — just combine raw
                    extracted[cls.KEYS["symptom"]] = f"Dolore ({user_input.strip()})"
                    extracted["_generic_symptom"] = False

            # Return early — don't run LLM Judge again for body part responses
            # But still extract other structured fields below (pain, age, location, etc.)

        # ═══ SYMPTOM via LLM Judge (only if chief_complaint still missing) ═══
        elif "chief_complaint" not in current_data and "chief_complaint" not in extracted and llm_service and current_phase in ("intake", "chief_complaint", ""):
            judgment = LLMJudge.evaluate_input(llm_service, user_input)

            # Store full judgment for downstream use
            extracted["_llm_judgment"] = judgment

            if judgment.get("is_emergency"):
                extracted["_urgency_override"] = "emergency"

            if judgment.get("is_info_request"):
                extracted["_urgency_override"] = "info"

            if judgment.get("urgency_hint") == "mental_health":
                extracted["_urgency_override"] = "mental_health"

            if judgment.get("is_specific_symptom"):
                if judgment.get("extracted_symptom"):
                    extracted[cls.KEYS["symptom"]] = judgment["extracted_symptom"]
                elif judgment.get("body_part"):
                    # LLM said specific but no medical term — build from body part
                    bp = judgment["body_part"]
                    extracted[cls.KEYS["symptom"]] = cls.BODY_PARTS_MAP.get(
                        bp.lower(), f"Dolore al {bp}"
                    )
                if judgment.get("body_part"):
                    extracted["_body_part"] = judgment["body_part"]
                logger.info(f"✅ Sintomo SPECIFICO (LLM): '{extracted.get(cls.KEYS['symptom'], 'N/A')}'")

            elif judgment.get("is_generic_symptom"):
                extracted["_generic_symptom"] = True
                extracted["_raw_symptom"] = user_input.strip()
                extracted["_clarification_question"] = judgment.get(
                    "clarification_question",
                    "Dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena..."
                )
                logger.info("⚠️ Sintomo GENERICO (LLM): richiesto approfondimento")

            # LLM may also extract location/age in the same pass
            if judgment.get("extracted_location") and "location" not in current_data:
                extracted[cls.KEYS["location"]] = str(judgment["extracted_location"]).title()
            if judgment.get("extracted_age") and "age" not in current_data:
                age_val = judgment["extracted_age"]
                if isinstance(age_val, (int, float)) and 0 < age_val < 120:
                    extracted[cls.KEYS["age"]] = int(age_val)

        # ═══ SYMPTOM DETAILS (cumulative, keyword-based — fine as regex) ═══
        detail_keywords = {
            "costante": "dolore costante", "intermittente": "intermittente",
            "pulsante": "pulsante", "localizzato": "localizzato",
            "diffuso": "diffuso", "acuto": "dolore acuto",
            "sordo": "dolore sordo", "bruciante": "bruciante", "lancinante": "lancinante",
        }
        for kw, desc in detail_keywords.items():
            if kw in user_lower:
                existing = current_data.get(cls.KEYS["details"], [])
                if desc not in existing:
                    extracted[cls.KEYS["details"]] = existing + [desc]
                break

        # ═══ PAIN SCALE (regex — it's a number/selection) ═══
        if current_phase == "pain_scale" or ("dolore" in user_lower and cls.KEYS["pain"] not in current_data):
            pain_patterns = [
                r'(\d{1,2})\s*-\s*(\d{1,2}):\s*',
                r'(\d{1,2})\s*/\s*10',
                r'(\d{1,2})\s+su\s+10',
            ]
            for pattern in pain_patterns:
                match = re.search(pattern, user_lower)
                if match:
                    try:
                        scale = int(match.group(1))
                        if 1 <= scale <= 10:
                            extracted[cls.KEYS["pain"]] = scale
                            break
                    except (IndexError, ValueError):
                        pass

            if cls.KEYS["pain"] not in extracted:
                pain_text_map = {
                    "lieve": 2, "leggero": 2,
                    "moderato": 5, "medio": 5,
                    "forte": 7, "intenso": 8,
                    "insopportabile": 9, "fortissimo": 9,
                }
                for kw, val in pain_text_map.items():
                    if kw in user_lower:
                        extracted[cls.KEYS["pain"]] = val
                        break

        # ═══ AGE (regex — it's a number, only in demographics phase) ═══
        if "age" not in current_data and current_phase == "demographics":
            age_patterns = [
                r'^(\d{1,3})$',
                r'\b(\d{1,3})\s+ann[io]',
                r'ho\s+(\d{1,3})\s+ann',
                r'ne\s+ho\s+(\d{1,3})',
            ]
            for pattern in age_patterns:
                match = re.search(pattern, user_lower)
                if match:
                    try:
                        age = int(match.group(1))
                        if 0 < age < 120:
                            extracted[cls.KEYS["age"]] = age
                            break
                    except (IndexError, ValueError):
                        pass

        # ═══ LOCATION (regex — known list of comuni) ═══
        if "location" not in current_data and "location" not in extracted:
            comuni_er = [
                "bologna", "modena", "parma", "reggio emilia", "piacenza",
                "ferrara", "ravenna", "forlì", "forli", "cesena", "rimini",
                "imola", "faenza", "lugo", "cervia", "riccione", "cattolica",
                "misano", "santarcangelo", "bellaria", "carpi", "casalecchio",
                "fidenza", "salsomaggiore", "sassuolo", "vignola", "castelfranco",
                "cento", "comacchio", "argenta", "budrio", "san giovanni in persiceto"
            ]
            for comune in comuni_er:
                if comune in user_lower:
                    extracted[cls.KEYS["location"]] = comune.title()
                    break

        # ═══ ONSET (also handles multiple-choice answers like "A) Meno di 1 settimana") ═══
        if "onset" not in current_data:
            # Strip "A) ", "B) ", "C) " prefix from multiple choice answers
            clean_input = re.sub(r'^[A-C]\)\s*', '', user_lower).strip()
            
            if "improvvis" in clean_input or "fulmine" in clean_input or "colpo" in clean_input:
                extracted[cls.KEYS["onset"]] = "improvviso"
            elif "gradual" in clean_input or "piano" in clean_input:
                extracted[cls.KEYS["onset"]] = "graduale"
            elif "meno di 30 minuti" in clean_input or "mezz'ora" in clean_input:
                extracted[cls.KEYS["onset"]] = "meno di 30 minuti"
            elif "meno di 1 ora" in clean_input or "meno di un'ora" in clean_input or "meno di 2 ore" in clean_input:
                extracted[cls.KEYS["onset"]] = "meno di 1 ora"
            elif "poche ore" in clean_input or "alcune ore" in clean_input:
                extracted[cls.KEYS["onset"]] = "da poche ore"
            elif "ieri" in clean_input:
                extracted[cls.KEYS["onset"]] = "ieri"
            elif "stamattina" in clean_input or "questa mattina" in clean_input or "oggi" in clean_input:
                extracted[cls.KEYS["onset"]] = "oggi"
            elif "meno di 1 settimana" in clean_input or "meno di una settimana" in clean_input:
                extracted[cls.KEYS["onset"]] = "meno di 1 settimana"
            elif "più di un giorno" in clean_input or "più di 1 giorno" in clean_input:
                extracted[cls.KEYS["onset"]] = "più di un giorno"
            elif "settimana" in clean_input or "giorni" in clean_input or "qualche giorno" in clean_input:
                extracted[cls.KEYS["onset"]] = "da una settimana"
            elif "mese" in clean_input or "mesi" in clean_input:
                extracted[cls.KEYS["onset"]] = "da un mese"
            elif "anno" in clean_input or "anni" in clean_input:
                extracted[cls.KEYS["onset"]] = "da oltre un anno"

        # ═══ CONSENT (binary) ═══
        if current_phase == "consent":
            positive = ["sì", "si", "ok", "va bene", "accetto", "certo", "d'accordo", "procedi"]
            negative = ["no", "non voglio", "rifiuto", "preferisco di no"]
            if any(w in user_lower for w in positive):
                extracted["consent"] = "yes"
            elif any(w in user_lower for w in negative):
                extracted["consent"] = "no"

        return extracted


# ============================================================================
# FSM — Tabular State Machine (unchanged logic, minor fixes)
# ============================================================================

class TriageFSM:
    """Finite State Machine with transition table."""

    def __init__(self, state_manager):
        self.state = state_manager
        self.transitions = {
            (TriageBranch.STANDARD, TriagePhase.INTAKE): self._std_from_intake,
            (TriageBranch.STANDARD, TriagePhase.CHIEF_COMPLAINT): self._std_from_complaint,
            (TriageBranch.STANDARD, TriagePhase.LOCALIZATION): self._std_from_location,
            (TriageBranch.STANDARD, TriagePhase.PAIN_SCALE): self._std_from_pain,
            (TriageBranch.STANDARD, TriagePhase.DEMOGRAPHICS): self._std_from_demographics,
            (TriageBranch.STANDARD, TriagePhase.CLINICAL_TRIAGE): self._std_from_clinical,
            (TriageBranch.STANDARD, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,

            (TriageBranch.EMERGENCY, TriagePhase.INTAKE): self._emg_from_intake,
            (TriageBranch.EMERGENCY, TriagePhase.CHIEF_COMPLAINT): self._emg_from_complaint,
            (TriageBranch.EMERGENCY, TriagePhase.PAIN_SCALE): self._emg_from_pain,
            (TriageBranch.EMERGENCY, TriagePhase.DEMOGRAPHICS): self._emg_from_demographics,
            (TriageBranch.EMERGENCY, TriagePhase.LOCALIZATION): self._emg_from_location,
            (TriageBranch.EMERGENCY, TriagePhase.FAST_TRIAGE): self._emg_from_fast,
            (TriageBranch.EMERGENCY, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,

            (TriageBranch.MENTAL_HEALTH, TriagePhase.INTAKE): lambda d, q: TriagePhase.CONSENT,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.CONSENT): self._mh_from_consent,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.DEMOGRAPHICS): self._mh_from_demographics,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.RISK_ASSESSMENT): self._mh_from_risk,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,

            (TriageBranch.INFO, TriagePhase.INTAKE): self._info_from_intake,
            (TriageBranch.INFO, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,
        }

    def next_phase(self, branch, current, data, phase_q_count):
        key = (branch, current)
        func = self.transitions.get(key)
        if func:
            return func(data, phase_q_count)
        logger.warning(f"⚠️ No transition for {key}, staying in {current.value}")
        return current

    # === STANDARD ===
    def _std_from_intake(self, data, q):
        if data.get("_generic_symptom") and "chief_complaint" not in data:
            return TriagePhase.CHIEF_COMPLAINT
        if "chief_complaint" in data:
            if "location" in data:
                return TriagePhase.PAIN_SCALE
            return TriagePhase.LOCALIZATION
        return TriagePhase.CHIEF_COMPLAINT

    def _std_from_complaint(self, data, q):
        if "chief_complaint" in data:
            if "location" in data:
                return TriagePhase.PAIN_SCALE
            return TriagePhase.LOCALIZATION
        # If generic symptom but no chief_complaint yet, stay to collect body part
        if data.get("_generic_symptom"):
            return TriagePhase.CHIEF_COMPLAINT
        return TriagePhase.CHIEF_COMPLAINT

    def _std_from_location(self, data, q):
        return TriagePhase.PAIN_SCALE if "location" in data else TriagePhase.LOCALIZATION

    def _std_from_pain(self, data, q):
        return TriagePhase.DEMOGRAPHICS if "pain_scale" in data else TriagePhase.PAIN_SCALE

    def _std_from_demographics(self, data, q):
        if "age" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.CLINICAL_TRIAGE
        return TriagePhase.DEMOGRAPHICS

    def _std_from_clinical(self, data, phase_q_count):
        required = ["chief_complaint", "location", "pain_scale", "age"]
        has_all = all(k in data for k in required)
        if phase_q_count >= 5 and has_all:
            return TriagePhase.OUTCOME
        if phase_q_count >= 7:
            return TriagePhase.OUTCOME
        return TriagePhase.CLINICAL_TRIAGE

    # === EMERGENCY ===
    def _emg_from_intake(self, data, q):
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION

    def _emg_from_complaint(self, data, q):
        """Handle escalation C→A when in CHIEF_COMPLAINT."""
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION

    def _emg_from_pain(self, data, q):
        """Handle escalation C→A when in PAIN_SCALE."""
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION

    def _emg_from_demographics(self, data, q):
        """Handle escalation C→A when in DEMOGRAPHICS."""
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION

    def _emg_from_location(self, data, q):
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION

    def _emg_from_fast(self, data, phase_q_count):
        return TriagePhase.OUTCOME if phase_q_count >= 3 else TriagePhase.FAST_TRIAGE

    # === MENTAL HEALTH ===
    def _mh_from_consent(self, data, q):
        if data.get("consent") == "yes":
            return TriagePhase.DEMOGRAPHICS
        if data.get("consent") == "no":
            return TriagePhase.OUTCOME
        return TriagePhase.CONSENT

    def _mh_from_demographics(self, data, q):
        if "age" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.RISK_ASSESSMENT
        return TriagePhase.DEMOGRAPHICS

    def _mh_from_risk(self, data, phase_q_count):
        return TriagePhase.OUTCOME if phase_q_count >= 4 else TriagePhase.RISK_ASSESSMENT

    # === INFO ===
    def _info_from_intake(self, data, q):
        return TriagePhase.OUTCOME


# ============================================================================
# QUESTION GENERATOR — uses LLM Judge clarification for generic symptoms
# ============================================================================

class QuestionGenerator:
    """Generates questions. Uses LLM Judge's clarification for generic symptoms."""

    def __init__(self, llm_service, rag_service):
        self.llm = llm_service
        self.rag = rag_service

    def generate(self, phase, branch, data, phase_q_count):
        if phase == TriagePhase.CHIEF_COMPLAINT:
            if data.get("_generic_symptom"):
                return {
                    "text": data.get("_clarification_question",
                                     "Capisco che non ti senti bene. Puoi dirmi dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena..."),
                    "type": "open_text",
                    "options": None
                }
            return {"text": "Qual è il motivo del tuo contatto oggi?", "type": "open_text", "options": None}

        if phase == TriagePhase.LOCALIZATION:
            return {"text": "In quale comune dell'Emilia-Romagna ti trovi?", "type": "open_text", "options": None}

        if phase == TriagePhase.PAIN_SCALE:
            return {
                "text": "Su una scala da 1 a 10, quanto è intenso il dolore?",
                "type": "multiple_choice",
                "options": ["1-3: Lieve", "4-6: Moderato", "7-8: Forte", "9-10: Insopportabile"]
            }

        if phase == TriagePhase.DEMOGRAPHICS:
            return {"text": "Quanti anni hai?", "type": "open_text", "options": None}

        if phase == TriagePhase.CONSENT:
            return {
                "text": "Per poterti aiutare al meglio, avrei bisogno di farti alcune domande personali. Sei d'accordo a procedere?",
                "type": "multiple_choice",
                "options": ["Sì, procedi", "Preferisco di no"]
            }

        if phase in [TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT]:
            return self._generate_clinical_question(phase, branch, data, phase_q_count)

        logger.error(f"❌ QuestionGenerator: unexpected phase {phase.value}")
        return {"text": "Puoi fornirmi maggiori dettagli sulla tua situazione?", "type": "open_text", "options": None}

    # Phrases that indicate the LLM generated a closing/summary instead of a question
    BLOCK_PHRASES = [
        "grazie per", "grazie delle", "grazie dell'", "informazioni fornite",
        "dati raccolti", "dati che mi hai",
        "riepilogo", "in sintesi", "riassumendo", "sulla base di",
        "ti consiglio", "ti suggerisco", "il mio consiglio",
        "in base a quanto", "dalle informazioni", "concludendo",
        "posso dirti che", "la mia valutazione", "consiglio di recarti",
        "ti raccomando", "il quadro clinico",
    ]

    # ═══════════════════════════════════════════════════════════════════════════
    # CLINICAL DIMENSIONS — Ordered investigation topics per symptom category.
    # Each question number maps to a SPECIFIC clinical dimension.
    # This PREVENTS the LLM from repeating the same question.
    # ═══════════════════════════════════════════════════════════════════════════
    CLINICAL_DIMENSIONS = {
        "dolore toracico": [
            {"topic": "INSORGENZA E DURATA", "instruction": "Chiedi da QUANTO TEMPO dura il dolore e se è iniziato IMPROVVISAMENTE (come un colpo) o GRADUALMENTE (aumentato piano piano). Opzioni: A) Improvviso, meno di 1 ora fa B) Graduale, da alcune ore C) Da più di un giorno"},
            {"topic": "IRRADIAZIONE DEL DOLORE", "instruction": "Chiedi se il dolore si IRRADIA verso altre parti del corpo: braccio sinistro, mascella, spalle, schiena. L'irradiazione al braccio sinistro/mascella è un red flag cardiaco. Opzioni: A) Sì, verso braccio sinistro o mascella B) Sì, verso schiena o spalle C) No, il dolore resta localizzato al petto"},
            {"topic": "CARATTERE DEL DOLORE", "instruction": "Chiedi la QUALITÀ/TIPO del dolore: costrittivo/oppressivo (come un peso sul petto), bruciante, trafittivo/puntorio (come un ago). Opzioni: A) Oppressivo/costrittivo, come un peso B) Bruciante C) Trafittivo/puntorio, come una fitta"},
            {"topic": "SINTOMI ASSOCIATI", "instruction": "Chiedi se ci sono SINTOMI ASSOCIATI: difficoltà a respirare (dispnea), sudorazione fredda, nausea/vomito, capogiri, palpitazioni. Opzioni: A) Sì, difficoltà a respirare B) Sì, sudorazione e/o nausea C) No, solo dolore toracico"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: farmaci attualmente in uso, patologie cardiache note (ipertensione, colesterolo alto), familiarità per malattie cardiache, fumo/diabete. Opzioni: A) Sì, ho patologie cardiache note o prendo farmaci B) No patologie note, ma ho fattori di rischio (fumo/diabete/familiarità) C) Nessuna patologia o fattore di rischio noto"},
            {"topic": "POSIZIONE E FATTORI", "instruction": "Chiedi se il dolore CAMBIA con la posizione del corpo, la respirazione profonda, o la pressione sul torace. Questo aiuta a distinguere cause cardiache da muscoloscheletriche. Opzioni: A) Peggiora con il respiro profondo o i movimenti B) È costante, non cambia C) Peggiora sotto sforzo, migliora a riposo"},
        ],
        "cefalea": [
            {"topic": "INSORGENZA E DURATA", "instruction": "Chiedi da QUANTO TEMPO dura il mal di testa e se è iniziato IMPROVVISAMENTE (a tuono) o gradualmente. Un'insorgenza improvvisa è un red flag. Opzioni: A) Improvviso, come un fulmine B) Graduale, da alcune ore C) Da più di un giorno"},
            {"topic": "LOCALIZZAZIONE", "instruction": "Chiedi DOVE è localizzato il mal di testa: un solo lato (unilaterale), entrambi i lati, fronte, nuca/occipitale, o diffuso. Opzioni: A) Un solo lato della testa B) Fronte o zona degli occhi C) Nuca/dietro la testa o diffuso"},
            {"topic": "CARATTERE DEL DOLORE", "instruction": "Chiedi il TIPO di dolore: pulsante/martellante, tensivo (come una fascia stretta), lancinante/a fitte. Opzioni: A) Pulsante/martellante B) Tensivo, come una morsa C) Lancinante, a fitte intense"},
            {"topic": "SINTOMI ASSOCIATI", "instruction": "Chiedi SINTOMI ASSOCIATI: nausea/vomito, fastidio alla luce (fotofobia), disturbi visivi (aura), rigidità del collo, vertigini. Opzioni: A) Nausea o vomito B) Fastidio alla luce o disturbi visivi C) Rigidità al collo o vertigini"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: farmaci assunti, precedenti episodi simili, trauma cranico recente, ipertensione nota. Opzioni: A) Sì, soffro spesso di mal di testa B) Ho avuto un trauma recente alla testa C) Nessun precedente significativo"},
        ],
        "dolore addominale": [
            {"topic": "LOCALIZZAZIONE ADDOMINALE", "instruction": "Chiedi DOVE esattamente è localizzato il dolore: quadrante superiore destro/sinistro, inferiore destro/sinistro, epigastrio (bocca dello stomaco), periombelicale, diffuso. Opzioni: A) Parte alta dell'addome (stomaco/costole) B) Parte bassa dell'addome C) Diffuso su tutto l'addome"},
            {"topic": "CARATTERE DEL DOLORE", "instruction": "Chiedi il TIPO di dolore: crampiforme/colico (va e viene a ondate), continuo/costante, trafittivo/acuto. Opzioni: A) Crampiforme, va e viene a ondate B) Costante e continuo C) Acuto e improvviso"},
            {"topic": "SINTOMI ASSOCIATI GI", "instruction": "Chiedi SINTOMI GASTROINTESTINALI associati: nausea, vomito, diarrea, stipsi (stitichezza), bruciore di stomaco. Opzioni: A) Nausea o vomito B) Diarrea o stipsi C) Nessun altro sintomo gastrointestinale"},
            {"topic": "FATTORI SCATENANTI", "instruction": "Chiedi se il dolore è collegato a FATTORI SPECIFICI: pasti (peggiora dopo mangiato?), posizione del corpo, stress. Chiedi anche se c'è febbre. Opzioni: A) Peggiora dopo i pasti B) C'è anche febbre C) Non noto un fattore scatenante preciso"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: farmaci (FANS, antibiotici), interventi chirurgici addominali precedenti, patologie note (reflusso, calcoli, ulcera). Opzioni: A) Sì, ho patologie addominali note o prendo farmaci B) Ho avuto interventi chirurgici all'addome C) Nessuna patologia o intervento noto"},
        ],
        "lombalgia": [
            {"topic": "INSORGENZA E DURATA", "instruction": "Chiedi da QUANTO TEMPO dura il dolore alla schiena e come è iniziato: dopo uno sforzo, gradualmente, improvvisamente. Opzioni: A) Dopo uno sforzo fisico o un movimento brusco B) Gradualmente, senza causa apparente C) Improvvisamente, da meno di un giorno"},
            {"topic": "IRRADIAZIONE", "instruction": "Chiedi se il dolore si IRRADIA: verso la gamba (sciatica), gluteo, inguine. L'irradiazione alle gambe può indicare compressione nervosa. Opzioni: A) Sì, scende verso una gamba B) Sì, verso gluteo o inguine C) No, resta localizzato alla schiena"},
            {"topic": "DEFICIT NEUROLOGICI", "instruction": "Chiedi se ci sono DEFICIT: intorpidimento/formicolio alle gambe, debolezza muscolare, difficoltà a controllare la vescica o l'intestino (red flags). Opzioni: A) Sì, formicolio o intorpidimento B) Sì, debolezza o difficoltà a camminare C) No, nessun deficit"},
            {"topic": "FATTORI POSIZIONALI", "instruction": "Chiedi se il dolore CAMBIA con la posizione: peggiora stando seduti, in piedi, durante il movimento, migliora sdraiati. Opzioni: A) Peggiora stando seduto o in piedi a lungo B) Peggiora con i movimenti C) È costante, non cambia con la posizione"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: episodi precedenti simili, interventi alla schiena, farmaci antidolorifici assunti, osteoporosi nota. Opzioni: A) Sì, ho avuto episodi simili in passato B) Prendo antidolorifici ma non migliorano C) È la prima volta"},
        ],
        "dolore al piede": [
            {"topic": "INSORGENZA E CAUSA", "instruction": "Chiedi da QUANTO TEMPO dura il dolore al piede e se è legato a un evento specifico: trauma, caduta, camminata prolungata, nuovo paio di scarpe. Opzioni: A) Dopo un trauma o una caduta B) Dopo attività fisica o camminata prolungata C) Senza causa apparente, gradualmente"},
            {"topic": "LOCALIZZAZIONE NEL PIEDE", "instruction": "Chiedi DOVE esattamente nel piede è localizzato il dolore: pianta, tallone, dita, dorso del piede, caviglia. Opzioni: A) Pianta del piede o tallone B) Dita o dorso del piede C) Caviglia o zona laterale"},
            {"topic": "CARICO E MOVIMENTO", "instruction": "Chiedi se il dolore PEGGIORA con il carico, la camminata o specifici movimenti, oppure se è presente anche a riposo. Opzioni: A) Peggiora camminando o stando in piedi B) Presente anche a riposo o di notte C) Solo con movimenti specifici"},
            {"topic": "SEGNI VISIBILI", "instruction": "Chiedi se ci sono SEGNI VISIBILI: gonfiore, arrossamento, lividi, deformità, difficoltà a muovere le dita. Opzioni: A) Sì, gonfiore e/o arrossamento B) Sì, livido o deformità visibile C) No, nessun segno visibile"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: diabete (neuropatia), problemi circolatori, gotta, episodi simili, farmaci in uso. Opzioni: A) Sì, ho diabete o problemi circolatori B) Ho avuto episodi simili in passato C) Nessuna patologia nota"},
        ],
        "dolore alla gamba": [
            {"topic": "INSORGENZA E CAUSA", "instruction": "Chiedi da QUANTO TEMPO dura il dolore alla gamba e se è collegato a un evento: sforzo, trauma, posizione prolungata. Opzioni: A) Dopo un trauma o sforzo fisico B) Gradualmente, senza causa apparente C) Improvvisamente, a riposo"},
            {"topic": "LOCALIZZAZIONE NELLA GAMBA", "instruction": "Chiedi DOVE nella gamba è il dolore: coscia, polpaccio, ginocchio, dietro il ginocchio. Il dolore al polpaccio improvviso può indicare trombosi venosa. Opzioni: A) Polpaccio B) Coscia C) Ginocchio o dietro il ginocchio"},
            {"topic": "CARATTERISTICHE E SEGNI", "instruction": "Chiedi le CARATTERISTICHE: crampo, bruciore, intorpidimento, formicolio. Chiedi se c'è gonfiore, rossore o calore al tatto (segni di trombosi). Opzioni: A) Crampo o dolore muscolare B) Formicolio o intorpidimento C) Gonfiore, rossore o calore"},
            {"topic": "IRRADIAZIONE", "instruction": "Chiedi se il dolore INIZIA dalla schiena e scende lungo la gamba (possibile sciatica) o se è localizzato solo nella gamba. Opzioni: A) Parte dalla schiena e scende lungo la gamba B) Solo nella gamba, non parte dalla schiena C) Si estende al piede"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: problemi circolatori (vene varicose, trombosi precedente), diabete, lombalgia cronica, farmaci in uso. Opzioni: A) Sì, ho problemi circolatori o vene varicose B) Ho problemi alla schiena C) Nessuna patologia nota"},
        ],
        "dolore articolare": [
            {"topic": "INSORGENZA E DURATA", "instruction": "Chiedi da QUANTO TEMPO dura il dolore articolare e se è iniziato dopo un trauma, sforzo o spontaneamente. Opzioni: A) Dopo un trauma o movimento brusco B) Gradualmente, senza causa evidente C) Improvvisamente, senza trauma"},
            {"topic": "MOBILITÀ", "instruction": "Chiedi se l'articolazione ha LIMITAZIONI DI MOVIMENTO: rigidità mattutina, impossibilità di piegare/estendere, blocco. Opzioni: A) Sì, rigidità soprattutto al mattino B) Sì, non riesco a muovere normalmente l'articolazione C) No, movimento normale nonostante il dolore"},
            {"topic": "SEGNI INFIAMMATORI", "instruction": "Chiedi se ci sono SEGNI DI INFIAMMAZIONE: gonfiore, rossore, calore al tatto, versamento articolare. Opzioni: A) Sì, gonfiore e rossore B) Sì, calore al tatto C) No, nessun segno visibile"},
            {"topic": "PATTERN", "instruction": "Chiedi se il dolore COINVOLGE più articolazioni o solo una, e se è simmetrico. Questo aiuta a distinguere artrite, artrosi, gotta. Opzioni: A) Solo un'articolazione B) Più articolazioni, simmetrico (es. entrambe le mani) C) Più articolazioni, asimmetrico"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: artrite nota, gotta, patologie autoimmuni, farmaci (cortisone, antinfiammatori), episodi precedenti. Opzioni: A) Sì, ho artrite o gotta nota B) Ho avuto episodi simili in passato C) Nessuna patologia articolare nota"},
        ],
        "febbre": [
            {"topic": "TEMPERATURA E DURATA", "instruction": "Chiedi la TEMPERATURA misurata e da QUANTO TEMPO è presente la febbre. Opzioni: A) Lieve (37-38°C), da oggi B) Alta (>38.5°C), da più di un giorno C) Non ho misurato ma mi sento molto caldo/freddo"},
            {"topic": "SINTOMI RESPIRATORI", "instruction": "Chiedi se ci sono SINTOMI RESPIRATORI associati: tosse, mal di gola, raffreddore, difficoltà a respirare. Opzioni: A) Sì, tosse e/o mal di gola B) Sì, difficoltà a respirare C) No, nessun sintomo respiratorio"},
            {"topic": "ALTRI SINTOMI", "instruction": "Chiedi se ci sono ALTRI SINTOMI: dolori muscolari, mal di testa, nausea/vomito, diarrea, eruzioni cutanee, dolore a urinare. Opzioni: A) Dolori muscolari e/o mal di testa B) Nausea, vomito o diarrea C) Dolore a urinare o altri sintomi"},
            {"topic": "CONTESTO", "instruction": "Chiedi il CONTESTO: viaggi recenti, contatto con persone malate, vaccinazioni recenti, interventi chirurgici recenti. Opzioni: A) Sì, contatto con persone malate B) Viaggio recente o intervento chirurgico C) Nessun contesto particolare"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI: farmaci antipiretici assunti, patologie croniche (immunosoppressione, diabete), allergie. Opzioni: A) Ho preso antipiretici ma la febbre non scende B) Ho patologie croniche C) Nessun farmaco e nessuna patologia nota"},
        ],
        "_default": [
            {"topic": "INSORGENZA E DURATA", "instruction": "Chiedi da QUANTO TEMPO è presente il sintomo e come è iniziato (improvvisamente o gradualmente). Opzioni: A) Improvvisamente, da poche ore B) Gradualmente, da alcuni giorni C) Da più di una settimana"},
            {"topic": "CARATTERISTICHE", "instruction": "Chiedi le CARATTERISTICHE del disturbo: è costante o intermittente? Peggiora o migliora? Opzioni: A) Costante, non migliora B) Va e viene C) Sta peggiorando nel tempo"},
            {"topic": "SINTOMI ASSOCIATI", "instruction": "Chiedi se ci sono ALTRI SINTOMI associati: febbre, nausea, stanchezza, difficoltà respiratorie, altri dolori. Opzioni: A) Sì, ho anche febbre B) Sì, ho altri sintomi (nausea, stanchezza, ecc.) C) No, solo questo disturbo"},
            {"topic": "FATTORI SCATENANTI", "instruction": "Chiedi se c'è un FATTORE SCATENANTE: trauma, sforzo fisico, stress, cibo, farmaco nuovo, contatto con malati. Opzioni: A) Sì, dopo un evento specifico B) Potrebbe essere legato a stress o fatica C) Nessun fattore evidente"},
            {"topic": "FARMACI E PATOLOGIE", "instruction": "Chiedi ANAMNESI FARMACOLOGICA: farmaci in uso, allergie, patologie croniche note, episodi simili in passato. Opzioni: A) Sì, prendo farmaci regolarmente B) Ho avuto episodi simili in passato C) Nessun farmaco e nessuna patologia nota"},
        ],
    }

    # Extra aliases to map medicalized terms to dimension keys
    DIMENSION_ALIASES = {
        "cefalea": "cefalea", "mal di testa": "cefalea",
        "dolore toracico": "dolore toracico", "toracalgia": "dolore toracico",
        "dolore addominale": "dolore addominale", "dolore gastrico": "dolore addominale",
        "lombalgia": "lombalgia", "dolore alla schiena": "lombalgia",
        "dolore al piede": "dolore al piede",
        "dolore alla gamba": "dolore alla gamba",
        "gonalgia": "dolore articolare", "coxalgia": "dolore articolare",
        "dolore alla spalla": "dolore articolare", "dolore al polso": "dolore articolare",
        "dolore alla caviglia": "dolore articolare",
        "dolore al braccio": "dolore articolare", "dolore alla mano": "dolore articolare",
        "cervicalgia": "lombalgia",  # Similar investigation (spine-related)
        "febbre": "febbre",
    }

    def _get_clinical_dimension(self, symptom: str, phase_q_count: int) -> Dict[str, str]:
        """Get the clinical dimension to investigate based on symptom and question number."""
        symptom_lower = symptom.lower()

        # 1. Check aliases first (exact match for medicalized terms)
        dimension_key = self.DIMENSION_ALIASES.get(symptom_lower)
        if dimension_key and dimension_key in self.CLINICAL_DIMENSIONS:
            dimensions = self.CLINICAL_DIMENSIONS[dimension_key]
        else:
            # 2. Partial match against dimension keys
            dimensions = None
            for key in self.CLINICAL_DIMENSIONS:
                if key == "_default":
                    continue
                if key in symptom_lower or any(word in symptom_lower for word in key.split() if len(word) > 3):
                    dimensions = self.CLINICAL_DIMENSIONS[key]
                    break

            if not dimensions:
                dimensions = self.CLINICAL_DIMENSIONS["_default"]

        # Select dimension by index (cap at last dimension if more questions)
        idx = min(phase_q_count, len(dimensions) - 1)
        selected = dimensions[idx]
        logger.info(f"🩺 Dimension map: '{symptom}' → key='{dimension_key or 'partial/default'}', Q{phase_q_count} → {selected['topic']}")
        return selected

    def _generate_clinical_question(self, phase, branch, data, phase_q_count):
        symptom = data.get("chief_complaint", "sintomo generico")
        pain = data.get("pain_scale", "N/D")
        age = data.get("age", "N/D")
        onset = data.get("onset", "N/D")

        # ═══ Get the SPECIFIC clinical dimension for this question ═══
        dimension = self._get_clinical_dimension(symptom, phase_q_count)
        topic = dimension["topic"]
        instruction = dimension["instruction"]
        logger.info(f"🩺 Q{phase_q_count+1}: Dimensione clinica → {topic}")

        # ═══ RAG context (supplements dimensions with protocol details) ═══
        rag_context = ""
        try:
            rag_chunks = self.rag.retrieve_context(symptom, k=2)
            if rag_chunks:
                rag_context = "\n".join([
                    f"  [{c.get('source', 'Protocollo')}] {c.get('content', '')}" for c in rag_chunks
                ])
        except Exception as e:
            logger.error(f"❌ RAG error: {e}")

        # ═══ Conversation history (for context, NOT for preventing repeats) ═══
        chat_history = data.get("_chat_history", [])
        history_summary = ""
        if chat_history:
            # Only include last 6 messages (3 Q&A pairs) for conciseness
            recent = chat_history[-6:]
            lines = []
            for msg in recent:
                role = "Paziente" if msg.get("role") == "user" else "Medico"
                lines.append(f"  {role}: {msg.get('content', '')[:120]}")
            history_summary = "\n".join(lines)

        # ═══ Branch-specific format ═══
        if phase == TriagePhase.FAST_TRIAGE:
            format_instr = "Formato: multiple_choice con 2-3 opzioni BREVI. Tono urgente e diretto."
        elif phase == TriagePhase.RISK_ASSESSMENT:
            format_instr = "Formato: multiple_choice con 2-3 opzioni. Tono EMPATICO e non giudicante."
        else:
            format_instr = "Formato: multiple_choice con 3 opzioni A/B/C."

        # ═══ Build summary of already-collected clinical info ═══
        clinical_answers = data.get("_clinical_answers", [])
        already_collected = []
        if onset != "N/D":
            already_collected.append(f"  - Insorgenza: {onset}")
        for ans in clinical_answers:
            already_collected.append(f"  - {ans['topic']}: {ans['answer']}")
        collected_summary = "\n".join(already_collected) if already_collected else ""

        # ═══ STRICT prompt: topic is MANDATORY ═══
        prompt = f"""Rispondi ESCLUSIVAMENTE con JSON valido. NESSUN testo prima o dopo il JSON.

PAZIENTE: {symptom}, Dolore {pain}/10, Età {age}, Insorgenza {onset}
{f"INFORMAZIONI GIÀ RACCOLTE:{chr(10)}{collected_summary}" if collected_summary else ""}

═══════════════════════════════════════════════
ARGOMENTO OBBLIGATORIO: {topic}
═══════════════════════════════════════════════
{instruction}
{f"{chr(10)}RIFERIMENTI CLINICI:{chr(10)}{rag_context}" if rag_context else ""}

{f"ULTIME RISPOSTE PAZIENTE:{chr(10)}{history_summary}" if history_summary else ""}

{format_instr}

⚠️ REGOLE INVIOLABILI — se le violi la risposta viene scartata:
1. La domanda DEVE riguardare SOLO: {topic}
2. DEVE terminare con il carattere "?"
3. VIETATO ringraziare, riassumere, diagnosticare o consigliare
4. VIETATO usare frasi come "Grazie per", "In base a", "Ti consiglio"
5. Output: SOLO il JSON, niente altro

{{"text": "La tua domanda su {topic} qui?", "type": "multiple_choice", "options": ["A) Opzione 1", "B) Opzione 2", "C) Opzione 3"]}}"""

        try:
            response = self.llm.generate_with_json_parse(prompt, temperature=0.4)
            if response and response.get("text"):
                text = response["text"].strip()
                text_lower = text.lower()

                # ── Reject closing statements ──
                if any(bp in text_lower for bp in self.BLOCK_PHRASES):
                    logger.warning(f"⚠️ LLM ha generato chiusura: '{text[:60]}' → fallback")
                # ── Reject if text doesn't end with '?' (not a question) ──
                elif not text.rstrip().endswith("?"):
                    logger.warning(f"⚠️ LLM non ha generato domanda (no '?'): '{text[:60]}' → fallback")
                else:
                    # Ensure options are present for multiple_choice
                    if response.get("type") == "multiple_choice" and not response.get("options"):
                        response["options"] = self._extract_fallback_options(instruction)
                    logger.info(f"✅ Q{phase_q_count+1} ({topic}): {text[:80]}...")
                    return response
        except Exception as e:
            logger.error(f"❌ Clinical question error: {e}")

        # Deterministic fallback: build question directly from dimension instruction
        fallback_text = instruction.split("Opzioni:")[0].replace("Chiedi ", "").strip()
        # Clean up trailing period before adding '?'
        fallback_text = fallback_text.rstrip(".")
        return {
            "text": fallback_text + "?",
            "type": "multiple_choice",
            "options": self._extract_fallback_options(instruction)
        }

    @staticmethod
    def _extract_fallback_options(instruction: str) -> list:
        """Extract options from dimension instruction as fallback."""
        if "Opzioni:" in instruction:
            opts_text = instruction.split("Opzioni:")[1].strip()
            options = [opt.strip() for opt in opts_text.split(" B)")]
            if len(options) >= 2:
                first = options[0]
                rest = options[1:]
                result = [first]
                for i, opt in enumerate(rest):
                    parts = opt.split(" C)")
                    result.append(f"B) {parts[0].strip()}")
                    if len(parts) > 1:
                        result.append(f"C) {parts[1].strip()}")
                return result[:3]
        return ["A) Sì", "B) No", "C) Non saprei dire"]


# ============================================================================
# OUTCOME GENERATOR — DIRETTIVA 4: Structured SBAR via LLM JSON
# ============================================================================

class OutcomeGenerator:
    """
    Generates outcomes with STRUCTURED SBAR.
    LLM fills a rigid JSON template; Python formats it deterministically.
    """

    SBAR_PROMPT = """Sei un medico. Genera un report SBAR per questo caso. Rispondi SOLO con JSON.

DATI: Sintomo={symptom}, Dolore={pain}/10, Età={age}, Località={location}
Dettagli clinici: {details}
Struttura consigliata: {facility_name} — {facility_addr} — Tel: {facility_phone}

JSON OBBLIGATORIO (compila TUTTI i campi):
{{
  "dialogo_utente": "2-3 frasi empatiche per il paziente che spiegano cosa fare e dove andare",
  "sbar": {{
    "situation": "Descrizione concisa della situazione clinica attuale",
    "background": "Contesto: età, fattori rilevanti, storia",
    "assessment": "Valutazione clinica e livello urgenza stimato",
    "recommendation": "Raccomandazione: recarsi a [struttura] per [motivo]"
  }},
  "urgency_code": "verde",
  "urgency_emoji": "🟢"
}}

REGOLE urgency_code: dolore>=8 o emergenza="rosso"/"arancione", dolore 5-7="giallo", dolore<5="verde"."""

    def __init__(self, llm_service, data_loader):
        self.llm = llm_service
        self.kb = data_loader

    def generate(self, branch: TriageBranch, data: Dict) -> Dict:
        if branch == TriageBranch.INFO:
            return self._generate_info_response(data)

        if branch == TriageBranch.MENTAL_HEALTH and data.get("consent") == "no":
            return self._generate_hotline_response()

        # ═══ Find facility (with Directive 2 pre-filter) ═══
        location = data.get("location", "Bologna")
        pain = data.get("pain_scale", 5)
        age = data.get("age")
        patient_age = int(age) if age and str(age).isdigit() else None

        if branch == TriageBranch.EMERGENCY or (isinstance(pain, int) and pain >= 7):
            facility_type = "Pronto Soccorso"
        elif branch == TriageBranch.MENTAL_HEALTH:
            facility_type = "CSM"
        elif isinstance(pain, int) and pain >= 4:
            facility_type = "CAU"
        else:
            facility_type = "Medico di Base"

        facility = self.kb.find_healthcare_facility(location, facility_type, patient_age=patient_age)

        f_name = facility.get("nome", "N/D") if facility else f"{facility_type} {location}"
        f_addr = facility.get("indirizzo", "Contatta CUP per informazioni") if facility else "N/D"
        f_contatti = facility.get("contatti", {}) if facility else {}
        f_phone = f_contatti.get("telefono", "N/D") if isinstance(f_contatti, dict) else "N/D"

        # ═══ Structured SBAR via LLM (Directive 4) ═══
        symptom = data.get("chief_complaint", "Non specificato")
        details = ", ".join(data.get("symptom_details", [])) or "Nessun dettaglio aggiuntivo"

        prompt = self.SBAR_PROMPT.format(
            symptom=symptom, pain=pain, age=age or "N/D", location=location,
            details=details, facility_name=f_name, facility_addr=f_addr, facility_phone=f_phone
        )

        sbar_json = self.llm.generate_with_json_parse(prompt, temperature=0.1, max_tokens=600)

        if sbar_json and "sbar" in sbar_json:
            return self._format_structured(sbar_json, f_name, f_addr, f_phone, branch)

        logger.warning("⚠️ SBAR LLM failed, using template fallback")
        return self._template_fallback(branch, data, f_name, f_addr, f_phone)

    def _format_structured(self, sbar_json, f_name, f_addr, f_phone, branch):
        """Deterministic formatting of LLM-generated structured SBAR."""
        sbar = sbar_json.get("sbar", {})
        dialog = sbar_json.get("dialogo_utente", "Ti consiglio di recarti alla struttura indicata.")
        urgency_emoji = sbar_json.get("urgency_emoji", "🟢")
        urgency_code = sbar_json.get("urgency_code", "verde")

        outcome_text = f"""{dialog}

📍 **{f_name}**
{f_addr}
📞 {f_phone}

Porta con te questo report quando ti rechi alla struttura."""

        if branch == TriageBranch.EMERGENCY:
            outcome_text += "\n\n🚨 **Se i sintomi peggiorano, chiama il 118 immediatamente.**"
        elif branch == TriageBranch.MENTAL_HEALTH:
            outcome_text += "\n\n📞 **Numeri utili:** 118 (Emergenza) | 1522 (Antiviolenza) | Telefono Amico: 02 2327 2327"

        sbar_full = f"""**REPORT TRIAGE SIRAYA**
Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}

**S - SITUATION (Situazione)**
{sbar.get('situation', 'N/D')}

**B - BACKGROUND (Contesto)**
{sbar.get('background', 'N/D')}

**A - ASSESSMENT (Valutazione)**
{urgency_emoji} {sbar.get('assessment', 'N/D')}

**R - RECOMMENDATION (Raccomandazione)**
{sbar.get('recommendation', 'N/D')}
Struttura: {f_name}
Indirizzo: {f_addr}
Telefono: {f_phone}"""

        return {
            "text": outcome_text,
            "type": "outcome",
            "options": None,
            "metadata": {
                "sbar_full": sbar_full,
                "sbar_json": sbar_json,
                "facility": f_name,
                "urgency_code": urgency_code
            }
        }

    def _template_fallback(self, branch, data, f_name, f_addr, f_phone):
        """Template SBAR when LLM fails."""
        symptom = data.get("chief_complaint", "Non specificato")
        pain = data.get("pain_scale", "N/D")
        age = data.get("age", "N/D")
        location = data.get("location", "N/D")

        if branch == TriageBranch.EMERGENCY:
            urgency = "🔴 CODICE ROSSO/ARANCIONE"
        elif isinstance(pain, int) and pain >= 7:
            urgency = "🟠 CODICE ARANCIONE"
        elif isinstance(pain, int) and pain >= 4:
            urgency = "🟡 CODICE GIALLO"
        else:
            urgency = "🟢 CODICE VERDE"

        outcome_text = f"""Considerando i sintomi descritti, ti consiglio di rivolgerti a:

📍 **{f_name}**
{f_addr}
📞 {f_phone}

Porta con te questo report quando ti rechi alla struttura."""

        sbar_full = f"""**REPORT TRIAGE SIRAYA**
Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}

**S - SITUATION:** {symptom}. Intensità dolore: {pain}/10.
**B - BACKGROUND:** Età: {age} anni. Località: {location}.
**A - ASSESSMENT:** {urgency}
**R - RECOMMENDATION:** {f_name} — {f_addr} — Tel: {f_phone}"""

        return {
            "text": outcome_text, "type": "outcome", "options": None,
            "metadata": {"sbar_full": sbar_full, "facility": f_name}
        }

    def _generate_info_response(self, data):
        query = data.get("chief_complaint", data.get("_raw_symptom", data.get("_last_user_input", "informazioni")))
        location = data.get("location", "")
        results = self.kb.find_facilities_smart(query, location, limit=3)

        if results:
            facilities_text = "\n\n".join([
                f"📍 **{f.get('nome', 'N/A')}**\n   📫 {f.get('indirizzo', 'N/A')}\n"
                f"   📞 {f.get('contatti', {}).get('telefono', 'N/D') if isinstance(f.get('contatti'), dict) else 'N/D'}\n"
                f"   🕐 {f.get('orari', 'N/D')}"
                for f in results
            ])
            return {"text": f"Ecco le informazioni:\n\n{facilities_text}\n\nPosso aiutarti con altro?", "type": "open_text", "options": None, "metadata": {}}

        return {"text": "Non ho trovato risultati. Puoi dirmi quale servizio cerchi e in quale comune?", "type": "open_text", "options": None, "metadata": {}}

    def _generate_hotline_response(self):
        return {
            "text": "Capisco e rispetto la tua scelta. Ricorda che puoi contattare:\n\n📞 **118** — Emergenza sanitaria (24/7)\n📞 **1522** — Antiviolenza (24/7)\n📞 **Telefono Amico** — 02 2327 2327\n📞 **Telefono Azzurro** — 19696 (minori, 24/7)",
            "type": "outcome", "options": None, "metadata": {}
        }


# ============================================================================
# MAIN CONTROLLER V4.0 — Directive 3: Auto-Outcome
# ============================================================================

class TriageControllerV3:
    """
    Controller V4.0 — LLM-as-a-Judge paradigm.

    Key changes from V3.1:
    - LLMJudge replaces regex for symptom classification
    - Auto-outcome when all mandatory slots filled + clinical done
    - Structured SBAR via JSON schema
    - StateKeys.TRIAGE_PATH synced with TRIAGE_BRANCH
    """

    def __init__(self):
        from ..core.state_manager import get_state_manager
        from ..services.llm_service import get_llm_service
        from ..services.data_loader import get_data_loader
        from ..services.db_service import get_db_service
        from ..services.rag_service import get_rag_service

        self.state = get_state_manager()
        self.llm = get_llm_service()
        self.kb = get_data_loader()
        self.db = get_db_service()
        self.rag = get_rag_service()

        self.slot_filler = UnifiedSlotFiller()
        self.fsm = TriageFSM(self.state)
        self.question_gen = QuestionGenerator(self.llm, self.rag)
        self.outcome_gen = OutcomeGenerator(self.llm, self.kb)

    def process_user_input(self, user_input: str) -> Dict:
        """Main entry point. Interface unchanged for chat_view.py compatibility."""
        start_time = time.time()

        # 1. Retrieve state
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        current_phase = self.state.get(StateKeys.CURRENT_PHASE, TriagePhase.INTAKE.value)
        current_branch = self.state.get(StateKeys.TRIAGE_BRANCH)

        logger.info(f"📍 State: branch={current_branch}, phase={current_phase}")

        # 2. Slot filling (LLM-enhanced for symptoms, regex for structured data)
        extracted = self.slot_filler.extract(
            user_input, collected,
            llm_service=self.llm,
            current_phase=current_phase
        )
        collected.update(extracted)
        collected["_last_user_input"] = user_input

        # ═══ DEFENSIVE GUARD: If chief_complaint is set, _generic_symptom MUST be False ═══
        # Prevents edge cases where both are True (which would cause an infinite loop
        # asking "Dove provi dolore?" even after the body part was already provided).
        if collected.get("chief_complaint") and collected.get("_generic_symptom"):
            collected["_generic_symptom"] = False
            logger.info(f"🛡️ Defensive: forced _generic_symptom=False (chief_complaint='{collected['chief_complaint']}')")

        # 3. Classify branch (first time) — use LLM Judge hint + SmartRouter safety net
        if not current_branch:
            urgency_override = extracted.get("_urgency_override")

            if urgency_override == "emergency":
                current_branch = TriageBranch.EMERGENCY
            elif urgency_override == "mental_health":
                current_branch = TriageBranch.MENTAL_HEALTH
            elif urgency_override == "info":
                current_branch = TriageBranch.INFO
            else:
                # SmartRouter as safety net
                from ..controllers.smart_router import SmartRouter
                path, _ = SmartRouter.route(user_input)
                branch_map = {"A": TriageBranch.EMERGENCY, "B": TriageBranch.MENTAL_HEALTH,
                              "C": TriageBranch.STANDARD, "INFO": TriageBranch.INFO}
                current_branch = branch_map.get(path, TriageBranch.STANDARD)

            self.state.set(StateKeys.TRIAGE_BRANCH, current_branch.value)
            # FIX: Sync TRIAGE_PATH for chat_view.py's _render_disposition_summary
            self.state.set(StateKeys.TRIAGE_PATH, current_branch.value)
            logger.info(f"✅ Branch classified: {current_branch.value}")
        else:
            current_branch = TriageBranch(current_branch)

        # 4. Escalation C → A (check during standard triage)
        if current_branch == TriageBranch.STANDARD:
            from ..controllers.smart_router import SmartRouter
            if SmartRouter.check_escalation(user_input):
                current_branch = TriageBranch.EMERGENCY
                self.state.set(StateKeys.TRIAGE_BRANCH, "A")
                self.state.set(StateKeys.TRIAGE_PATH, "A")
                logger.warning(f"⚠️ ESCALATION C→A: '{user_input[:50]}'")

        # Clean up helper keys before saving
        for key in ["_current_phase", "_llm_judgment"]:
            collected.pop(key, None)
        self.state.set(StateKeys.COLLECTED_DATA, collected)

        # ═══════════════════════════════════════════════════════════
        # 5. DIRECTIVE 3: Auto-outcome check
        # If ALL mandatory slots filled AND clinical phase complete,
        # generate outcome IMMEDIATELY (no "Grazie" dead-end)
        # ═══════════════════════════════════════════════════════════
        if current_branch in (TriageBranch.STANDARD, TriageBranch.EMERGENCY):
            mandatory = ["chief_complaint", "location", "pain_scale", "age"] if current_branch == TriageBranch.STANDARD else ["location"]
            all_filled = all(k in collected for k in mandatory)
            phase_q = self.state.get("phase_question_count", 0)

            # Trigger auto-outcome if:
            # (a) Standard: clinical phase done (>=5 questions) OR hard cap (>=7)
            # (b) Emergency: fast triage done (>=3 questions)
            if current_branch == TriageBranch.STANDARD:
                clinical_done = (current_phase == "clinical_triage" and phase_q >= 5)
            else:
                clinical_done = (current_phase == "fast_triage" and phase_q >= 3)

            if all_filled and clinical_done:
                logger.info(f"⚡ Auto-outcome: slots OK + {phase_q} domande → OUTCOME")
                self.state.set(StateKeys.CURRENT_PHASE, TriagePhase.OUTCOME.value)
                response = self.outcome_gen.generate(current_branch, collected)
                self.state.set(StateKeys.SBAR_REPORT_DATA, response.get("metadata", {}).get("sbar_full", ""))
                self._log_interaction(user_input, response, current_branch, TriagePhase.OUTCOME, phase_q, start_time)
                return self._format_response(response, start_time)

        # 6. FSM transition
        prev_phase = TriagePhase(current_phase)
        phase_q_count = self.state.get("phase_question_count", 0)
        next_phase = self.fsm.next_phase(
            current_branch, prev_phase, collected, phase_q_count
        )
        phase_q_count = self.state.get("phase_question_count", 0)  # Re-read after FSM (may have reset)

        # ═══ FIX 2a: Reset counter on EVERY phase transition ═══
        if next_phase != prev_phase:
            # Only reset if we're entering a clinical/fast/risk phase from a non-clinical phase
            CLINICAL_PHASES = {TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT}
            if next_phase in CLINICAL_PHASES and prev_phase not in CLINICAL_PHASES:
                self.state.set("phase_question_count", 0)
                phase_q_count = 0
                logger.info(f"🔄 Counter reset: {prev_phase.value} → {next_phase.value}")

        # Auto-advance: if next phase already has data, skip forward
        MAX_AUTO = 3
        for _ in range(MAX_AUTO):
            if next_phase == TriagePhase.OUTCOME:
                break
            next_attempt = self.fsm.next_phase(current_branch, next_phase, collected, 0)
            if next_attempt == next_phase:
                break
            logger.info(f"🔄 Auto-advance: {next_phase.value} → {next_attempt.value}")
            next_phase = next_attempt
            phase_q_count = self.state.get("phase_question_count", 0)

        # ═══ FIX 2b: HARD CAP — force outcome at 7 questions regardless ═══
        CLINICAL_PHASES = {TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT}
        if next_phase in CLINICAL_PHASES and phase_q_count >= 7:
            logger.warning(f"⚠️ HARD CAP: {phase_q_count} domande → forza OUTCOME")
            next_phase = TriagePhase.OUTCOME

        self.state.set(StateKeys.CURRENT_PHASE, next_phase.value)

        # 7. Save clinical answer from previous question (before generating next)
        CLINICAL_PHASES = {TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT}
        if prev_phase in CLINICAL_PHASES and user_input.strip():
            # Save the user's answer with the dimension topic that was asked
            clinical_answers = collected.get("_clinical_answers", [])
            # Get the dimension that was asked in the PREVIOUS question
            symptom = collected.get("chief_complaint", "")
            if symptom:
                prev_dimension = self.question_gen._get_clinical_dimension(symptom, max(0, phase_q_count - 1))
                clinical_answers.append({
                    "topic": prev_dimension["topic"],
                    "answer": user_input.strip()[:200]  # Cap length
                })
                collected["_clinical_answers"] = clinical_answers
                self.state.set(StateKeys.COLLECTED_DATA, collected)
                logger.info(f"💬 Saved clinical answer: {prev_dimension['topic']} → {user_input[:60]}")

        # 8. Generate response
        if next_phase == TriagePhase.OUTCOME:
            response = self.outcome_gen.generate(current_branch, collected)
            self.state.set(StateKeys.SBAR_REPORT_DATA, response.get("metadata", {}).get("sbar_full", ""))
        else:
            # Inject chat history for clinical question generation (avoids repeating questions)
            messages = self.state.get(StateKeys.MESSAGES, [])
            if messages:
                # Pass last 14 messages (7 Q&A pairs) for context
                collected["_chat_history"] = messages[-14:]
            response = self.question_gen.generate(next_phase, current_branch, collected, phase_q_count)
            collected.pop("_chat_history", None)  # Don't persist chat history in collected

        # Validate response
        if not isinstance(response, dict) or "text" not in response:
            response = {"text": "Puoi fornirmi maggiori dettagli?", "type": "open_text", "options": None}

        # 9. Increment counter (ONLY for clinical/fast/risk phases)
        CLINICAL_PHASES_INC = {TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT}
        if next_phase in CLINICAL_PHASES_INC:
            self.state.set("phase_question_count", phase_q_count + 1)

        # 10. Log
        self._log_interaction(user_input, response, current_branch, next_phase, phase_q_count, start_time)

        return self._format_response(response, start_time)

    def _format_response(self, response, start_time):
        processing_time = int((time.time() - start_time) * 1000)
        return {
            "assistant_response": response.get("text", ""),
            "question_type": response.get("type", "open_text"),
            "options": response.get("options"),
            "metadata": response.get("metadata", {}),
            "processing_time_ms": processing_time
        }

    def _log_interaction(self, user_input, response, branch, phase, phase_q, start_time):
        processing_time = int((time.time() - start_time) * 1000)
        session_id = self.state.get(StateKeys.SESSION_ID, "unknown")
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        self.db.save_interaction(
            session_id=session_id,
            user_input=user_input,
            assistant_response=response.get("text", ""),
            processing_time_ms=processing_time,
            session_state={
                "branch": branch.value,
                "phase": phase.value,
                "phase_q_count": phase_q,
                "collected_data": collected,  # ← db_service expects "collected_data" key
                "chief_complaint": collected.get("chief_complaint"),
                "triage_path": branch.value,
                "urgency_level": 4 if branch == TriageBranch.EMERGENCY else 3,
            },
            metadata={}
        )


# ============================================================================
# SINGLETON via st.cache_resource (auto-invalidates when file changes)
# ============================================================================



def get_triage_controller():
    """
    Create controller instance.
    NOT cached — controller is lightweight (services LLM/RAG/DB/KB are cached separately).
    This ensures code changes to controller/FSM/QuestionGenerator are immediately active.
    """
    return TriageControllerV3()
