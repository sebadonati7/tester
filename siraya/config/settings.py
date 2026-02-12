"""
SIRAYA Health Navigator - Configuration Settings
V2.0: Supabase Integration + Full Constant Migration

This module centralizes all configuration:
- Supabase connection settings
- API keys management
- Triage phases and rules
- Emergency keywords
- Clinical mappings
"""

import os
import streamlit as st
from pathlib import Path
from typing import Dict, Any, List, Set
from dataclasses import dataclass


# ============================================================================
# PATH RESOLUTION
# ============================================================================

# Base directory for siraya package
BASE_DIR = Path(__file__).resolve().parent.parent

# Data directory
DATA_DIR = BASE_DIR / "data"


# ============================================================================
# PATHS CONFIGURATION
# ============================================================================

@dataclass
class PATHS:
    """File paths configuration."""
    BASE: Path = BASE_DIR
    DATA: Path = DATA_DIR
    STYLES_CSS: Path = BASE_DIR / "config" / "styles.css"
    MASTER_KB: Path = DATA_DIR / "master_kb.json"
    DISTRICTS: Path = DATA_DIR / "distretti_sanitari_er.json"
    MAP_DATA: Path = DATA_DIR / "mappa_er.json"


# ============================================================================
# SUPABASE CONFIGURATION
# ============================================================================

class SupabaseConfig:
    """
    Supabase connection settings.

    Reads from st.secrets (nested [supabase] or flat) or environment variables.
    """
    # Table names
    TABLE_LOGS: str = "triage_logs"
    TABLE_FACILITIES: str = "facilities"
    TABLE_SESSIONS: str = "sessions"

    @staticmethod
    def get_url() -> str:
        """Get Supabase URL — tries [supabase].url → flat SUPABASE_URL → env."""
        try:
            return st.secrets["supabase"]["url"]
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            return st.secrets["SUPABASE_URL"]
        except (KeyError, TypeError, AttributeError):
            pass
        return os.environ.get("SUPABASE_URL", "")

    @staticmethod
    def get_key() -> str:
        """Get Supabase key — tries [supabase].key → flat SUPABASE_KEY → env."""
        try:
            return st.secrets["supabase"]["key"]
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            return st.secrets["SUPABASE_KEY"]
        except (KeyError, TypeError, AttributeError):
            pass
        return os.environ.get("SUPABASE_KEY", "")

    @staticmethod
    def is_configured() -> bool:
        """Check if Supabase is properly configured."""
        return bool(SupabaseConfig.get_url() and SupabaseConfig.get_key())


# ============================================================================
# API CONFIGURATION
# ============================================================================

class APIConfig:
    """
    API keys and model configuration.

    Supports both nested secrets ([groq].api_key) and flat (GROQ_API_KEY).
    """
    # Model names
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Temperature for generation
    TEMPERATURE: float = 0.1

    # Timeout settings
    API_TIMEOUT_SECONDS: int = 60

    @staticmethod
    def get_groq_key() -> str:
        """Get Groq API key — tries [groq].api_key → flat GROQ_API_KEY → env."""
        try:
            return st.secrets["groq"]["api_key"]
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            return st.secrets["GROQ_API_KEY"]
        except (KeyError, TypeError, AttributeError):
            pass
        return os.environ.get("GROQ_API_KEY", "")

    @staticmethod
    def get_gemini_key() -> str:
        """Get Gemini API key — tries [gemini].api_key → flat GEMINI_API_KEY → env."""
        try:
            return st.secrets["gemini"]["api_key"]
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            return st.secrets["GEMINI_API_KEY"]
        except (KeyError, TypeError, AttributeError):
            pass
        return os.environ.get("GEMINI_API_KEY", "")


# ============================================================================
# UI CONFIGURATION
# ============================================================================

class Settings:
    """Application settings."""
    APP_TITLE: str = "SIRAYA Health Navigator"
    APP_ICON: str = "🩺"
    PAGE_LAYOUT: str = "wide"
    INITIAL_SIDEBAR_STATE: str = "expanded"


class UITheme:
    """UI theme colors and styling."""
    PRIMARY_COLOR: str = "#4A90E2"
    SECONDARY_COLOR: str = "#f0f4f8"
    ERROR_COLOR: str = "#dc2626"
    SUCCESS_COLOR: str = "#16a34a"
    WARNING_COLOR: str = "#d97706"


# ============================================================================
# RAG CONFIGURATION
# ============================================================================

