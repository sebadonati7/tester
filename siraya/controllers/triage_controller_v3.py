"""
SIRAYA Triage Controller V3.1
REFACTORED — All critical bugs fixed + new features.

Fixes applied:
- FIX: Symptom specificity validation ("ho male" → asks "where?")
- FIX: Counter overwrite (read AFTER FSM transition)
- FIX: Age filter in facility search (no pediatric for adults)
- FIX: Auto-transition (no "Grazie per le informazioni" dead-ends)
- FIX: Branch INFO handler
- FIX: Escalation C → A
- FIX: Medicalization via SymptomNormalizer

Architecture:
- UnifiedSlotFiller: Data extraction with CANONICAL KEYS + specificity check
- TriageFSM: Tabular state machine (dict lookup)
- QuestionGenerator: RAG-driven clinical questions
- OutcomeGenerator: Brief + SBAR with age-filtered facility search

Single counter: phase_question_count (reset on clinical phase entry)
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
# ENUMS
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
# SLOT FILLER — FIX: Symptom Specificity Validation
# ============================================================================

class UnifiedSlotFiller:
    """
    Slot filling with PERSISTENT MEMORY and SPECIFICITY VALIDATION.
    
    FIX: "ho male" is now classified as GENERIC (not saved as chief_complaint).
    Only SPECIFIC symptoms (with body location) are accepted.
    
    RULE: chief_complaint is IMMUTABLE after first extraction.
    Details go to symptom_details (cumulative list).
    """
    
    # ✅ CANONICAL KEYS — Single source of truth
    KEYS = {
        "symptom": "chief_complaint",      # IMMUTABLE
        "details": "symptom_details",      # CUMULATIVE (list)
        "location": "location",
        "pain": "pain_scale",
        "age": "age",
        "gender": "gender",
        "onset": "onset"
    }
    
    # FIX: Specific symptom patterns (contain body location)
    SPECIFIC_SYMPTOM_PATTERNS = [
        r"mal\s+di\s+(testa|pancia|stomaco|schiena|gola|denti|orecchio|gambe?)",
        r"dolore\s+(al|alla|alle|ai|allo)\s+\w+",
        r"male\s+(al|alla|alle|ai|allo)\s+\w+",
        r"mi\s+fa\s+male\s+(il|la|le|i|lo)\s+\w+",
        r"ho\s+(la\s+)?(febbre|tosse|nausea|vomito|diarrea|vertigini|prurito)",
        r"non\s+(riesco\s+a|sento|vedo|respiro)",
        r"mi\s+(brucia|pizzica|prude|gira)\s+",
        r"(taglio|tagliato|ferita|frattura|ustione|gonfiore|eruzione)\b",
        r"(dolore|male)\s+(forte|acuto|continuo|persistente)\s+(al|alla|alle|ai|allo)\s+\w+",
    ]
    
    # FIX: Generic symptom indicators → require deepening
    GENERIC_SYMPTOM_INDICATORS = [
        "ho male", "sto male", "mi sento male", "non sto bene",
        "ho un problema", "ho un dolore", "mi fa male",
        "non mi sento bene", "ho bisogno di aiuto",
        "mi sento poco bene", "ho dei problemi"
    ]
    
    # Body part extraction for specific symptoms
    BODY_PARTS = [
        "testa", "pancia", "stomaco", "schiena", "gola", "denti",
        "orecchio", "orecchie", "braccio", "braccia", "gamba", "gambe",
        "petto", "torace", "addome", "collo", "spalla", "spalle",
        "ginocchio", "ginocchia", "piede", "piedi", "mano", "mani",
        "occhio", "occhi", "naso", "bocca", "fianco", "fianchi",
        "schiena", "caviglia", "polso", "anca"
    ]
    
    @classmethod
    def extract(cls, user_input: str, current_data: Dict = None) -> Dict[str, Any]:
        """
        Extract data with MEMORY: never overwrites chief_complaint.
        FIX: Distinguishes SPECIFIC vs GENERIC symptoms.
        """
        if current_data is None:
            current_data = {}
        
        extracted = {}
        user_lower = user_input.lower().strip()
        
        # === SYMPTOM (IMMUTABLE) ===
        if "chief_complaint" not in current_data:
            # FIX: Step 1 — Check SPECIFIC symptom patterns
            specific_found = False
            for pattern in cls.SPECIFIC_SYMPTOM_PATTERNS:
                match = re.search(pattern, user_lower)
                if match:
                    extracted[cls.KEYS["symptom"]] = user_input.strip()[:100]
                    specific_found = True
                    logger.info(f"✅ Sintomo SPECIFICO salvato: {user_input[:40]}")
                    
                    # Also extract body part if present in symptom
                    for part in cls.BODY_PARTS:
                        if part in user_lower:
                            extracted["_body_part"] = part
                            logger.info(f"✅ Localizzazione anatomica: {part}")
                            break
                    break
            
            # FIX: Step 2 — Check GENERIC symptom (do NOT save as chief_complaint)
            if not specific_found:
                for indicator in cls.GENERIC_SYMPTOM_INDICATORS:
                    if indicator in user_lower:
                        # Mark as generic — FSM will ask "Where does it hurt?"
                        extracted["_generic_symptom"] = True
                        extracted["_raw_symptom"] = user_input.strip()
                        logger.info(f"⚠️ Sintomo GENERICO rilevato: '{user_input[:40]}' → richiedo approfondimento")
                        break
        
        # === SYMPTOM DETAILS (CUMULATIVE) ===
        detail_keywords = {
            "costante": "dolore costante",
            "intermittente": "intermittente",
            "pulsante": "pulsante",
            "localizzato": "localizzato",
            "diffuso": "diffuso",
            "acuto": "dolore acuto",
            "sordo": "dolore sordo",
            "bruciante": "bruciante",
            "lancinante": "lancinante",
        }
        
        for kw, desc in detail_keywords.items():
            if kw in user_lower:
                existing = current_data.get(cls.KEYS["details"], [])
                if desc not in existing:
                    extracted[cls.KEYS["details"]] = existing + [desc]
                    logger.info(f"✅ Dettaglio: {desc}")
                break
        
        # === CONTEXT AWARENESS: use current phase ===
        current_phase = current_data.get("_current_phase", "")
        
        # === PAIN SCALE ===
        if current_phase == "pain_scale" or "dolore" in user_lower or "/" in user_input:
            pain_patterns = [
                r'(\d{1,2})\s*-\s*(\d{1,2}):\s*',  # "7-8: Forte" → group(1)=7
                r'(\d{1,2})\s*/\s*10',              # "7/10"
                r'(\d{1,2})\s+su\s+10',             # "7 su 10"
            ]
            
            for pattern in pain_patterns:
                match = re.search(pattern, user_lower)
                if match:
                    try:
                        scale = int(match.group(1))
                        if 1 <= scale <= 10:
                            extracted[cls.KEYS["pain"]] = scale
                            logger.info(f"✅ Dolore: {scale}/10")
                            break
                    except (IndexError, ValueError):
                        pass
            
            # FIX: Also handle text-based pain selections
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
                        logger.info(f"✅ Dolore (testo): {kw} → {val}/10")
                        break
        
        # === AGE (STRICT: only in demographics phase AND standalone number) ===
        if "age" not in current_data and current_phase == "demographics":
            age_patterns = [
                r'^(\d{1,3})$',                 # "56" (strict standalone)
                r'\b(\d{1,3})\s+ann[io]',       # "56 anni"
                r'ho\s+(\d{1,3})\s+ann',        # "ho 56 anni"
                r'ne\s+ho\s+(\d{1,3})',         # "ne ho 36"
            ]
            
            for pattern in age_patterns:
                match = re.search(pattern, user_lower)
                if match:
                    try:
                        age = int(match.group(1))
                        if 0 < age < 120:
                            extracted[cls.KEYS["age"]] = age
                            logger.info(f"✅ Età: {age}")
                            break
                    except (IndexError, ValueError):
                        pass
        
        # === LOCATION ===
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
                logger.info(f"✅ Località estratta: {comune.title()}")
                break
        
        # === ONSET ===
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
        
        # === CONSENT (for Mental Health branch) ===
        if current_phase == "consent":
            positive = ["sì", "si", "ok", "va bene", "accetto", "certo", "d'accordo", "procedi"]
            negative = ["no", "non voglio", "rifiuto", "preferisco di no"]
            if any(w in user_lower for w in positive):
                extracted["consent"] = "yes"
            elif any(w in user_lower for w in negative):
                extracted["consent"] = "no"
        
        return extracted


# ============================================================================
# FSM — Tabular State Machine (FIX: generic symptom handling)
# ============================================================================

class TriageFSM:
    """
    Finite State Machine with transition table.
    Zero nested if-else, only dict lookup.
    """
    
    def __init__(self, state_manager):
        self.state = state_manager
        
        # ✅ Transition table: (branch, phase) → transition_function
        self.transitions = {
            # Branch C: STANDARD
            (TriageBranch.STANDARD, TriagePhase.INTAKE): self._std_from_intake,
            (TriageBranch.STANDARD, TriagePhase.CHIEF_COMPLAINT): self._std_from_complaint,
            (TriageBranch.STANDARD, TriagePhase.LOCALIZATION): self._std_from_location,
            (TriageBranch.STANDARD, TriagePhase.PAIN_SCALE): self._std_from_pain,
            (TriageBranch.STANDARD, TriagePhase.DEMOGRAPHICS): self._std_from_demographics,
            (TriageBranch.STANDARD, TriagePhase.CLINICAL_TRIAGE): self._std_from_clinical,
            (TriageBranch.STANDARD, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,
            
            # Branch A: EMERGENCY
            (TriageBranch.EMERGENCY, TriagePhase.INTAKE): self._emg_from_intake,
            (TriageBranch.EMERGENCY, TriagePhase.LOCALIZATION): self._emg_from_location,
            (TriageBranch.EMERGENCY, TriagePhase.FAST_TRIAGE): self._emg_from_fast,
            (TriageBranch.EMERGENCY, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,
            
            # Branch B: MENTAL_HEALTH
            (TriageBranch.MENTAL_HEALTH, TriagePhase.INTAKE): lambda d, q: TriagePhase.CONSENT,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.CONSENT): self._mh_from_consent,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.DEMOGRAPHICS): self._mh_from_demographics,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.RISK_ASSESSMENT): self._mh_from_risk,
            (TriageBranch.MENTAL_HEALTH, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,
            
            # FIX: Branch INFO
            (TriageBranch.INFO, TriagePhase.INTAKE): self._info_from_intake,
            (TriageBranch.INFO, TriagePhase.OUTCOME): lambda d, q: TriagePhase.OUTCOME,
        }
    
    def next_phase(
        self, 
        branch: TriageBranch, 
        current: TriagePhase, 
        data: Dict, 
        phase_q_count: int
    ) -> TriagePhase:
        """Determine next phase via lookup table. O(1) complexity."""
        key = (branch, current)
        func = self.transitions.get(key)
        
        if func:
            return func(data, phase_q_count)
        
        # Fallback: stay in current phase
        logger.warning(f"⚠️ No transition for {key}, staying in {current.value}")
        return current
    
    # === STANDARD TRANSITIONS ===
    
    def _std_from_intake(self, data: Dict, q: int) -> TriagePhase:
        # FIX: If generic symptom detected, ask for deepening
        if data.get("_generic_symptom") and "chief_complaint" not in data:
            return TriagePhase.CHIEF_COMPLAINT  # "Where does it hurt?"
        if "chief_complaint" in data:
            if "location" in data:
                return TriagePhase.PAIN_SCALE
            return TriagePhase.LOCALIZATION
        return TriagePhase.CHIEF_COMPLAINT
    
    def _std_from_complaint(self, data: Dict, q: int) -> TriagePhase:
        if "chief_complaint" in data:
            if "location" in data:
                return TriagePhase.PAIN_SCALE
            return TriagePhase.LOCALIZATION
        return TriagePhase.CHIEF_COMPLAINT
    
    def _std_from_location(self, data: Dict, q: int) -> TriagePhase:
        return TriagePhase.PAIN_SCALE if "location" in data else TriagePhase.LOCALIZATION
    
    def _std_from_pain(self, data: Dict, q: int) -> TriagePhase:
        if "pain_scale" in data:
            logger.info(f"✅ Pain scale trovato: {data['pain_scale']}, avanzando")
            return TriagePhase.DEMOGRAPHICS
        return TriagePhase.PAIN_SCALE
    
    def _std_from_demographics(self, data: Dict, q: int) -> TriagePhase:
        if "age" in data:
            # ✅ RESET counter when entering clinical phase
            self.state.set("phase_question_count", 0)
            logger.info("🔄 Entrando in CLINICAL_TRIAGE, reset counter")
            return TriagePhase.CLINICAL_TRIAGE
        return TriagePhase.DEMOGRAPHICS
    
    def _std_from_clinical(self, data: Dict, phase_q_count: int) -> TriagePhase:
        """
        Exit from clinical triage ONLY if:
        - Minimum 5 questions AND complete data
        - OR 7 questions (absolute max)
        """
        required_keys = ["chief_complaint", "location", "pain_scale", "age"]
        has_all = all(k in data for k in required_keys)
        
        if phase_q_count >= 5 and has_all:
            logger.info(f"✅ Clinical complete: {phase_q_count} domande + dati OK → OUTCOME")
            return TriagePhase.OUTCOME
        
        if phase_q_count >= 7:
            logger.warning(f"⚠️ Max 7 domande clinical → forzo OUTCOME")
            return TriagePhase.OUTCOME
        
        logger.info(f"⏸️ Clinical continua: domanda {phase_q_count + 1}/7")
        return TriagePhase.CLINICAL_TRIAGE
    
    # === EMERGENCY TRANSITIONS ===
    
    def _emg_from_intake(self, data: Dict, q: int) -> TriagePhase:
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION
    
    def _emg_from_location(self, data: Dict, q: int) -> TriagePhase:
        if "location" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.FAST_TRIAGE
        return TriagePhase.LOCALIZATION
    
    def _emg_from_fast(self, data: Dict, phase_q_count: int) -> TriagePhase:
        if phase_q_count >= 3:
            logger.info(f"✅ Fast triage complete: {phase_q_count} domande → OUTCOME")
            return TriagePhase.OUTCOME
        return TriagePhase.FAST_TRIAGE
    
    # === MENTAL HEALTH TRANSITIONS ===
    
    def _mh_from_consent(self, data: Dict, q: int) -> TriagePhase:
        if data.get("consent") == "yes":
            return TriagePhase.DEMOGRAPHICS
        if data.get("consent") == "no":
            return TriagePhase.OUTCOME  # Consent refused → outcome with hotline
        return TriagePhase.CONSENT  # Still waiting for consent answer
    
    def _mh_from_demographics(self, data: Dict, q: int) -> TriagePhase:
        if "age" in data:
            self.state.set("phase_question_count", 0)
            return TriagePhase.RISK_ASSESSMENT
        return TriagePhase.DEMOGRAPHICS
    
    def _mh_from_risk(self, data: Dict, phase_q_count: int) -> TriagePhase:
        if phase_q_count >= 4:
            logger.info(f"✅ Risk assessment complete: {phase_q_count} domande → OUTCOME")
            return TriagePhase.OUTCOME
        return TriagePhase.RISK_ASSESSMENT
    
    # === INFO TRANSITIONS ===  # FIX: New
    
    def _info_from_intake(self, data: Dict, q: int) -> TriagePhase:
        """INFO: go directly to outcome (search KB and respond)."""
        return TriagePhase.OUTCOME


# ============================================================================
# QUESTION GENERATOR — FIX: Generic symptom handling + Medicalization
# ============================================================================

class QuestionGenerator:
    """
    Generates questions using RAG for clinical phases.
    FIX: Handles generic symptoms by asking "where does it hurt?"
    FIX: Integrates SymptomNormalizer for medicalization.
    """
    
    def __init__(self, llm_service, rag_service):
        self.llm = llm_service
        self.rag = rag_service
        # FIX: Medicalization support
        try:
            from ..services.symptom_normalizer import SymptomNormalizer
            self.normalizer = SymptomNormalizer()
        except Exception:
            self.normalizer = None
            logger.warning("⚠️ SymptomNormalizer non disponibile")
    
    def generate(
        self, 
        phase: TriagePhase, 
        branch: TriageBranch, 
        data: Dict, 
        phase_q_count: int
    ) -> Dict:
        """Generate appropriate question for phase."""
        
        # === INTAKE PHASES: Fixed questions ===
        
        if phase == TriagePhase.CHIEF_COMPLAINT:
            # FIX: If generic symptom detected, ask for body location
            if data.get("_generic_symptom"):
                return {
                    "text": "Capisco che non ti senti bene. Per poterti aiutare al meglio, puoi dirmi dove provi dolore o fastidio? Ad esempio: testa, pancia, petto, schiena...",
                    "type": "open_text",
                    "options": None
                }
            return {
                "text": "Qual è il motivo del tuo contatto oggi?",
                "type": "open_text",
                "options": None
            }
        
        if phase == TriagePhase.LOCALIZATION:
            return {
                "text": "In quale comune dell'Emilia-Romagna ti trovi?",
                "type": "open_text",
                "options": None
            }
        
        if phase == TriagePhase.PAIN_SCALE:
            return {
                "text": "Su una scala da 1 a 10, quanto è intenso il dolore?",
                "type": "multiple_choice",
                "options": [
                    "1-3: Lieve",
                    "4-6: Moderato",
                    "7-8: Forte",
                    "9-10: Insopportabile"
                ]
            }
        
        if phase == TriagePhase.DEMOGRAPHICS:
            return {
                "text": "Quanti anni hai?",
                "type": "open_text",
                "options": None
            }
        
        if phase == TriagePhase.CONSENT:
            return {
                "text": "Per poterti aiutare al meglio, avrei bisogno di farti alcune domande personali. Sei d'accordo a procedere?",
                "type": "multiple_choice",
                "options": ["Sì, procedi", "Preferisco di no"]
            }
        
        # === CLINICAL PHASES: RAG-driven questions ===
        
        if phase in [TriagePhase.CLINICAL_TRIAGE, TriagePhase.FAST_TRIAGE, TriagePhase.RISK_ASSESSMENT]:
            return self._generate_clinical_question(phase, branch, data, phase_q_count)
        
        # FIX: No more "Grazie per le informazioni" fallback
        # If we reach here, it's a programming error — log and return a safe question
        logger.error(f"❌ QuestionGenerator: unexpected phase {phase.value}, branch {branch.value}")
        return {
            "text": "Puoi fornirmi maggiori dettagli sulla tua situazione?",
            "type": "open_text",
            "options": None
        }
    
    def _generate_clinical_question(
        self,
        phase: TriagePhase,
        branch: TriageBranch,
        data: Dict,
        phase_q_count: int
    ) -> Dict:
        """Generate RAG-driven clinical question."""
        symptom = data.get("chief_complaint", "sintomo generico")
        pain = data.get("pain_scale", "N/D")
        age = data.get("age", "N/D")
        
        # FIX: Medicalization — normalize symptom for better RAG retrieval
        normalized_symptom = symptom
        if self.normalizer:
            try:
                normalized_symptom = self.normalizer.normalize(symptom)
                if normalized_symptom != symptom:
                    logger.info(f"🔄 Medicalizzazione: '{symptom}' → '{normalized_symptom}'")
            except Exception as e:
                logger.warning(f"⚠️ Medicalization error: {e}")
        
        # ✅ FORCE RAG: Always active for clinical phases
        rag_context = "(Nessun protocollo specifico trovato, usa conoscenza medica generale)"
        try:
            rag_chunks = self.rag.retrieve_context(normalized_symptom, k=3)
            if rag_chunks:
                rag_context = "\n\n".join([
                    f"[{chunk.get('source', 'Protocollo')}] {chunk.get('content', '')}"
                    for chunk in rag_chunks
                ])
                logger.info(f"✅ RAG: {len(rag_chunks)} protocolli per '{normalized_symptom}'")
            else:
                logger.warning(f"⚠️ RAG: Nessun protocollo per '{normalized_symptom}'")
        except Exception as e:
            logger.error(f"❌ RAG error: {e}")
        
        # Branch-specific prompt customization
        if phase == TriagePhase.FAST_TRIAGE:
            branch_instructions = """**TIPO DOMANDA:** Domanda RAPIDA per emergenza.
