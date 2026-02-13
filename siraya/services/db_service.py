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
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Salva interazione su Supabase o in modalità offline.
        
        Args:
            session_id: ID sessione
            user_input: Input utente
            assistant_response: Risposta assistente
            metadata: Metadati aggiuntivi
            
        Returns:
            True se salvato con successo
        """
        timestamp = datetime.now().isoformat()
        
        record = {
            "session_id": session_id,
            "user_message": user_input,
            "assistant_message": assistant_response,
            "timestamp": timestamp,
            "metadata": metadata or {},
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

