"""
SIRAYA Health Navigator - LLM Service
V2.0: Full AI Orchestrator Migration - FIXED FOR STREAMLIT CLOUD

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
import os
from typing import Dict, List, Any, Optional, AsyncGenerator, Set
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from ..config.settings import EMERGENCY_RULES, ClinicalMappings, RAGConfig

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
        """Initialize API clients from Streamlit secrets with robust fallback."""
        groq_key = None
        gemini_key = None
        
        # === GROQ API KEY ===
        try:
            if "groq" in st.secrets and "api_key" in st.secrets["groq"]:
                groq_key = st.secrets["groq"]["api_key"]
                logger.info(f"✅ Groq key from secrets: {groq_key[:10]}...")
        except Exception as e:
            logger.warning(f"⚠️ Groq secrets error: {e}")
        
        if not groq_key:
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                logger.info(f"✅ Groq key from env: {groq_key[:10]}...")
        
        if groq_key:
            try:
                from groq import Groq
                self._groq_client = Groq(api_key=groq_key)
                logger.info("✅ Groq client initialized")
            except ImportError:
                logger.error("❌ groq library not installed")
            except Exception as e:
                logger.error(f"❌ Groq init error: {e}")
        else:
            logger.error("❌ GROQ_API_KEY not found")
        
        # === GEMINI API KEY ===
        try:
            if "gemini" in st.secrets and "api_key" in st.secrets["gemini"]:
                gemini_key = st.secrets["gemini"]["api_key"]
        except Exception as e:
            logger.warning(f"⚠️ Gemini secrets error: {e}")
        
        if not gemini_key:
            gemini_key = os.getenv("GEMINI_API_KEY")
        
        if gemini_key:
            try:
                import google.genai as genai
                genai.configure(api_key=gemini_key)
                self._gemini_model = genai.GenerativeModel("gemini-1.5-flash")
                logger.info("✅ Gemini configured")
            except Exception as e:
                logger.warning(f"⚠️ Gemini config error: {e}")
    
    def _build_system_prompt_with_rag(
        self,
        user_symptoms: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Build system prompt with RAG-retrieved clinical protocols.
        
        Args:
            user_symptoms: User-reported symptoms
            context: Current triage context (location, age, etc.)
            
        Returns:
            System prompt with embedded protocol context
        """
        protocol_context = ""
        phase = context.get("fase", "FASE_4_TRIAGE")
        
        try:
            from .rag_service import get_rag_service
            rag = get_rag_service()
            
            if rag.should_use_rag(phase, user_symptoms):
                logger.info("🧠 RAG activated")
                protocol_docs = rag.retrieve_context(user_symptoms, k=5)
                protocol_context = rag.format_context_for_llm(protocol_docs, phase)
            else:
                logger.info("💬 RAG not needed")
                protocol_context = "ℹ️ Protocolli non necessari per questa fase conversazionale."
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")
            protocol_context = "⚠️ Protocolli clinici non disponibili."
        
        age = context.get("patient_age", "N/D")
        sex = context.get("patient_sex", "N/D")
        location = context.get("patient_location", "N/D")
        percorso = context.get("percorso", "C")
        
        system_prompt = f"""Sei SIRAYA, un assistente medico di triage certificato per l'Emilia-Romagna.

{protocol_context}

=== CONTESTO PAZIENTE ===
Età: {age}
Sesso: {sex}
Località: {location}
Percorso: {percorso}

=== TUO COMPITO ===
1. Analizza i sintomi del paziente usando i protocolli sopra (se disponibili)
2. Determina il CODICE COLORE (Rosso/Giallo/Verde/Bianco)
3. Identifica la SPECIALIZZAZIONE necessaria
4. Spiega brevemente il razionale clinico

REGOLE FERREE:
- NON fare diagnosi. Usa solo "Sospetto di..." o "Quadro compatibile con..."
- NON prescrivere farmaci
- NON indicare ospedali (lo farà il sistema automaticamente)
- Cita la fonte del protocollo usato (se disponibile)

=== OUTPUT RICHIESTO ===
Rispondi in formato JSON:
```json
{{
  "codice_colore": "ROSSO|GIALLO|VERDE|BIANCO",
  "specializzazione": "Nome specializzazione",
  "urgenza": 1-5,
  "ragionamento": "Breve spiegazione clinica",
  "red_flags": ["Lista sintomi allarmanti"]
}}
            return system_prompt

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
    """Check for emergency keywords in message."""
    if not message:
        return None
    
    text_lower = message.lower().strip()
    
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
        def _get_mental_health_crisis_response(self) -> str:
    """Get response for mental health crisis."""
    return """🆘 **SUPPORTO IMMEDIATO**
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
# MAIN AI RESPONSE METHOD
# ========================================================================

def get_ai_response(
    self,
    user_input: str,
    context: Dict[str, Any]
) -> str:
    """
    Get AI response with RAG-augmented system prompt.
    
    Args:
        user_input: User message
        context: Triage context
        
    Returns:
        AI response text
    """
    try:
        system_prompt = self._build_system_prompt_with_rag(user_input, context)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
        
        response_text = ""
        
        if self._groq_client:
            try:
                response = self._groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1500
                )
                response_text = response.choices[0].message.content
                logger.info("✅ Groq response OK")
            except Exception as e:
                logger.error(f"❌ Groq API error: {e}")
        
        if not response_text and self._gemini_model:
            try:
                prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
                response = self._gemini_model.generate_content(prompt)
                response_text = response.text
                logger.info("✅ Gemini response OK")
            except Exception as e:
                logger.error(f"❌ Gemini API error: {e}")
        
        if not response_text:
            logger.error("❌ Both LLM providers failed")
            return "❌ Servizio AI non disponibile. Riprova più tardi.\n\n💡 Verifica che le chiavi API siano configurate in st.secrets."
        
        sanitized = DiagnosisSanitizer.sanitize(response_text)
        
        return sanitized
        
    except Exception as e:
        logger.error(f"❌ LLM error: {e}")
        return f"⚠️ Errore nella generazione della risposta: {str(e)}"

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