Formato: domanda chiusa con 2-3 opzioni SI/NO o scelta singola.
Sii DIRETTO, VELOCE."""
        elif phase == TriagePhase.RISK_ASSESSMENT:
            branch_instructions = """**TIPO DOMANDA:** Valutazione rischio salute mentale.
Sii EMPATICO e NON giudicante. Domande su stato emotivo, pensieri, supporto sociale.
Formato: domanda aperta e sensibile."""
        else:
            branch_instructions = """**TIPO DOMANDA:** Indagine clinica strutturata.
Formato: multiple_choice con 3 opzioni A/B/C.
Indaga caratteristiche diagnostiche rilevanti."""
        
        prompt = f"""
Sei un medico esperto in triage telefonico. Genera la domanda {phase_q_count + 1} per questo caso clinico.

**DATI PAZIENTE:**
- Sintomo: {normalized_symptom}
- Intensità dolore: {pain}/10
- Età: {age} anni

**PROTOCOLLI CLINICI PERTINENTI:**
{rag_context}

{branch_instructions}

**REGOLE CRITICHE:**
1. Domanda SPECIFICA per il sintomo (NON generica)
2. USA i protocolli sopra per formulare domanda mirata
3. Indaga caratteristiche diagnostiche rilevanti
4. UNA SOLA domanda

