"""
SIRAYA Health Navigator - LLM Service
V2.0: Full AI Orchestrator Migration

This service:
- Wraps Groq and Gemini API calls
- Manages triage prompts and paths (A/B/C)
- Handles symptom normalization
- Provides emergency detection
- Returns structured responses
"""

import re
import json
import logging
import asyncio
import difflib
from typing import Dict, List, Any, Optional, AsyncGenerator, Set
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from ..config.settings import APIConfig, EMERGENCY_RULES, ClinicalMappings

logger = logging.getLogger(__name__)


# ============================================================================
# SYMPTOM NORMALIZER
# ============================================================================

class SymptomNormalizer:
    """
    Normalizes symptom descriptions to canonical medical terms.
    
    Uses fuzzy matching for typo tolerance.
    """
    
    def __init__(
        self,
        canonical_kb: Optional[Dict[str, str]] = None,
        fuzzy_threshold: float = 0.85
    ):
        self.canonical_kb = canonical_kb or ClinicalMappings.CANONICAL_KB
        self.fuzzy_threshold = fuzzy_threshold
        self.unknown_terms: Set[str] = set()
    
    def _preprocess(self, text: str) -> str:
        """Preprocess text for normalization."""
        if not text:
            return ""
        
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', ' ', text)
        
        words = text.split()
        words = [w for w in words if w not in ClinicalMappings.STOP_WORDS]
        text = ' '.join(words)
        
        return re.sub(r'\s+', ' ', text).strip()
    
    def normalize(self, symptom: str) -> str:
        """
        Normalize symptom to canonical medical term.
        
        Args:
            symptom: User-reported symptom
            
        Returns:
            Canonical term or original if no match
        """
        if not symptom or not isinstance(symptom, str):
            return ""
        
        original = symptom
        cleaned = self._preprocess(symptom)
        
        if not cleaned:
            return original
        
        # Exact match
        if cleaned in self.canonical_kb:
            return self.canonical_kb[cleaned]
        
        # Fuzzy match
        keys = list(self.canonical_kb.keys())
        matches = difflib.get_close_matches(cleaned, keys, n=1, cutoff=self.fuzzy_threshold)
        
        if matches:
            return self.canonical_kb[matches[0]]
        
        self.unknown_terms.add(original)
        return original


# ============================================================================
# DIAGNOSIS SANITIZER
# ============================================================================

class DiagnosisSanitizer:
    """Blocks unauthorized diagnoses and prescriptions."""
    
    FORBIDDEN_PATTERNS = [
        r"\bdiagnosi\b",
        r"\bprescrivo\b",
        r"\bterapia\b",
        r"\bhai\s+(la|il|un[\'a]? )\s+\w+",
        r"\bè\s+(sicuramente|probabilmente)\b",
        r"\bprendi\s+\w+\s+mg\b",
        r"\b(hai|sembra che tu abbia|potresti avere)\s+.*\b(infiammazione|infezione|patologia|malattia)\b"
    ]
    
    @staticmethod
    def sanitize(response_text: str) -> str:
        """
        Sanitize response text, blocking diagnoses.
        
        Args:
            response_text: AI response text
            
        Returns:
            Sanitized text
        """
        text_lower = response_text.lower()
        
        for pattern in DiagnosisSanitizer.FORBIDDEN_PATTERNS:
            if re.search(pattern, text_lower):
                logger.critical(f"DIAGNOSIS BLOCKED: {response_text[:100]}...")
                return (
                    "In base ai dati raccolti, la situazione merita un approfondimento clinico. "
                    "Potresti descrivermi meglio da quanto tempo avverti questi sintomi?"
                )
        
        return response_text


# ============================================================================
# LLM SERVICE CLASS
# ============================================================================

