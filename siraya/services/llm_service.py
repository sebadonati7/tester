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
from typing import Dict, List, Any, Optional, Set
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
        # Try secrets first
        try:
            if "groq" in st.secrets and "api_key" in st.secrets["groq"]:
                groq_key = st.secrets["groq"]["api_key"]
                logger.info(f"✅ Groq key from secrets: {groq_key[:10]}...")
        except Exception as e:
            logger.warning(f"⚠️ Groq secrets error: {e}")
        
        # Fallback to environment
        if not groq_key:
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                logger.info(f"✅ Groq key from env: {groq_key[:10]}...")
        
        # Initialize Groq client
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
        
        # Import RAG service
        try:
            from .rag_service import get_rag_service
            rag = get_rag_service()
            
            # Use RAG only if necessary
            if rag.should_use_rag(phase, user_symptoms):
                logger.info("🧠 RAG activated for clinical query")
                protocol_docs = rag.retrieve_context(user_symptoms, k=5)
                protocol_context = rag.format_context_for_llm(protocol_docs, phase)
            else:
                logger.info("💬 RAG not needed (general conversation)")
                protocol_context = "ℹ️ Protocolli non necessari per questa fase conversazionale."
        except Exception as e:
            logger.warning(f"RAG retrieval failed: {e}")
            protocol_context = "⚠️ Protocolli clinici non disponibili. Procedi con linee guida generali."
        
        # Extract patient info
        age = context.get("patient_age", "N/D")
        sex = context.get("patient_sex", "N/D")
        location = context.get("patient_location", "N/D")
        percorso = context.get("percorso", "C")
        
        # Build hybrid prompt
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
3. Identifica la SPECIALIZZAZIONE necessaria (es. Cardiologia, Traumatologia, Oculistica)
4. Spiega brevemente il razionale clinico

REGOLE FERREE:
- NON fare diagnosi ("Hai l'infarto" ❌). Usa solo "Sospetto di..." o "Quadro compatibile con..."
- NON prescrivere farmaci
- NON indicare ospedali (lo farà il sistema automaticamente)
- Cita SEMPRE la fonte del protocollo usato (es. "[Manuale Lazio, pag. 42]")

=== OUTPUT RICHIESTO ===
Rispondi in questo formato JSON:
```json
{{
  "codice_colore": "ROSSO|GIALLO|VERDE|BIANCO",
  "specializzazione": "Nome specializzazione",
  "urgenza": 1-5,
  "ragionamento": "Breve spiegazione clinica con citazione protocollo",
  "red_flags": ["Lista eventuali sintomi allarmanti rilevati"]
}}