**ESEMPI DI DOMANDE BUONE:**
- Per dolore addominale: "Il dolore è localizzato in un punto preciso o è diffuso in tutta la pancia?"
- Per cefalea: "Il dolore è pulsante (tipo martello) o costante e pressorio?"
- Per dolore toracico: "Il dolore si irradia al braccio sinistro o alla mascella?"

**OUTPUT JSON:**
{{
  "text": "Domanda specifica basata sui protocolli",
  "type": "multiple_choice",
  "options": ["Opzione A", "Opzione B", "Opzione C"]
}}
"""
        
        try:
            response = self.llm.generate_with_json_parse(prompt, temperature=0.3)
            if response and response.get("text"):
                logger.info(f"✅ Domanda clinica: {response.get('text', '')[:60]}...")
                return response
        except Exception as e:
            logger.error(f"❌ Errore generation clinica: {e}")
        
        # Intelligent fallback (not generic "Grazie")
        return {
            "text": f"Riguardo al {normalized_symptom}, puoi descrivermi meglio le caratteristiche del disturbo?",
            "type": "open_text",
            "options": None
        }


# ============================================================================
# OUTCOME GENERATOR — FIX: Age filter + INFO handler
# ============================================================================

class OutcomeGenerator:
    """
    Generates OUTCOME brief + complete SBAR (separate).
    FIX: Passes patient_age to facility search for age validation.
    FIX: Handles Branch INFO.
    """
    
    def __init__(self, llm_service, data_loader):
        self.llm = llm_service
        self.kb = data_loader
    
    def generate(self, branch: TriageBranch, data: Dict) -> Dict:
        """Generate outcome based on branch type."""
        
        # FIX: Branch INFO handler
        if branch == TriageBranch.INFO:
            return self._generate_info_response(data)
        
        # FIX: Branch B with refused consent → hotline only
        if branch == TriageBranch.MENTAL_HEALTH and data.get("consent") == "no":
            return self._generate_hotline_response()
        
        # Standard outcome generation
        location = data.get("location", "Bologna")
        pain = data.get("pain_scale", 5)
        
        # FIX: Extract age for facility filtering
        age = data.get("age")
        patient_age = int(age) if age and str(age).isdigit() else None
        
        # Facility type routing logic
        if branch == TriageBranch.EMERGENCY or (isinstance(pain, int) and pain >= 7):
            facility_type = "Pronto Soccorso"
        elif branch == TriageBranch.MENTAL_HEALTH:
            facility_type = "CSM"  # Centro Salute Mentale
        elif isinstance(pain, int) and pain >= 4:
            facility_type = "CAU"
        else:
            facility_type = "Medico di Base"
        
        # FIX: Pass patient_age to facility search
        facility = self.kb.find_healthcare_facility(
            location, facility_type, patient_age=patient_age
        )
        
        if facility:
            facility_name = facility.get("nome", "N/D")
            facility_address = facility.get("indirizzo", "N/D")
            contatti = facility.get("contatti", {})
            facility_phone = contatti.get("telefono", "N/D") if isinstance(contatti, dict) else "N/D"
        else:
            facility_name = f"{facility_type} {location}"
            facility_address = "Contatta CUP per informazioni"
            facility_phone = "N/D"
        
        # Emergency-specific additions
        emergency_addon = ""
        if branch == TriageBranch.EMERGENCY:
            emergency_addon = "\n\n🚨 **Se i sintomi peggiorano, chiama il 118 immediatamente.**"
        
        # Mental health-specific additions
        mh_addon = ""
        if branch == TriageBranch.MENTAL_HEALTH:
            mh_addon = "\n\n📞 **Numeri utili:**\n- 118 (Emergenza 24/7)\n- 1522 (Antiviolenza 24/7)\n- Telefono Amico: 02 2327 2327"
        
        # Generate brief outcome
        outcome_brief = f"""Considerando i sintomi descritti, ti consiglio di rivolgerti a:

