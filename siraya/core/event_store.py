"""
SIRAYA Event Store - Event-Driven Triage Architecture
V3.0: Event Sourcing per tracciabilità completa e contatori affidabili.

Questo modulo implementa:
- Event Store per tracciare TUTTI gli eventi del triage
- Ricostruzione stato da eventi (single source of truth)
- Contatori affidabili basati su eventi invece di variabili globali
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Tipi di eventi del triage."""
    BRANCH_CLASSIFIED = "branch_classified"
    DATA_EXTRACTED = "data_extracted"
    PHASE_ENTERED = "phase_entered"
    QUESTION_ASKED = "question_asked"
    ANSWER_RECEIVED = "answer_received"
    PHASE_COMPLETED = "phase_completed"
    TRIAGE_COMPLETED = "triage_completed"


class TriageEventStore:
    """
    Event store per tracciare TUTTI gli eventi del triage.
    Permette ricostruzione stato senza contatori fragili.
    """
    
    def __init__(self, db_service, state_manager):
        self.db = db_service
        self.state = state_manager
        self._events_cache = []  # Cache locale eventi sessione corrente
    
    def emit(self, event_type: EventType, phase: str, data: Dict) -> None:
        """
        Emette un evento e lo salva immediatamente in Supabase.
        
        Args:
            event_type: Tipo evento (EventType enum)
            phase: Fase corrente (es. "CLINICAL_TRIAGE")
            data: Payload evento (dict)
        """
        session_id = self.state.get("session_id", "unknown")
        
        event = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type.value,
            "phase": phase,
            "data": data
        }
        
        # Salva in cache locale
        self._events_cache.append(event)
        
        # Salva in Supabase (tabella triage_logs con colonna event_type nel metadata)
        try:
            # Prepara dati per triage_logs
            user_input = data.get("user_input", "")
            assistant_response = data.get("assistant_response", "")
            processing_time = data.get("processing_time_ms", 0)
            
            # Metadata con info evento
            metadata = {
                "event": True,
                "event_type": event_type.value,
                "phase": phase,
                "event_data": data
            }
            
            # Session state con info evento
            session_state = {
                "event_type": event_type.value,
                "phase": phase,
                "data": data
            }
            
            self.db.supabase.table("triage_logs").insert({
                "session_id": session_id,
                "timestamp": event["timestamp"],
                "user_input": user_input,
                "assistant_response": assistant_response,
                "processing_time_ms": processing_time,
                "session_state": session_state,
                "metadata": metadata
            }).execute()
            
            logger.info(f"📤 Event emitted: {event_type.value} @ {phase}")
        except Exception as e:
            logger.error(f"❌ Failed to emit event: {e}")
    
    def get_events(
        self, 
        session_id: Optional[str] = None, 
        event_type: Optional[EventType] = None
    ) -> List[Dict]:
        """
        Recupera eventi dalla cache o da Supabase.
        
        Args:
            session_id: ID sessione (default: corrente)
            event_type: Filtra per tipo evento (opzionale)
            
        Returns:
            Lista di eventi (dict con session_id, timestamp, event_type, phase, data)
        """
        if session_id is None:
            session_id = self.state.get("session_id", "unknown")
        
        # Prima controlla cache
        if self._events_cache:
            events = self._events_cache
        else:
            # Fallback a Supabase
            try:
                response = self.db.supabase.table("triage_logs")\
                    .select("*")\
                    .eq("session_id", session_id)\
                    .order("timestamp", desc=False)\
                    .execute()
                
                events = []
                for row in response.data:
                    session_state = row.get("session_state", {})
                    if isinstance(session_state, str):
                        import json
                        try:
                            session_state = json.loads(session_state)
                        except:
                            session_state = {}
                    
                    # Estrai info evento
                    event_type_val = session_state.get("event_type", "unknown")
                    phase_val = session_state.get("phase", "")
                    data_val = session_state.get("data", {})
                    
                    events.append({
                        "session_id": row["session_id"],
                        "timestamp": row["timestamp"],
                        "event_type": event_type_val,
                        "phase": phase_val,
                        "data": data_val
                    })
                
                self._events_cache = events
                logger.info(f"📥 Loaded {len(events)} events from Supabase")
            except Exception as e:
                logger.error(f"❌ Failed to fetch events: {e}")
                events = []
        
        # Filtra per event_type se richiesto
        if event_type:
            events = [e for e in events if e.get("event_type") == event_type.value]
        
        return events
    
    def count_questions_in_phase(self, phase: str) -> int:
        """
        Conta quante domande sono state fatte in una fase specifica.
        Più affidabile di un counter globale.
        
        Args:
            phase: Nome fase (es. "CLINICAL_TRIAGE", "FAST_TRIAGE")
            
        Returns:
            Numero di domande nella fase
        """
        events = self.get_events(event_type=EventType.QUESTION_ASKED)
        count = sum(1 for e in events if e.get("phase") == phase)
        logger.info(f"📊 Questions in phase {phase}: {count}")
        return count
    
    def get_collected_data_from_events(self) -> Dict:
        """
        Ricostruisce collected_data da eventi DATA_EXTRACTED.
        Single source of truth: eventi, non session state.
        
        Returns:
            Dict con tutti i dati estratti durante la conversazione
        """
        events = self.get_events(event_type=EventType.DATA_EXTRACTED)
        collected = {}
        
        for event in events:
            extracted = event.get("data", {}).get("extracted", {})
            if isinstance(extracted, dict):
                collected.update(extracted)
        
        logger.info(f"📦 Collected data from events: {list(collected.keys())}")
        return collected
    
    def get_current_phase_from_events(self) -> str:
        """
        Determina fase corrente dall'ultimo evento PHASE_ENTERED.
        
        Returns:
            Nome fase corrente (default: "intake")
        """
        events = self.get_events(event_type=EventType.PHASE_ENTERED)
        if events:
            return events[-1].get("phase", "intake")
        return "intake"
    
    def clear_cache(self) -> None:
        """Pulisce cache locale (utile per nuovo triage)."""
        self._events_cache = []
        logger.info("🗑️ Event cache cleared")


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_event_store_instance: Optional[TriageEventStore] = None


def get_event_store() -> TriageEventStore:
    """
    Get singleton event store instance.
    
    Returns:
        TriageEventStore instance
    """
    global _event_store_instance
    if _event_store_instance is None:
        from .state_manager import get_state_manager
        from ..services.db_service import get_db_service
        
        _event_store_instance = TriageEventStore(
            db_service=get_db_service(),
            state_manager=get_state_manager()
        )
    return _event_store_instance

