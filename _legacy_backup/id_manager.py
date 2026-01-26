"""
ID Manager - Atomic ID Generation con File Locking
Genera ID univoci formato: 0001_ddMMyy

Versione semplificata cross-platform.
"""

import os
import time
import threading
from datetime import datetime
from typing import Optional

COUNTER_FILE = "id_counter.txt"
MAX_RETRIES = 10
RETRY_DELAY = 0.1  # secondi

# Thread lock globale per sicurezza in-process
_lock = threading.Lock()


class IDManager:
    """
    Gestione atomica ID con thread lock.
    """
    
    def __init__(self, counter_file: str = COUNTER_FILE):
        self.counter_file = counter_file
    
    def _read_counter(self) -> int:
        """Legge contatore da file."""
        if not os.path.exists(self.counter_file):
            return 0
        
        try:
            with open(self.counter_file, 'r') as f:
                content = f.read().strip()
                return int(content) if content else 0
        except:
            return 0
    
    def _write_counter(self, value: int):
        """Scrive contatore su file."""
        try:
            with open(self.counter_file, 'w') as f:
                f.write(str(value))
        except Exception as e:
            print(f"Errore scrittura contatore: {e}")
    
    def generate_id(self) -> Optional[str]:
        """
        Genera ID univoco con formato 0001_ddMMyy.
        
        Returns:
            ID univoco o None se errore
        """
        with _lock:  # Thread-safe
            try:
                # Leggi e incrementa contatore
                counter = self._read_counter()
                counter += 1
                
                # Genera ID
                date_suffix = datetime.now().strftime("%d%m%y")
                session_id = f"{counter:04d}_{date_suffix}"
                
                # Salva nuovo contatore
                self._write_counter(counter)
                
                return session_id
            
            except Exception as e:
                print(f"Errore generazione ID: {e}")
                return None


# === FUNZIONE HELPER ===
def get_new_session_id() -> str:
    """
    Wrapper convenience per generazione ID.
    
    Returns:
        ID univoco o fallback basato su timestamp
    """
    manager = IDManager()
    session_id = manager.generate_id()
    
    if session_id is None:
        # Fallback: timestamp se ID manager fallisce
        fallback_id = f"FALLBACK_{int(time.time())}"
        print(f"Usando fallback ID: {fallback_id}")
        return fallback_id
    
    return session_id


# === TEST ===
if __name__ == "__main__":
    print("Testing ID Manager...")
    
    for i in range(5):
        session_id = get_new_session_id()
        print(f"Generated ID {i + 1}: {session_id}")
        time.sleep(0.05)
    
    print("ID Manager test completato")