📍 **{facility_name}**
{facility_address}
📞 {facility_phone}

Porta con te questo report quando ti rechi alla struttura.{emergency_addon}{mh_addon}"""
        
        # Generate complete SBAR (for download)
        sbar_full = self._generate_sbar(branch, data, facility_name)
        
        return {
            "text": outcome_brief,
            "type": "outcome",
            "options": None,
            "metadata": {
                "sbar_full": sbar_full,
                "facility": facility_name
            }
        }
    
    def _generate_info_response(self, data: Dict) -> Dict:
        """FIX: Handle INFO branch — search KB and respond directly."""
        query = data.get("chief_complaint", data.get("_raw_symptom", ""))
        location = data.get("location", "")
        
        # Use user's original input as query
        if not query:
            query = data.get("_last_user_input", "informazioni")
        
        results = self.kb.find_facilities_smart(query, location, limit=3)
        
        if results:
            facilities_text = "\n\n".join([
                f"📍 **{f.get('nome', 'N/A')}**\n"
                f"   📫 {f.get('indirizzo', 'N/A')}\n"
                f"   📞 {f.get('contatti', {}).get('telefono', 'N/D') if isinstance(f.get('contatti'), dict) else 'N/D'}\n"
                f"   🕐 {f.get('orari', 'N/D')}"
                for f in results
            ])
            return {
                "text": f"Ecco le informazioni che ho trovato:\n\n{facilities_text}\n\nPosso aiutarti con altro?",
                "type": "open_text",
                "options": None,
                "metadata": {}
            }
        else:
            return {
                "text": "Non ho trovato risultati specifici per la tua ricerca. Puoi darmi più dettagli? Ad esempio: quale servizio cerchi e in quale comune?",
                "type": "open_text",
                "options": None,
                "metadata": {}
            }
    
    def _generate_hotline_response(self) -> Dict:
        """Generate response when mental health consent is refused."""
        return {
            "text": """Capisco e rispetto la tua scelta. Ricorda che puoi contattare questi servizi in qualsiasi momento:

