"""
SIRAYA Health Navigator - Session Storage & Supabase Integration
V5.0: Zero-File Policy - Full migration to Supabase
Clean Slate: Only functions used by frontend.py
"""

import os
import json
import uuid
import streamlit as st
from typing import Any, Dict, List, Optional
from datetime import datetime

# ============================================================================
# SUPABASE INTEGRATION (V4.0 - Zero-File Policy)
# ============================================================================

@st.cache_resource
def init_supabase():
    """
    Inizializza connessione Supabase con connection pooling.
    Usa st.cache_resource per garantire una singola istanza per sessione.
    
    Returns:
        Supabase client o None se fallisce
    """
    try:
        from supabase import create_client, Client
        
        # Leggi credenziali da st.secrets
        url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
        key = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY"))
        
        if not url or not key:
            # Silent warning - non usare st.warning qui (causa errori in import)
            print("⚠️ Credenziali Supabase non trovate. Logging disabilitato.")
            return None
        
        client: Client = create_client(url, key)
        print("✅ Connessione Supabase attiva")
        return client
        
    except ImportError:
        print("❌ Libreria supabase non installata. Esegui: pip install supabase")
        return None
    except Exception as e:
        print(f"❌ Errore connessione Supabase: {e}")
        return None


class SupabaseLogger:
    """
    Logger centralizzato per interazioni chatbot su Supabase.
    Zero-File Policy: Tutti i log vengono scritti nel database.
    """
    
    def __init__(self):
        self.client = init_supabase()
        self.table_name = "triage_logs"
    
    def log_interaction(
        self,
        session_id: str,
        user_input: str,
        bot_response: str,
        metadata: Dict[str, Any],
        duration_ms: int = 0
    ) -> bool:
        """
        Salva interazione chatbot su Supabase con schema SQL completo.
        
        Args:
            session_id: ID univoco sessione
            user_input: Messaggio utente
            bot_response: Risposta bot
            metadata: Metadati aggiuntivi (triage_step, urgency_code, etc.)
            duration_ms: Durata risposta AI in millisecondi
        
        Returns:
            bool: True se salvato con successo
        """
        if not self.client:
            # Fail silently - non crashare mai la chat
            return False
        
        try:
            # Estrazione sicura con defaults schema-compliant
            payload = {
                # Core fields
                "session_id": session_id,
                "created_at": datetime.utcnow().isoformat(),
                "user_input": user_input,
                "bot_response": bot_response,
                
                # Clinical KPI
                "detected_intent": metadata.get('intent', metadata.get('detected_intent', 'triage')),
                "triage_code": metadata.get('triage_code') or metadata.get('codice_urgenza') or metadata.get('urgency_code', 'N/D'),
                "medical_specialty": metadata.get('medical_specialty') or metadata.get('specialization', 'Generale'),
                "suggested_facility_type": metadata.get('suggested_facility_type') or metadata.get('destinazione', 'N/D'),
                "reasoning": metadata.get('reasoning', ''),
                "estimated_wait_time": metadata.get('wait_time', metadata.get('estimated_wait_time', '')),
                
                # Technical KPI
                "processing_time_ms": duration_ms,
                "model_version": metadata.get('model', metadata.get('model_version', 'v2.0')),
                "tokens_used": metadata.get('tokens', metadata.get('tokens_used', 0)),
                "client_ip": metadata.get('client_ip', ''),
                
                # Metadata dump (full JSON)
                "metadata": json.dumps(metadata, ensure_ascii=False)
            }
            
            # Insert con gestione errori
            response = self.client.table(self.table_name).insert(payload).execute()
            
            # Verifica successo
            if response.data:
                return True
            else:
                # Silent warning - non usare st.warning qui per evitare problemi di contesto
                print(f"⚠️ Log non salvato: {response}")
                return False
                
        except Exception as e:
            # Fail silently - logging non deve mai bloccare la chat
            print(f"⚠️ Errore logging Supabase: {e}")
            return False
    
    def get_recent_logs(self, limit: int = 50, session_id: Optional[str] = None) -> List[Dict]:
        """
        Recupera log recenti da Supabase.
        
        Args:
            limit: Numero massimo di record
            session_id: Filtra per session_id specifico (opzionale)
        
        Returns:
            Lista di record log
        """
        if not self.client:
            return []
        
        try:
            query = self.client.table(self.table_name).select("*")
            
            if session_id:
                query = query.eq("session_id", session_id)
            
            response = query.order("created_at", desc=True).limit(limit).execute()
            
            return response.data if response.data else []
            
        except Exception as e:
            # Silent error - non usare st.error qui (causa problemi in import)
            print(f"❌ Errore recupero log: {e}")
            return []
    
    def get_all_logs_for_analytics(self) -> List[Dict]:
        """
        Recupera TUTTI i log per analytics dashboard.
        Usa paginazione per dataset grandi.
        
        Returns:
            Lista completa di record log
        """
        if not self.client:
            print("🔍 DEBUG: No Supabase client available")
            return []
        
        try:
            all_records = []
            page_size = 1000
            offset = 0
            
            while True:
                response = (
                    self.client.table(self.table_name)
                    .select("*")
                    .order("created_at", desc=False)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                
                if not response.data:
                    break
                
                all_records.extend(response.data)
                
                # Se riceviamo meno di page_size record, abbiamo finito
                if len(response.data) < page_size:
                    break
                
                offset += page_size
            
            return all_records
            
        except Exception as e:
            print(f"❌ Errore recupero log completi: {e}")
            return []


# ============================================================================
# SINGLETON & FACTORY
# ============================================================================

_logger_singleton: Optional[SupabaseLogger] = None

def get_logger() -> SupabaseLogger:
    """
    Singleton per SupabaseLogger.
    
    Returns:
        SupabaseLogger instance
    """
    global _logger_singleton
    if _logger_singleton is None:
        _logger_singleton = SupabaseLogger()
    return _logger_singleton


# ============================================================================
# FRONTEND.PY INTERFACE FUNCTIONS
# ============================================================================

def init_session_state():
    """
    Inizializza tutte le variabili di sessione necessarie per frontend.py.
    Chiamata una volta all'avvio dell'applicazione.
    """
    # Session ID univoco
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    
    # Chat messages history
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # Privacy acceptance flag
    if "privacy_accepted" not in st.session_state:
        st.session_state.privacy_accepted = False
    
    # Triage data collection
    if "collected_data" not in st.session_state:
        st.session_state.collected_data = {}
    
    # Current triage step
    if "current_step" not in st.session_state:
        st.session_state.current_step = "INIT"
    
    # Specialization (for routing)
    if "specialization" not in st.session_state:
        st.session_state.specialization = "Generale"
    
    # Location (comune)
    if "location" not in st.session_state:
        st.session_state.location = ""


def log_interaction_supabase(
    user_input: str,
    bot_response: str,
    metadata: Dict[str, Any],
    duration_ms: int = 0
) -> bool:
    """
    Wrapper per logging interazione su Supabase.
    Usa session_id dalla sessione corrente.
    
    Args:
        user_input: Messaggio utente
        bot_response: Risposta bot
        metadata: Metadati aggiuntivi
        duration_ms: Durata risposta AI in millisecondi
    
    Returns:
        bool: True se salvato con successo
    """
    logger = get_logger()
    session_id = st.session_state.get("session_id", str(uuid.uuid4()))
    
    return logger.log_interaction(
        session_id=session_id,
        user_input=user_input,
        bot_response=bot_response,
        metadata=metadata,
        duration_ms=duration_ms
    )