class LLMService:
    """
    Service layer for LLM interactions.
    
    Features:
    - Groq/Gemini fallback chain
    - Path A/B/C prompt routing
    - Emergency detection
    - Symptom normalization
    - Structured response handling
    """
    
    def __init__(self):
        """Initialize LLM service with API clients."""
        self._groq_client = None
        self._gemini_model = None
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._symptom_normalizer = SymptomNormalizer()
        self._prompts = self._load_prompts()
        
        self._init_clients()
    
    def _init_clients(self) -> None:
        """Initialize API clients from settings."""
        groq_key = APIConfig.get_groq_key()
        gemini_key = APIConfig.get_gemini_key()
        
        if groq_key:
            try:
                from groq import AsyncGroq
                self._groq_client = AsyncGroq(api_key=groq_key)
                logger.info("Groq client initialized")
            except ImportError:
                logger.warning("groq library not installed")
            except Exception as e:
                logger.error(f"Groq init error: {e}")
        
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                self._gemini_model = genai.GenerativeModel(APIConfig.GEMINI_MODEL)
                logger.info("Gemini model initialized")
            except ImportError:
                logger.warning("google.generativeai not installed")
            except Exception as e:
                logger.error(f"Gemini init error: {e}")
    
    def _load_prompts(self) -> Dict[str, str]:
        """Load triage prompt templates."""
        return {
            "base_rules": (
                "Sei l'AI Health Navigator (SIRAYA). NON SEI UN MEDICO.\n"
                "- SINGLE QUESTION POLICY: Poni una sola domanda alla volta.\n"
                "- NO DIAGNOSI: Non fornire diagnosi né ordini.\n"
                "- SLOT FILLING: Estrai dati (età, luogo, sintomi) dai messaggi liberi.\n"
                "- FORMATO OPZIONI: Usa sempre opzioni A, B, C per guidare l'utente."
            ),
            "percorso_a": (
                "EMERGENZA (SOSPETTO RED/ORANGE):\n"
                "1. SETUP: Localizzazione Immediata (Salta se nota).\n"
                "2. INDAGINE CLINICA (FAST-TRIAGE): \n"
                "   - VINCOLO: Esegui ALMENO 3 domande rapide specifiche sul sintomo.\n"
                "3. ESITO: Se confermato, consiglia PS, link affollamento e report SBAR."
            ),
            "percorso_b": (
                "SALUTE MENTALE (SOSPETTO BLACK):\n"
                "1. CONSENSO: Richiedi autorizzazione per domande personali.\n"
                "2. INDAGINE CLINICA (VALUTAZIONE RISCHIO):\n"
                "   - Valuta percorsi seguiti, farmaci e rischio immediato.\n"
                "3. ESITO: Se emergenza, 118 e hotline. Se supporto territoriale, routing CSM/NPIA."
            ),
            "percorso_c": (
                "STANDARD (GREEN/YELLOW):\n"
                "1. ANAMNESI BASE: Età, Sesso, Gravidanza, Farmaci (Una alla volta).\n"
                "2. INDAGINE CLINICA (INDAGINE ADATTIVA):\n"
                "   - VINCOLO: Esegui tra 5 e 7 domande di approfondimento clinico.\n"
                "3. ESITO: Routing gerarchico (Specialistica -> CAU -> MMG) e report SBAR."
            ),
            "disposition_prompt": (
                "FASE SBAR (HANDOVER):\n"
                "Genera il riassunto strutturato obbligatorio:\n"
                "S (Situation): Sintomo e intensità.\n"
                "B (Background): Età, sesso, farmaci.\n"
                "A (Assessment): Red Flags escluse e risposte chiave.\n"
                "R (Recommendation): Struttura suggerita e motivo."
            ),
        }
    
    def is_available(self) -> bool:
        """Check if at least one LLM is available."""
        return bool(self._groq_client or self._gemini_model)
    
    # ========================================================================
    # EMERGENCY DETECTION
    # ========================================================================
    
    def check_emergency(self, message: str) -> Optional[Dict]:
        """
        Check for emergency keywords in message.
        
        Args:
            message: User message
            
        Returns:
            Emergency response dict or None
        """
        if not message:
            return None
        
        text_lower = message.lower().strip()
        
        # Critical red flags (118 immediate)
        for keyword in EMERGENCY_RULES.CRITICAL_RED_FLAGS:
            if keyword in text_lower:
                logger.critical(f"CRITICAL RED FLAG: {keyword}")
                return {
                    "text": self.get_emergency_response(keyword),
                    "urgency": 5,
                    "type": "CRITICAL",
                    "keyword": keyword,
                    "call_118": True
                }
        
        # Mental health crisis
        for keyword in EMERGENCY_RULES.MENTAL_HEALTH_CRISIS:
            if keyword in text_lower:
                logger.critical(f"MENTAL HEALTH CRISIS: {keyword}")
                return {
                    "text": self._get_mental_health_crisis_response(),
                    "urgency": 5,
                    "type": "BLACK",
                    "keyword": keyword,
                    "call_118": True
                }
        
        return None
    
    def get_emergency_response(self, symptom: str) -> str:
        """Get canned response for emergency situations."""
        return f"""🚨 **EMERGENZA RILEVATA**

Ho rilevato un possibile sintomo critico: **{symptom}**

**Azione consigliata:**
1. Chiama immediatamente il **118**
2. Se sei solo/a, cerca di metterti in sicurezza
3. Se possibile, fatti assistere da qualcuno

Il 118 valuterà la tua situazione e invierà i soccorsi appropriati.

_Non sottovalutare questi sintomi - è sempre meglio verificare._
"""
    
    def _get_mental_health_crisis_response(self) -> str:
        """Get response for mental health crisis."""
        return """🆘 **SUPPORTO IMMEDIATO**

Capisco che stai attraversando un momento molto difficile.

**Numeri utili:**
- **118** - Emergenza sanitaria
- **1522** - Antiviolenza e stalking
- **02 2327 2327** - Telefono Amico (24h)

Non sei solo/a. Ci sono persone pronte ad aiutarti in questo momento.

_Se sei in pericolo immediato, chiama il 118._
"""
    
    def get_fallback_response(self, phase: str) -> str:
        """Get fallback response when AI fails."""
        fallbacks = {
            "LOCATION": "In quale comune dell'Emilia-Romagna ti trovi?",
            "CHIEF_COMPLAINT": "Qual è il sintomo principale che ti preoccupa?",
            "PAIN_ASSESSMENT": "Su una scala da 1 a 10, quanto è intenso il dolore?",
            "RED_FLAGS": "Hai difficoltà a respirare, dolore al petto, o altri sintomi preoccupanti?",
            "DEMOGRAPHICS": "Potresti dirmi la tua età?",
            "ANAMNESIS": "Stai assumendo farmaci particolari?",
            "DISPOSITION": "In base ai dati raccolti, ti consiglio di consultare un professionista sanitario.",
        }
        
        return fallbacks.get(phase, "Puoi descrivermi meglio la tua situazione?")
    
    # ========================================================================
    # MESSAGE PROCESSING
    # ========================================================================
    
    def process_message(
        self,
        user_message: str,
        session_id: str = "",
        collected_data: Optional[Dict] = None,
        current_phase: str = "INIT",
        triage_path: str = "C"
    ) -> tuple:
        """
        Process user message and generate AI response.
        
        Args:
            user_message: User's input
            session_id: Session identifier
            collected_data: Previously collected triage data
            current_phase: Current triage phase
            triage_path: Current triage path (A/B/C)
            
        Returns:
            Tuple of (response_text, metadata_dict)
        """
        if collected_data is None:
            collected_data = st.session_state.get("collected_data", {})
        
        metadata = {
            "urgenza": 3,
            "area": "Generale",
            "confidence": 0.8,
            "dati_estratti": {},
            "opzioni": None,
            "fase_corrente": current_phase
        }
        
        # Check for emergency
        emergency = self.check_emergency(user_message)
        if emergency:
            metadata["urgenza"] = emergency.get("urgency", 5)
            metadata["is_emergency"] = True
            metadata["emergency_type"] = emergency.get("type", "CRITICAL")
            return emergency["text"], metadata
        
        # Determine path
        path = self._determine_path(user_message, collected_data)
        
        # Build messages
        messages = st.session_state.get("messages", [])
        api_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        api_messages.append({"role": "user", "content": user_message})
        
        # Call AI
        try:
            response, ai_metadata = self._call_ai_sync_with_metadata(
                api_messages, path, current_phase, collected_data
            )
            
            # Sanitize
            response = DiagnosisSanitizer.sanitize(response)
            
            # Merge metadata
            metadata.update(ai_metadata)
            
            return response, metadata
            
        except Exception as e:
            logger.error(f"LLM processing error: {e}")
            metadata["fallback_used"] = True
            return self.get_fallback_response(current_phase), metadata
    
    def _determine_path(self, message: str, collected_data: Dict) -> str:
        """Determine triage path based on message and data."""
        text_lower = message.lower()
        
        # Path A: Emergency
        for keyword in EMERGENCY_RULES.HIGH_RED_FLAGS:
            if keyword in text_lower:
                return "A"
        
        if collected_data.get("RED_FLAGS"):
            return "A"
        
        # Path B: Mental health
        for keyword in EMERGENCY_RULES.MENTAL_HEALTH_KEYWORDS:
            if keyword in text_lower:
                return "B"
        
        # Default: Standard
        return "C"
    
    def _call_ai_sync_with_metadata(
        self,
        messages: List[Dict],
        path: str,
        phase: str,
        collected_data: Dict
    ) -> tuple:
        """
        Synchronous AI call with Groq/Gemini fallback.
        Returns both text and extracted metadata.
        
        Args:
            messages: Conversation messages
            path: Triage path (A/B/C)
            phase: Current phase
            collected_data: Collected data
            
        Returns:
            Tuple of (response_text, metadata_dict)
        """
        system_msg = self._build_system_prompt(path, phase, collected_data)
        api_messages = [{"role": "system", "content": system_msg}] + messages[-5:]
        
        response_text = ""
        metadata = {"fallback_used": False}
        
        # Try Groq first
        if self._groq_client:
            try:
                response_text = self._call_groq_sync(api_messages)
                if response_text:
                    text, meta = self._extract_response_with_metadata(response_text)
                    return text, meta
            except Exception as e:
                logger.error(f"Groq error: {e}")
        
        # Fallback to Gemini
        if self._gemini_model:
            try:
                response_text = self._call_gemini_sync(api_messages)
                if response_text:
                    text, meta = self._extract_response_with_metadata(response_text)
                    return text, meta
            except Exception as e:
                logger.error(f"Gemini error: {e}")
        
        metadata["fallback_used"] = True
        return self.get_fallback_response(phase), metadata
    
    def _call_groq_sync(self, messages: List[Dict]) -> str:
        """Synchronous Groq API call."""
        async def _async_call():
            response = await asyncio.wait_for(
                self._groq_client.chat.completions.create(
                    model=APIConfig.GROQ_MODEL,
                    messages=messages,
                    temperature=APIConfig.TEMPERATURE,
                    response_format={"type": "json_object"}
                ),
                timeout=APIConfig.API_TIMEOUT_SECONDS
            )
            return response.choices[0].message.content
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _async_call())
                return future.result(timeout=90)
        else:
            return loop.run_until_complete(_async_call())
    
    def _call_gemini_sync(self, messages: List[Dict]) -> str:
        """Synchronous Gemini API call."""
        prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        response = self._gemini_model.generate_content(prompt)
        return response.text
    
    def _extract_response_text(self, response: str) -> str:
        """Extract response text from JSON or raw response."""
        try:
            # Try to parse as JSON
            clean_json = re.sub(r"```json\n?|```", "", response).strip()
            data = json.loads(clean_json)
            return data.get("testo", response)
        except json.JSONDecodeError:
            return response
    
    def _extract_response_with_metadata(self, response: str) -> tuple:
        """
        Extract response text and metadata from JSON response.
        
        Returns:
            Tuple of (text, metadata_dict)
        """
        metadata = {}
        
        try:
            # Try to parse as JSON
            clean_json = re.sub(r"```json\n?|```", "", response).strip()
            data = json.loads(clean_json)
            
            text = data.get("testo", response)
            
            # Extract structured metadata
            metadata = {
                "opzioni": data.get("opzioni"),
                "tipo_domanda": data.get("tipo_domanda"),
                "fase_corrente": data.get("fase_corrente"),
                "dati_estratti": data.get("dati_estratti", {}),
            }
            
            # Extract metadata section if present
            if "metadata" in data and isinstance(data["metadata"], dict):
                metadata.update(data["metadata"])
            
            return text, metadata
            
        except json.JSONDecodeError:
            return response, metadata
    
    def _build_system_prompt(
        self,
        path: str,
        phase: str,
        collected_data: Dict
    ) -> str:
        """Build system prompt based on path and phase."""
        context = self._build_context_section(collected_data)
        path_instruction = self._prompts.get(f"percorso_{path.lower()}", self._prompts["percorso_c"])
        
        if phase == "DISPOSITION":
            path_instruction = self._prompts["disposition_prompt"]
        
        return f"""
{self._prompts['base_rules']}

CONTESTO (NON CHIEDERE NUOVAMENTE):
{context}

DIRETTIVE: {path_instruction}
FASE: {phase} | PERCORSO: {path}

FORMATO RISPOSTA JSON:
{{
    "testo": "risposta per l'utente",
    "tipo_domanda": "survey|scale|text|confirmation",
    "opzioni": ["Opzione A", "Opzione B", "Opzione C"],
    "fase_corrente": "{phase}",
    "dati_estratti": {{}},
    "metadata": {{ "urgenza": 3, "area": "Generale", "confidence": 0.8 }}
}}
"""
    
    def _build_context_section(self, collected_data: Dict) -> str:
        """Build context section from collected data."""
        if not collected_data:
            return "DATI RACCOLTI: Nessuno\n\nINIZIA LA RACCOLTA DATI."
        
        slots = []
        
        if collected_data.get("LOCATION"):
            slots.append(f"Comune: {collected_data['LOCATION']}")
        if collected_data.get("CHIEF_COMPLAINT"):
            slots.append(f"Sintomo: {collected_data['CHIEF_COMPLAINT']}")
        if collected_data.get("PAIN_SCALE"):
            slots.append(f"Dolore: {collected_data['PAIN_SCALE']}/10")
        if collected_data.get("age"):
            slots.append(f"Età: {collected_data['age']} anni")
        if collected_data.get("sex"):
            slots.append(f"Sesso: {collected_data['sex']}")
        
        return f"DATI GIÀ RACCOLTI:\n" + "\n".join(slots) if slots else "DATI RACCOLTI: Nessuno"


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """Get singleton LLM service instance."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