class RAGConfig:
    """
    RAG (Retrieval-Augmented Generation) configuration.
    
    Controls how clinical protocols are indexed and retrieved.
    """
    # Vector store settings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHROMA_PERSIST_DIR: Path = DATA_DIR / "chroma_db"
    
    # Chunking settings
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    
    # Retrieval settings
    TOP_K_CHUNKS: int = 5
    MAX_CONTEXT_LENGTH: int = 4000
    
    # Protocol files mapping
    PROTOCOLS_DIR: Path = DATA_DIR / "protocols"
    
    PROTOCOL_PRIORITIES = {
        "Manuale-Triage-Lazio.pdf": 1,          # Primary source
        "Sistema-Dispatch-Toscana.pdf": 2,      # Emergency dispatch
        "Linee-Guida-Piemonte.pdf": 3,          # Regional guidelines
        "WAST_ViolenzaDomestica.pdf": 10,       # Special cases
        "ASQ_AbuseSostanze.pdf": 10,            # Special cases
        "18A0052000100030110001.pdf": 99,       # Legal/normative
    }


# ============================================================================
# TRIAGE CONFIGURATION
# ============================================================================

class TriageConfig:
    """Triage protocol configuration."""
    
    # Phases in order
    PHASES: List[Dict[str, str]] = [
        {"id": "INTENT_DETECTION", "name": "Rilevazione Intento", "icon": "🎯"},
        {"id": "LOCATION", "name": "Localizzazione", "icon": "📍"},
        {"id": "CHIEF_COMPLAINT", "name": "Sintomo Principale", "icon": "🔍"},
        {"id": "PAIN_ASSESSMENT", "name": "Valutazione Dolore", "icon": "📊"},
        {"id": "RED_FLAGS", "name": "Red Flags", "icon": "🚨"},
        {"id": "DEMOGRAPHICS", "name": "Anagrafica", "icon": "👤"},
        {"id": "ANAMNESIS", "name": "Anamnesi", "icon": "📋"},
        {"id": "DISPOSITION", "name": "Disposizione", "icon": "🏥"},
    ]
    
    # Phase IDs for quick access
    PHASE_IDS: List[str] = [p["id"] for p in PHASES]
    
    # Fallback options for each phase
    FALLBACK_OPTIONS: Dict[str, List[str]] = {
        "LOCATION": ["Bologna", "Modena", "Parma", "Reggio Emilia", "Ferrara", 
                     "Ravenna", "Rimini", "Altro comune ER"],
        "CHIEF_COMPLAINT": ["Dolore", "Febbre", "Trauma/Caduta", 
                           "Difficoltà respiratorie", "Problemi gastrointestinali", "Altro"],
        "PAIN_SCALE": ["1-3 (Lieve)", "4-6 (Moderato)", "7-8 (Forte)", 
                       "9-10 (Insopportabile)", "Nessun dolore"],
        "RED_FLAGS": ["Sì, ho sintomi gravi", "No, nessun sintomo preoccupante", 
                      "Non sono sicuro/a"],
        "ANAMNESIS": ["Fornisco informazioni", "Preferisco non rispondere", 
                      "Non applicabile"],
    }


# ============================================================================
# EMERGENCY RULES
# ============================================================================

class EMERGENCY_RULES:
    """Emergency detection keywords and rules."""
    
    # Critical red flags requiring immediate 118
    CRITICAL_RED_FLAGS: List[str] = [
        "dolore toracico", "dolore petto", "oppressione torace",
        "non riesco respirare", "non riesco a respirare", "soffoco",
        "difficoltà respiratoria grave",
        "perdita di coscienza", "svenuto", "svenimento",
        "convulsioni", "crisi convulsiva",
        "emorragia massiva", "sangue abbondante",
        "paralisi", "metà corpo bloccata"
    ]
    
    # High-priority red flags (Path A fast-track)
    HIGH_RED_FLAGS: List[str] = [
        "febbre alta", "febbre 39", "febbre 40",
        "trauma cranico", "battuto forte testa",
        "vomito continuo", "vomito sangue",
        "dolore addominale acuto", "dolore pancia molto forte"
    ]
    
    # Mental health crisis keywords
    MENTAL_HEALTH_CRISIS: List[str] = [
        "suicidio", "uccidermi", "togliermi la vita", "farla finita",
        "ammazzarmi", "voglio morire", "non voglio più vivere",
        "autolesionismo", "tagliarmi", "farmi male"
    ]
    
    # Mental health keywords (Path B)
    MENTAL_HEALTH_KEYWORDS: List[str] = [
        "ansia", "ansioso", "ansiosa", "attacco di panico", "panico",
        "depressione", "depresso", "depressa", "triste", "tristezza",
        "stress", "burn out", "burnout", "esaurimento",
        "non ce la faccio più"
    ]
    
    # Informational query keywords (non-triage)
    INFO_KEYWORDS: List[str] = [
        "orari", "orario", "quando apre", "quando chiude",
        "farmacia", "farmacie di turno",
        "dove trovo", "dov'è", "come arrivo",
        "come funziona", "cos'è", "cosa fa",
        "prenot", "appuntamento",
        "numero", "telefono", "contatto"
    ]