📞 **Numeri utili:**
- **118** — Emergenza sanitaria (24/7)
- **1522** — Antiviolenza e stalking (24/7)
- **Telefono Amico** — 02 2327 2327 (tutti i giorni 10-24)
- **Telefono Azzurro** — 19696 (per minori, 24/7)

Non esitare a richiedere aiuto quando ne senti il bisogno.""",
            "type": "outcome",
            "options": None,
            "metadata": {}
        }
    
    def _generate_sbar(self, branch: TriageBranch, data: Dict, facility: str) -> str:
        """Generate SBAR with original symptom + details."""
        
        symptom_original = data.get("chief_complaint", "Non specificato")
        symptom_details = data.get("symptom_details", [])
        
        if symptom_details:
            symptom_full = f"{symptom_original} ({', '.join(symptom_details)})"
        else:
            symptom_full = symptom_original
        
        pain = data.get("pain_scale", "N/D")
        age = data.get("age", "N/D")
        gender = data.get("gender", "N/D")
        location = data.get("location", "N/D")
        onset = data.get("onset", "Non specificato")
        
        # Urgency code based on branch + pain
        if branch == TriageBranch.EMERGENCY:
            urgency = "🔴 CODICE ROSSO/ARANCIONE"
        elif isinstance(pain, int) and pain >= 7:
            urgency = "🟡 CODICE GIALLO"
        else:
            urgency = "🟢 CODICE VERDE"
        
        sbar = f"""
