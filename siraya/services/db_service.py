"""
SIRAYA Database Service - Gestione Supabase con modalità offline
V2.0: Ripristino connessione + fallback JSONL locale
"""

import json
import logging
import streamlit as st
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class DatabaseService:
    """
    Servizio database centralizzato.
    
    Features:
    - Connessione Supabase con retry e validazione
    - Modalità offline con salvataggio locale JSONL
    - Verifica scrittura con logging
    - Gestione errori robusta
    """
    
    def __init__(self):
        self.supabase = None
        self.connection_tested = False
        self.offline_mode = False
        self.offline_log_path = Path("offline_logs.jsonl")
        
        self._init_connection()
    
    def _init_connection(self) -> None:
        """Inizializza connessione Supabase con gestione errori robusta."""
        try:
            from ..config.settings import SupabaseConfig
            
            if not SupabaseConfig.is_configured():
                logger.warning("⚠️ Supabase non configurato — modalità offline attiva")
                self.offline_mode = True
                return
            
            from supabase import create_client
            
            url = SupabaseConfig.get_url()
            key = SupabaseConfig.get_key()
            
            if not url or not key:
                logger.warning("⚠️ Credenziali Supabase mancanti — modalità offline")
                self.offline_mode = True
                return
            
            # Crea client
            self.supabase = create_client(url, key)
            
            # Test connessione con query semplice
            try:
                test_result = self.supabase.table(SupabaseConfig.TABLE_LOGS).select("id").limit(1).execute()
                self.connection_tested = True
                self.offline_mode = False
                logger.info("✅ Database Service connesso a Supabase")
            except Exception as test_e:
                logger.error(f"❌ Test connessione Supabase fallito: {test_e}")
                self.offline_mode = True
                self.connection_tested = False
                
        except ImportError:
            logger.error("❌ Modulo supabase non installato — modalità offline")
            self.offline_mode = True
        except Exception as e:
            logger.error(f"❌ Errore inizializzazione database: {type(e).__name__} - {e}")
            self.offline_mode = True
    
    def is_connected(self) -> bool:
        """Verifica se la connessione è attiva."""
        return self.connection_tested and not self.offline_mode
    
    def save_interaction(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        processing_time_ms: Optional[int] = None,
        session_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Salva interazione su Supabase con tutti i KPI clinici e tecnici.
        
        Args:
            session_id: ID sessione
            user_input: Input utente
            assistant_response: Risposta assistente
            processing_time_ms: Tempo di elaborazione in millisecondi
            session_state: Stato della sessione (per estrarre KPI clinici)
            metadata: Metadati aggiuntivi
            
        Returns:
            True se salvato con successo
        """
        # Estrai dati clinici da session_state
        collected = session_state.get("collected_data", {}) if session_state else {}
        
        # Mappa triage_path a triage_code
        triage_path = session_state.get("triage_path", "C") if session_state else "C"
        urgency_level = session_state.get("urgency_level", 3) if session_state else 3
        
        # Mappa urgency_level + triage_path a triage_code
        triage_code_map = {
            (5, "A"): "ROSSO",
            (4, "A"): "ARANCIONE",
            (3, "A"): "ARANCIONE",
            (5, "B"): "NERO",
            (4, "B"): "NERO",
            (3, "B"): "NERO",
            (4, "C"): "GIALLO",
            (3, "C"): "GIALLO",
            (2, "C"): "VERDE",
            (1, "C"): "VERDE",
        }
        triage_code = triage_code_map.get((urgency_level, triage_path), "GIALLO")
        if triage_path == "INFO":
            triage_code = "BIANCO"
        
        # Estrai specializzazione
        medical_specialty = session_state.get("specialization", "Generale") if session_state else "Generale"
        
        # Estrai detected_intent (chief_complaint)
        detected_intent = collected.get("chief_complaint") or session_state.get("chief_complaint") if session_state else None
        
        # Determina suggested_facility_type
        if triage_path == "A" or urgency_level >= 4:
            suggested_facility_type = "Pronto Soccorso"
        elif triage_path == "B":
            suggested_facility_type = "CSM"  # Centro di Salute Mentale
        else:
            suggested_facility_type = "CAU"  # Centro Assistenza Urgenze
        
        # Estrai reasoning (primi 500 caratteri della risposta o metadata)
        reasoning = None
        if metadata and isinstance(metadata, dict):
            reasoning = metadata.get("reasoning")
        if not reasoning and assistant_response:
            reasoning = assistant_response[:500]  # Primi 500 caratteri come fallback
        
        # Estrai estimated_wait_time basato su triage_code
        wait_time_map = {
            "ROSSO": "Immediato (< 15 min)",
            "ARANCIONE": "Urgente (15-60 min)",
            "GIALLO": "Differibile (1-2 ore)",
            "VERDE": "Non urgente (2-4 ore)",
            "NERO": "Supporto specializzato",
            "BIANCO": "N/A"
        }
        estimated_wait_time = wait_time_map.get(triage_code, "N/D")
        
        # Prepara record completo
        record = {
            "session_id": session_id,
            "user_input": user_input[:1000] if user_input else None,  # Limita lunghezza
            "bot_response": assistant_response[:2000] if assistant_response else None,  # Limita lunghezza
            "detected_intent": detected_intent[:200] if detected_intent else None,
            "triage_code": triage_code,
            "medical_specialty": medical_specialty,
            "suggested_facility_type": suggested_facility_type,
            "reasoning": reasoning[:1000] if reasoning else None,  # Limita lunghezza
            "estimated_wait_time": estimated_wait_time,
            "processing_time_ms": processing_time_ms,
            "model_version": "v4.0-llama-3.3-70b",  # Versione modello corrente
            "tokens_used": None,  # Non disponibile da Groq/Gemini senza response metadata
            "client_ip": None,  # Non disponibile in Streamlit Cloud senza header
            "metadata": metadata or {}  # JSONB accetta dict direttamente
        }
        
        # Prova Supabase
        if self.is_connected():
            try:
                from ..config.settings import SupabaseConfig
                
                response = self.supabase.table(SupabaseConfig.TABLE_LOGS).insert(record).execute()
                
                # Verifica risposta
                if response and hasattr(response, 'data'):
                    logger.info(f"✅ Interazione salvata su Supabase: {session_id[:8]}")
                    return True
                else:
                    logger.warning("⚠️ Risposta Supabase non valida — fallback offline")
                    return self._save_offline(record)
                    
            except Exception as e:
                logger.error(f"❌ Errore salvataggio Supabase: {type(e).__name__} - {e}")
                return self._save_offline(record)
        else:
            # Modalità offline
            return self._save_offline(record)
    
    def _save_offline(self, record: Dict[str, Any]) -> bool:
        """Salva record in file JSONL locale."""
        try:
            with open(self.offline_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"💾 Interazione salvata offline: {record['session_id'][:8]}")
            return True
        except Exception as e:
            logger.error(f"❌ Errore salvataggio offline: {e}")
            return False
    
    def save_session_complete(
        self,
        session_id: str,
        collected_data: Dict[str, Any],
        sbar_report: Optional[str] = None
    ) -> bool:
        """
        Salva sessione completata con SBAR.
        
        Args:
            session_id: ID sessione
            collected_data: Dati raccolti durante triage
            sbar_report: Report SBAR finale
            
        Returns:
            True se salvato con successo
        """
        record = {
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "collected_data": collected_data,
            "sbar_report": sbar_report,
        }
        
        if self.is_connected():
            try:
                from ..config.settings import SupabaseConfig
                
                # Salva in tabella sessions (se esiste) o logs
                table_name = SupabaseConfig.TABLE_SESSIONS
                response = self.supabase.table(table_name).insert(record).execute()
                
                if response:
                    logger.info(f"✅ Sessione completata salvata: {session_id[:8]}")
                    return True
            except Exception as e:
                logger.warning(f"⚠️ Errore salvataggio sessione: {e}")
        
        # Fallback offline
        return self._save_offline(record)
    
    def get_status_message(self) -> str:
        """Restituisce messaggio di stato per UI."""
        if self.is_connected():
            return "✅ Database Connesso"
        elif self.offline_mode:
            return "💾 Modalità Offline (salvataggio locale)"
        else:
            return "⚠️ Database Non Configurato"
    
    def fetch_user_history(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Recupera cronologia conversazioni utente da Supabase.
        Previene domande duplicate (età, località già note).
        
        Args:
            user_id: Identificativo utente (session_id o user_id)
            limit: Numero massimo di interazioni da recuperare
        
        Returns:
            Lista interazioni [{session_state, user_input, created_at, ...}]
        """
        if not self.is_connected():
            logger.warning("⚠️ fetch_user_history chiamato ma DB offline")
            return []
        
        try:
            from ..config.settings import SupabaseConfig
            
            result = self.supabase.table(SupabaseConfig.TABLE_LOGS)\
                .select("session_id, user_input, bot_response, metadata, created_at")\
                .eq("session_id", user_id)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            
            if result and result.data:
                logger.info(f"✅ Recuperate {len(result.data)} interazioni storiche per {user_id[:8]}")
                return result.data
            else:
                return []
                
        except Exception as e:
            logger.error(f"❌ Errore fetch history: {type(e).__name__} - {e}")
            return []


# Singleton instance
_db_service: Optional[DatabaseService] = None


def get_db_service() -> DatabaseService:
    """Get singleton DatabaseService instance."""
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service


def init_db_connection() -> DatabaseService:
    """
    Inizializza connessione database.
    
    Compatibile con il prompt richiesto.
    """
    return get_db_service()

