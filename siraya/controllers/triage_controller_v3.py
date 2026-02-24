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

        # ═══ ONSET ═══
        if "ieri" in user_lower:
            extracted[cls.KEYS["onset"]] = "ieri"
        elif "stamattina" in user_lower or "questa mattina" in user_lower:
            extracted[cls.KEYS["onset"]] = "stamattina"
        elif "oggi" in user_lower:
            extracted[cls.KEYS["onset"]] = "oggi"
        elif "settimana" in user_lower:
            extracted[cls.KEYS["onset"]] = "da una settimana"
        elif "mese" in user_lower or "mesi" in user_lower:
            extracted[cls.KEYS["onset"]] = "da un mese"

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

    # FIX 4: Phrases that indicate the LLM generated a closing/summary instead of a question
    BLOCK_PHRASES = [
        "grazie per", "grazie delle", "informazioni fornite", "dati raccolti",
        "riepilogo", "in sintesi", "riassumendo", "sulla base di",
        "ti consiglio", "ti suggerisco", "il mio consiglio",
    ]

    def _generate_clinical_question(self, phase, branch, data, phase_q_count):
        symptom = data.get("chief_complaint", "sintomo generico")
        pain = data.get("pain_scale", "N/D")
        age = data.get("age", "N/D")

        rag_context = "(Nessun protocollo specifico, usa conoscenza medica generale)"
        try:
            rag_chunks = self.rag.retrieve_context(symptom, k=3)
            if rag_chunks:
                rag_context = "\n\n".join([
                    f"[{c.get('source', 'Protocollo')}] {c.get('content', '')}" for c in rag_chunks
                ])
        except Exception as e:
            logger.error(f"❌ RAG error: {e}")

        if phase == TriagePhase.FAST_TRIAGE:
            branch_instr = "Domanda RAPIDA per emergenza. Formato: 2-3 opzioni SI/NO. Sii DIRETTO."
        elif phase == TriagePhase.RISK_ASSESSMENT:
            branch_instr = "Valutazione rischio salute mentale. Tono EMPATICO, NON giudicante."
        else:
            branch_instr = "Indagine clinica strutturata. Formato: multiple_choice con 3 opzioni A/B/C."

        # FIX 4a: STRICT JSON-only prompt
        prompt = f"""Genera UNA domanda clinica per il triage. Rispondi ESCLUSIVAMENTE con JSON valido.
NESSUN testo prima del JSON. NESSUN testo dopo il JSON. NESSUN commento. SOLO il JSON.

DATI PAZIENTE: Sintomo={symptom}, Dolore={pain}/10, Età={age}
Domanda numero: {phase_q_count + 1}

PROTOCOLLI:
{rag_context}

{branch_instr}

REGOLE:
1) Domanda SPECIFICA per il sintomo "{symptom}"
2) USA i protocolli clinici sopra
3) UNA SOLA domanda (NON fare riepiloghi, ringraziamenti o diagnosi)
4) Il campo "text" DEVE contenere una DOMANDA (deve terminare con "?")

{{"text": "La tua domanda specifica qui?", "type": "multiple_choice", "options": ["A) Prima opzione", "B) Seconda opzione", "C) Terza opzione"]}}"""

        try:
            response = self.llm.generate_with_json_parse(prompt, temperature=0.3)
            if response and response.get("text"):
                # FIX 4b: Detect "Grazie" / closing statements → skip
                text_lower = response["text"].lower()
                if any(bp in text_lower for bp in self.BLOCK_PHRASES):
                    logger.warning(f"⚠️ LLM ha generato chiusura invece di domanda: '{response['text'][:60]}' → fallback")
                else:
                    return response
        except Exception as e:
            logger.error(f"❌ Clinical question error: {e}")

        return {"text": f"Riguardo al {symptom}, puoi descrivermi le caratteristiche del disturbo?", "type": "open_text", "options": None}


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

        # 7. Generate response
        if next_phase == TriagePhase.OUTCOME:
            response = self.outcome_gen.generate(current_branch, collected)
            self.state.set(StateKeys.SBAR_REPORT_DATA, response.get("metadata", {}).get("sbar_full", ""))
        else:
            response = self.question_gen.generate(next_phase, current_branch, collected, phase_q_count)

        # Validate response
        if not isinstance(response, dict) or "text" not in response:
            response = {"text": "Puoi fornirmi maggiori dettagli?", "type": "open_text", "options": None}

        # 8. Increment counter (ONLY for clinical/fast/risk phases)
        if next_phase in CLINICAL_PHASES:
            self.state.set("phase_question_count", phase_q_count + 1)

        # 9. Log
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
            session_state={"branch": branch.value, "phase": phase.value, "phase_q_count": phase_q, "collected": collected},
            metadata={}
        )


# ============================================================================
# SINGLETON via st.cache_resource (auto-invalidates when file changes)
# ============================================================================

import streamlit as st


@st.cache_resource
def get_triage_controller():
    """Get controller instance. Streamlit manages lifecycle and auto-invalidates on file change."""
    return TriageControllerV3()