**REPORT TRIAGE SIRAYA**
Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}

**S - SITUATION (Situazione)**
{symptom_full}. Intensità dolore: {pain}/10. Insorgenza: {onset}.

**B - BACKGROUND (Contesto)**
Età: {age} anni
Sesso: {gender}
Località: {location}

**A - ASSESSMENT (Valutazione)**
Triage Branch {branch.value} completato.
Urgenza stimata: {urgency}

**R - RECOMMENDATION (Raccomandazione)**
Struttura consigliata: {facility}
"""
        return sbar.strip()


# ============================================================================
# MAIN CONTROLLER V3.1 — All fixes integrated
# ============================================================================

class TriageControllerV3:
    """
    Controller V3.1 — Refactored with all critical fixes.
    
    Fixes:
    - Symptom specificity validation
    - Counter overwrite prevention
    - Age-filtered facility search
    - Auto-transition (no dead-ends)
    - Branch INFO support
    - Escalation C → A
    - Medicalization via SymptomNormalizer
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
        
        # ✅ Components (Separation of Concerns)
        self.slot_filler = UnifiedSlotFiller()
        self.fsm = TriageFSM(self.state)
        self.question_gen = QuestionGenerator(self.llm, self.rag)
        self.outcome_gen = OutcomeGenerator(self.llm, self.kb)
    
    def process_user_input(self, user_input: str) -> Dict:
        """
        Main entry point — REFACTORED with all fixes.
        
        Returns:
            {
                "assistant_response": str,
                "question_type": str,
                "options": List[str] | None,
                "metadata": Dict,
                "processing_time_ms": int
            }
        """
        start_time = time.time()
        
        # 1. Retrieve state
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        current_phase = self.state.get(StateKeys.CURRENT_PHASE, TriagePhase.INTAKE.value)
        current_branch = self.state.get(StateKeys.TRIAGE_BRANCH)
        
        logger.info(f"📍 Stato: branch={current_branch}, phase={current_phase}")
        
        # 2. Classify branch (first time)
        if not current_branch:
            current_branch = self._classify_branch(user_input)
            self.state.set(StateKeys.TRIAGE_BRANCH, current_branch.value)
            logger.info(f"✅ Branch: {current_branch.value}")
        else:
            current_branch = TriageBranch(current_branch)
        
        # FIX: Escalation C → A
        if current_branch == TriageBranch.STANDARD:
            from ..controllers.smart_router import SmartRouter
            if SmartRouter.check_escalation(user_input):
                current_branch = TriageBranch.EMERGENCY
                self.state.set(StateKeys.TRIAGE_BRANCH, "A")
                logger.warning(f"⚠️ ESCALATION C→A: '{user_input[:50]}'")
        
        # 3. Slot filling (PASS current_data for memory + phase context)
        collected["_current_phase"] = current_phase  # Add phase context
        collected["_last_user_input"] = user_input    # FIX: Track last input for INFO
        extracted = self.slot_filler.extract(user_input, collected)
        collected.update(extracted)
        # Remove helpers after extraction
        collected.pop("_current_phase", None)
        self.state.set(StateKeys.COLLECTED_DATA, collected)
        
        # 4. FSM: determine next phase
        phase_q_count = self.state.get("phase_question_count", 0)
        next_phase = self.fsm.next_phase(
            branch=current_branch,
            current=TriagePhase(current_phase),
            data=collected,
            phase_q_count=phase_q_count
        )
        
        # FIX: Read counter AFTER transition (FSM may have reset it)
        phase_q_count = self.state.get("phase_question_count", 0)
        
        if next_phase.value != current_phase:
            logger.info(f"🔄 Transizione: {current_phase} → {next_phase.value}")
        
        # FIX: AUTO-TRANSITION — if new phase already has all data, advance further
        MAX_AUTO_TRANSITIONS = 3  # Safety: avoid infinite loops
        transitions = 0
        while transitions < MAX_AUTO_TRANSITIONS:
            if next_phase == TriagePhase.OUTCOME:
                break  # Don't auto-advance past outcome
            
            # Check if current phase already has all needed data
            next_attempt = self.fsm.next_phase(
                current_branch, next_phase, collected, 0
            )
            if next_attempt == next_phase:
                break  # FSM says stay here → need user input
            
            logger.info(f"🔄 Auto-transizione: {next_phase.value} → {next_attempt.value}")
            next_phase = next_attempt
            # Re-read counter after potential FSM reset
            phase_q_count = self.state.get("phase_question_count", 0)
            transitions += 1
        
        self.state.set(StateKeys.CURRENT_PHASE, next_phase.value)
        
        # 5. Generate response
        if next_phase == TriagePhase.OUTCOME:
            response = self.outcome_gen.generate(current_branch, collected)
            # Save SBAR for download
            self.state.set(
                StateKeys.SBAR_REPORT_DATA, 
                response.get("metadata", {}).get("sbar_full", "")
            )
        else:
            response = self.question_gen.generate(
                next_phase, current_branch, collected, phase_q_count
            )
        
        # FIX: Validate response — no more "Grazie per le informazioni" fallback
        if not isinstance(response, dict) or "text" not in response:
            logger.warning("⚠️ Response non valida, generando domanda di sicurezza")
            response = {
                "text": "Puoi fornirmi maggiori dettagli per aiutarti al meglio?",
                "type": "open_text",
                "options": None
            }
        
        # 6. Increment counter (ONLY if not OUTCOME and in clinical phases)
        if next_phase not in [TriagePhase.OUTCOME, TriagePhase.SBAR_GENERATION]:
            self.state.set("phase_question_count", phase_q_count + 1)
        
        # 7. Save to Supabase
        session_id = self.state.get(StateKeys.SESSION_ID, "unknown")
        processing_time = int((time.time() - start_time) * 1000)
        
        self.db.save_interaction(
            session_id=session_id,
            user_input=user_input,
            assistant_response=response.get("text", "N/A"),
            processing_time_ms=processing_time,
            session_state={
                "branch": current_branch.value,
                "phase": next_phase.value,
                "phase_q_count": phase_q_count + 1,
                "collected": collected
            },
            metadata={}
        )
        
        # 8. Return
        return {
            "assistant_response": response["text"],
            "question_type": response.get("type", "open_text"),
            "options": response.get("options"),
            "metadata": response.get("metadata", {}),
            "processing_time_ms": processing_time
        }
    
    def _classify_branch(self, user_input: str) -> TriageBranch:
        """
        Classify branch via keyword matching.
        Uses SmartRouter for robust classification.
        """
        from ..controllers.smart_router import SmartRouter
        
        path, metadata = SmartRouter.route(user_input)
        logger.info(f"🎯 SmartRouter: path={path}, reason={metadata.get('reason', 'N/A')}")
        
        branch_map = {
            "A": TriageBranch.EMERGENCY,
            "B": TriageBranch.MENTAL_HEALTH,
            "C": TriageBranch.STANDARD,
            "INFO": TriageBranch.INFO,
        }
        return branch_map.get(path, TriageBranch.STANDARD)


# ============================================================================
# SINGLETON FACTORY
# ============================================================================

_controller_instance = None

def get_triage_controller():
    """Get singleton controller instance."""
    global _controller_instance
    if _controller_instance is None:
        _controller_instance = TriageControllerV3()
    return _controller_instance