# ============================================================================
# CLINICAL MAPPINGS
# ============================================================================

class ClinicalMappings:
    """Clinical terminology and mappings."""
    
    # Red flags for NLP detection
    RED_FLAGS_KEYWORDS: List[str] = [
        "svenimento", "sangue", "confusione", "petto", "respiro",
        "paralisi", "convulsioni", "coscienza", "dolore torace",
        "emorragia", "trauma cranico", "infarto", "ictus"
    ]
    
    # Common symptoms for detection
    SINTOMI_COMUNI: List[str] = [
        "febbre", "tosse", "mal di testa", "nausea", "dolore addominale",
        "vertigini", "debolezza", "affanno", "palpitazioni", "diarrea",
        "vomito", "mal di gola", "dolore articolare", "eruzioni cutanee",
        "gonfiore", "bruciore", "prurito", "stanchezza"
    ]
    
    # Specializations
    SPECIALIZZAZIONI: List[str] = [
        "Cardiologia", "Neurologia", "Ortopedia", "Gastroenterologia",
        "Pediatria", "Ginecologia", "Dermatologia", "Psichiatria",
        "Otorinolaringoiatria", "Oftalmologia", "Generale"
    ]
    
    # Symptom normalization mapping
    CANONICAL_KB: Dict[str, str] = {
        # Cefalea
        "mal di testa": "Cefalea",
        "mal testa": "Cefalea",
        "testa che fa male": "Cefalea",
        "dolore testa": "Cefalea",
        "emicrania": "Cefalea",
        
        # Dolore addominale
        "mal di pancia": "Dolore addominale",
        "dolore pancia": "Dolore addominale",
        "dolore stomaco": "Dolore addominale",
        "mal di stomaco": "Dolore addominale",
        
        # Dolore toracico
        "dolore petto": "Dolore toracico",
        "dolore al petto": "Dolore toracico",
        "dolore cuore": "Dolore toracico",
        "oppressione petto": "Dolore toracico",
        
        # Dispnea
        "difficoltà respirare": "Dispnea",
        "non riesco respirare": "Dispnea grave",
        "soffoco": "Dispnea grave",
        "affanno": "Dispnea",
        "fiato corto": "Dispnea",
        
        # Febbre
        "febbre": "Febbre",
        "temperatura alta": "Febbre",
        "ho la febbre": "Febbre",
        
        # Trauma
        "caduta": "Trauma",
        "sono caduto": "Trauma",
        "botta": "Trauma",
        "incidente": "Trauma",
        
        # Vertigini
        "vertigini": "Vertigini",
        "capogiro": "Vertigini",
        "giramento testa": "Vertigini",
        
        # Nausea/Vomito
        "nausea": "Nausea",
        "vomito": "Vomito",
        "ho vomitato": "Vomito",
        
        # Mental health
        "ansia": "Ansia",
        "ansioso": "Ansia",
        "attacco panico": "Attacco di panico",
        "depressione": "Depressione",
        "stress": "Stress",
    }
    
    # Stop words for symptom preprocessing
    STOP_WORDS: Set[str] = {
        "ho", "hai", "ha", "un", "una", "il", "la", "lo", "di", "da", "in",
        "per", "con", "su", "a", "che", "mi", "ti", "si", "al", "alla",
        "del", "della", "delle", "dei", "degli", "molto", "tanto", "poco"
    }


# ============================================================================
# HAVERSINE DISTANCE CALCULATION
# ============================================================================

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate great-circle distance between two points.
    
    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates
        
    Returns:
        Distance in kilometers
    """
    import math
    
    R = 6371  # Earth's radius in km
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c
class UI_THEME:
    """Configurazione tema dell'interfaccia utente - Medical Blue Palette"""
    
    # Colori primari e secondari
    PRIMARY = "#0E4E8E"      # Blu Istituzionale
    SECONDARY = "#4DA6FF"    # Azzurro Chiaro
    
    # Colori base
    BACKGROUND = "#FFFFFF"   # Bianco
    TEXT = "#262730"         # Grigio Scuro per leggibilità
    
    # Colori di stato
    SUCCESS = "#28a745"      # Verde conferma
    WARNING = "#ffc107"      # Giallo alert
    ERROR = "#dc3545"        # Rosso errore
    INFO = "#17a2b8"         # Ciano info
