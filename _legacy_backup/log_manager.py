"""
LogManager - Atomic Thread-Safe JSONL Writer
SIRAYA 2026 Evolution: Monolithic Integration

Zero Pandas Policy: Usa solo dict, list, json, threading.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Singleton lock globale per thread-safety
_LOG_LOCK = threading.Lock()


class LogManager:
    """
    Manager atomico per scrittura thread-safe su file JSONL.
    
    Caratteristiche:
    - Thread-safe con threading.Lock()
    - Scrittura atomica (append + flush + fsync)
    - Validazione schema prima della scrittura
    - Timestamp ISO 8601 generato al momento della scrittura (2026)
    """
    
    def __init__(self, log_file: str = "triage_logs.jsonl"):
        """
        Inizializza LogManager.
        
        Args:
            log_file: Path al file JSONL (default: triage_logs.jsonl)
        """
        self.log_file = log_file
        self._ensure_directory()
    
    def _ensure_directory(self):
        """Assicura che la directory del file esista."""
        log_path = Path(self.log_file)
        if log_path.parent and not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def _validate_log_entry(entry: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Valida schema log entry secondo REQUIRED_FIELDS del backend.
        
        Args:
            entry: Dizionario log entry da validare
        
        Returns:
            (is_valid, error_message): True se valido, False + messaggio errore altrimenti
        """
        # Campi obbligatori richiesti da backend.py
        required_fields = {
            'session_id': str,
            'timestamp_start': str,
            'timestamp_end': str,
        }
        
        # Verifica campi obbligatori
        for field, expected_type in required_fields.items():
            if field not in entry:
                return False, f"Campo obbligatorio '{field}' mancante"
            
            if not isinstance(entry[field], expected_type):
                return False, f"Campo '{field}' tipo errato (atteso {expected_type.__name__})"
        
        # Verifica presenza urgency_level (in outcome o metadata)
        urgency_found = False
        
        if 'outcome' in entry and isinstance(entry['outcome'], dict):
            if 'urgency_level' in entry['outcome']:
                urgency_found = True
        
        if not urgency_found and 'metadata' in entry and isinstance(entry['metadata'], dict):
            if 'urgency' in entry['metadata'] or 'urgency_level' in entry['metadata']:
                urgency_found = True
        
        if not urgency_found and ('urgency' in entry or 'urgency_level' in entry):
            urgency_found = True
        
        if not urgency_found:
            return False, "urgency_level non trovato (obbligatorio per dashboard)"
        
        # Validazione formato timestamp ISO 8601
        for ts_field in ['timestamp_start', 'timestamp_end']:
            if ts_field in entry:
                ts_str = entry[ts_field]
                try:
                    datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    return False, f"Timestamp '{ts_field}' formato non valido: {ts_str}"
        
        return True, None
    
    def write_log(self, log_data: Dict[str, Any], force_timestamp: bool = True) -> bool:
        """
        Scrive un record log in modo atomico e thread-safe.
        
        IMPORTANTE: Se force_timestamp=True, sovrascrive timestamp_start e timestamp_end
        con timestamp ISO 8601 generato AL MOMENTO DELLA SCRITTURA (2026).
        Questo garantisce che i log del 2026 passino sempre la validazione.
        
        Args:
            log_data: Dizionario con dati log (può non avere timestamp)
            force_timestamp: Se True, forza timestamp ISO 8601 al momento scrittura
        
        Returns:
            bool: True se scritto con successo, False altrimenti
        """
        # Crea copia per non modificare l'originale
        entry = log_data.copy()
        
        # FIX 2026: Genera timestamp ISO 8601 AL MOMENTO DELLA SCRITTURA
        if force_timestamp:
            now = datetime.now()
            entry['timestamp_start'] = entry.get('timestamp_start', now.isoformat())
            entry['timestamp_end'] = now.isoformat()  # Sempre timestamp corrente (2026)
        
        # Validazione schema
        is_valid, error_msg = self._validate_log_entry(entry)
        if not is_valid:
            logger.error(f"❌ Validazione log fallita: {error_msg}")
            logger.debug(f"Entry scartata: {json.dumps(entry, ensure_ascii=False)[:200]}...")
            return False
        
        # Scrittura atomica thread-safe
        try:
            with _LOG_LOCK:
                # Append atomico con flush + fsync
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                
                logger.debug(f"✅ Log scritto atomico: session={entry.get('session_id', 'unknown')}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Errore scrittura log atomica: {e}")
            return False
    
    def write_log_batch(self, log_entries: list[Dict[str, Any]], force_timestamp: bool = True) -> int:
        """
        Scrive multipli record in modo atomico (batch write).
        
        Args:
            log_entries: Lista di dizionari log
            force_timestamp: Se True, forza timestamp ISO 8601
        
        Returns:
            int: Numero di record scritti con successo
        """
        written = 0
        
        with _LOG_LOCK:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    for entry_data in log_entries:
                        entry = entry_data.copy()
                        
                        if force_timestamp:
                            now = datetime.now()
                            entry['timestamp_start'] = entry.get('timestamp_start', now.isoformat())
                            entry['timestamp_end'] = now.isoformat()
                        
                        is_valid, error_msg = self._validate_log_entry(entry)
                        if not is_valid:
                            logger.warning(f"⚠️ Entry batch scartata: {error_msg}")
                            continue
                        
                        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                        written += 1
                    
                    f.flush()
                    os.fsync(f.fileno())
                
                logger.info(f"✅ Batch write: {written}/{len(log_entries)} record scritti")
                return written
                
            except Exception as e:
                logger.error(f"❌ Errore batch write: {e}")
                return written


# Singleton instance (opzionale, per comodità)
_default_log_manager: Optional[LogManager] = None


def get_log_manager(log_file: str = "triage_logs.jsonl") -> LogManager:
    """
    Factory function per ottenere istanza LogManager (singleton pattern).
    
    Args:
        log_file: Path al file JSONL
    
    Returns:
        LogManager: Istanza singleton
    """
    global _default_log_manager
    
    if _default_log_manager is None or _default_log_manager.log_file != log_file:
        _default_log_manager = LogManager(log_file)
    
    return _default_log_manager

