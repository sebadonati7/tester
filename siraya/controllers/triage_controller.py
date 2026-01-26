"""
SIRAYA Health Navigator - Triage Controller
V2.0: Full orchestration with Supabase logging.

This controller:
- Receives input from chat view
- Calls LLM service
- Updates state manager
- Logs to Supabase
- Handles emergency detection
"""

import time
import json
import logging
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime

import streamlit as st

from ..core.state_manager import get_state_manager, StateKeys
from ..core.navigation import get_navigation, PageName
from ..services.llm_service import get_llm_service
from ..config.settings import EMERGENCY_RULES, SupabaseConfig

logger = logging.getLogger(__name__)


# ============================================================================
# SUPABASE LOGGER
# ============================================================================

def _log_to_supabase(
    session_id: str,
    user_input: str,
    bot_response: str,
    metadata: Dict[str, Any],
    duration_ms: int = 0
) -> bool:
    """
    Log interaction directly to Supabase.
    
    Args:
        session_id: Current session ID
        user_input: User's message
        bot_response: AI's response
        metadata: Additional metadata (phase, urgency, etc.)
        duration_ms: Processing time
        
    Returns:
        True if logging successful
    """
    if not SupabaseConfig.is_configured():
        logger.warning("Supabase not configured - skipping log")
        return False
    
    try:
        from supabase import create_client
        
        client = create_client(
            SupabaseConfig.get_url(),
            SupabaseConfig.get_key()
        )
        
        log_record = {
            "session_id": session_id,
            "user_input": user_input,
            "bot_response": bot_response,
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "processing_time_ms": duration_ms,
            "created_at": datetime.utcnow().isoformat()
        }
        
        result = client.table(SupabaseConfig.TABLE_LOGS).insert(log_record).execute()
        
        if result.data:
            logger.info(f"✅ Logged interaction to Supabase: {session_id[:8]}...")
            return True
        else:
            logger.warning("Supabase insert returned no data")
            return False
            
    except ImportError:
        logger.error("supabase-py not installed")
        return False
    except Exception as e:
        logger.error(f"Supabase logging error: {e}")
        return False


# ============================================================================
# TRIAGE CONTROLLER CLASS
# ============================================================================

class TriageController:
    """
    Controller for triage flow management.
    
    Connects user input to AI service and state updates.
    Implements Visual Parity with legacy frontend.py logic.
    """
    
    def __init__(self):
        """Initialize controller with dependencies."""
        self.state = get_state_manager()
        self.llm = get_llm_service()
        self.nav = get_navigation()
    
    def handle_user_input(
        self,
        user_message: str,
        session_id: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Main entry point: Process user message through triage pipeline.
        
        Steps:
        1. Start timing
        2. Check for emergency keywords
        3. Call LLM service
        4. Extract and store data
        5. Update phase
        6. Log to Supabase
        
        Args:
            user_message: User's input text
            session_id: Current session identifier
            
        Returns:
            Tuple of (response_text, metadata_dict)
        """
        start_time = time.time()
        
        # Step 1: Emergency check (CRITICAL - before AI call)
        emergency_response, emergency_meta = self._check_emergency(user_message)
        if emergency_response:
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Log emergency to Supabase
            _log_to_supabase(
                session_id=session_id,
                user_input=user_message,
                bot_response=emergency_response,
                metadata=emergency_meta,
                duration_ms=duration_ms
            )
            
            return emergency_response, emergency_meta
        
        # Step 2: Call LLM
        try:
            collected_data = self.state.get(StateKeys.COLLECTED_DATA, {})
            current_phase = self.state.get(StateKeys.CURRENT_PHASE, "INTENT_DETECTION")
            triage_path = self.state.get(StateKeys.TRIAGE_PATH, "C")
            
            response, response_meta = self.llm.process_message(
                user_message=user_message,
                session_id=session_id,
                collected_data=collected_data,
                current_phase=current_phase,
                triage_path=triage_path
            )
            
        except Exception as e:
            logger.error(f"LLM error: {e}")
            response = self.llm.get_fallback_response(
                self.state.get(StateKeys.CURRENT_PHASE, "INIT")
            )
            response_meta = {"fallback_used": True, "error": str(e)}
        
        # Step 3: Extract data from response
        self._extract_data_from_response(user_message, response_meta)
        
        # Step 4: Update phase based on collected data
        self._update_phase()
        
        # Step 5: Calculate duration and build final metadata
        duration_ms = int((time.time() - start_time) * 1000)
        
        final_metadata = {
            "phase": self.state.get(StateKeys.CURRENT_PHASE, "Unknown"),
            "triage_path": self.state.get(StateKeys.TRIAGE_PATH, "C"),
            "urgency": self.state.get(StateKeys.URGENCY_LEVEL, 3),
            "specialization": self.state.get(StateKeys.SPECIALIZATION, "Generale"),
            "location": self.state.get(StateKeys.PATIENT_LOCATION, "N/D"),
            "chief_complaint": self.state.get(StateKeys.CHIEF_COMPLAINT, ""),
            "pain_scale": self.state.get(StateKeys.PAIN_SCALE, None),
            "red_flags": self.state.get(StateKeys.RED_FLAGS, []),
            "question_count": self.state.get(StateKeys.QUESTION_COUNT, 0),
            "processing_time_ms": duration_ms,
            **response_meta  # Merge LLM metadata
        }
        
        # Step 6: Log to Supabase
        _log_to_supabase(
            session_id=session_id,
            user_input=user_message,
            bot_response=response,
            metadata=final_metadata,
            duration_ms=duration_ms
        )
        
        return response, final_metadata
    
    def _check_emergency(
        self,
        message: str
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Check for emergency keywords in user message.
        
        Args:
            message: User's input
            
        Returns:
            Tuple of (response_text, metadata) if emergency, (None, {}) otherwise
        """
        message_lower = message.lower()
        
        # Check CRITICAL red flags (immediate 118)
        for keyword in EMERGENCY_RULES.CRITICAL_RED_FLAGS:
            if keyword in message_lower:
                self.state.set(StateKeys.URGENCY_LEVEL, 5)
                self.state.set(StateKeys.TRIAGE_PATH, "A")
                
                red_flags = self.state.get(StateKeys.RED_FLAGS, [])
                if keyword not in red_flags:
                    red_flags.append(keyword)
                self.state.set(StateKeys.RED_FLAGS, red_flags)
                
                logger.critical(f"🚨 CRITICAL EMERGENCY: {keyword}")
                
                response = self.llm.get_emergency_response(keyword)
                metadata = {
                    "is_emergency": True,
                    "emergency_type": "CRITICAL",
                    "trigger_keyword": keyword,
                    "urgency": 5,
                    "triage_path": "A"
                }
                
                return response, metadata
        
        # Check mental health crisis (BLACK path)
        for keyword in EMERGENCY_RULES.MENTAL_HEALTH_CRISIS:
            if keyword in message_lower:
                self.state.set(StateKeys.URGENCY_LEVEL, 5)
                self.state.set(StateKeys.TRIAGE_PATH, "B")
                
                logger.warning(f"⚫ MENTAL HEALTH CRISIS: {keyword}")
                
                response = self.llm.get_emergency_response(keyword)
                metadata = {
                    "is_emergency": True,
                    "emergency_type": "MENTAL_HEALTH",
                    "trigger_keyword": keyword,
                    "urgency": 5,
                    "triage_path": "B"
                }
                
                return response, metadata
        
        # Check HIGH red flags (Path A fast-track)
        for keyword in EMERGENCY_RULES.HIGH_RED_FLAGS:
            if keyword in message_lower:
                self.state.set(StateKeys.URGENCY_LEVEL, 4)
                self.state.set(StateKeys.TRIAGE_PATH, "A")
                
                red_flags = self.state.get(StateKeys.RED_FLAGS, [])
                if keyword not in red_flags:
                    red_flags.append(keyword)
                self.state.set(StateKeys.RED_FLAGS, red_flags)
                
                # Don't return immediately - let AI continue with fast-track
                logger.info(f"🔴 HIGH URGENCY FLAG: {keyword}")
        
        return None, {}
    
    def _extract_data_from_response(
        self,
        user_input: str,
        response_meta: Dict[str, Any]
    ) -> None:
        """
        Extract structured data from user input and LLM response metadata.
        
        Args:
            user_input: User's message
            response_meta: Metadata from LLM response
        """
        # Extract from LLM metadata if available (dati_estratti)
        if "dati_estratti" in response_meta:
            extracted = response_meta["dati_estratti"]
            
            if extracted.get("LOCATION"):
                location = extracted["LOCATION"]
                self.state.set(StateKeys.PATIENT_LOCATION, location)
                self.state.update_collected_data("LOCATION", location)
            
            if extracted.get("CHIEF_COMPLAINT"):
                complaint = extracted["CHIEF_COMPLAINT"]
                self.state.set(StateKeys.CHIEF_COMPLAINT, complaint)
                self.state.update_collected_data("CHIEF_COMPLAINT", complaint)
            
            if extracted.get("PAIN_SCALE") is not None:
                pain = extracted["PAIN_SCALE"]
                self.state.set(StateKeys.PAIN_SCALE, pain)
                self.state.update_collected_data("PAIN_SCALE", pain)
            
            if extracted.get("RED_FLAGS"):
                flags = extracted["RED_FLAGS"]
                existing = self.state.get(StateKeys.RED_FLAGS, [])
                for flag in flags:
                    if flag not in existing:
                        existing.append(flag)
                self.state.set(StateKeys.RED_FLAGS, existing)
                self.state.update_collected_data("RED_FLAGS", existing)
            
            if extracted.get("age"):
                self.state.set(StateKeys.PATIENT_AGE, extracted["age"])
                self.state.update_collected_data("age", extracted["age"])
            
            if extracted.get("sex"):
                self.state.set(StateKeys.PATIENT_SEX, extracted["sex"])
                self.state.update_collected_data("sex", extracted["sex"])
        
        # Fallback: Simple pattern extraction from user input
        self._extract_from_user_input(user_input)
    
    def _extract_from_user_input(self, user_input: str) -> None:
        """Simple pattern extraction from user input as fallback."""
        import re
        
        # Age extraction
        age_match = re.search(r'\b(\d{1,3})\s*anni?\b', user_input.lower())
        if age_match:
            age = int(age_match.group(1))
            if 0 < age < 120:
                self.state.set(StateKeys.PATIENT_AGE, age)
                self.state.update_collected_data("age", age)
        
        # Pain scale extraction
        pain_match = re.search(r'\b([0-9]|10)\s*/?\s*10\b', user_input)
        if pain_match:
            pain = int(pain_match.group(1))
            self.state.set(StateKeys.PAIN_SCALE, pain)
            self.state.update_collected_data("PAIN_SCALE", pain)
    
    def _update_phase(self) -> None:
        """Update triage phase based on collected data."""
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        current_phase = self.state.get(StateKeys.CURRENT_PHASE, "INTENT_DETECTION")
        
        # Phase progression logic (matches frontend.py TriageStep order)
        if not collected.get("LOCATION"):
            new_phase = "LOCATION"
        elif not collected.get("CHIEF_COMPLAINT"):
            new_phase = "CHIEF_COMPLAINT"
        elif collected.get("PAIN_SCALE") is None:
            new_phase = "PAIN_ASSESSMENT"
        elif not collected.get("RED_FLAGS"):
            new_phase = "RED_FLAGS"
        elif not collected.get("age"):
            new_phase = "DEMOGRAPHICS"
        else:
            new_phase = "DISPOSITION"
        
        if new_phase != current_phase:
            self.state.set(StateKeys.CURRENT_PHASE, new_phase)
            
            # Increment question count
            count = self.state.get(StateKeys.QUESTION_COUNT, 0)
            self.state.set(StateKeys.QUESTION_COUNT, count + 1)
    
    def get_survey_options(self) -> Optional[List[str]]:
        """
        Get current survey options if pending.
        
        Returns:
            List of option strings or None
        """
        return st.session_state.get("pending_survey_options")
    
    def set_survey_options(self, options: List[str]) -> None:
        """Set pending survey options for button rendering."""
        st.session_state["pending_survey_options"] = options
    
    def clear_survey_options(self) -> None:
        """Clear pending survey options."""
        st.session_state["pending_survey_options"] = None
    
    def reset_triage(self) -> None:
        """Reset triage state for new session."""
        self.state.reset_triage()
        self.clear_survey_options()
    
    def get_completion_percentage(self) -> float:
        """
        Calculate triage completion percentage.
        
        Returns:
            Percentage 0-100
        """
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        
        required_fields = [
            "LOCATION",
            "CHIEF_COMPLAINT",
            "PAIN_SCALE",
            "RED_FLAGS",
            "age",
        ]
        
        filled = sum(1 for field in required_fields if collected.get(field))
        return (filled / len(required_fields)) * 100
    
    def can_generate_report(self) -> bool:
        """Check if enough data for report generation."""
        collected = self.state.get(StateKeys.COLLECTED_DATA, {})
        return bool(collected.get("LOCATION") or collected.get("CHIEF_COMPLAINT"))
    
    def get_current_step_display(self) -> str:
        """Get human-readable current step name."""
        phase = self.state.get(StateKeys.CURRENT_PHASE, "INIT")
        
        names = {
            "INTENT_DETECTION": "📍 Identificazione Intento",
            "LOCATION": "📍 Localizzazione",
            "CHIEF_COMPLAINT": "🩺 Sintomo Principale",
            "PAIN_ASSESSMENT": "📊 Intensità Dolore",
            "RED_FLAGS": "🚨 Segnali di Allarme",
            "DEMOGRAPHICS": "👤 Dati Anagrafici",
            "ANAMNESIS": "📋 Anamnesi Clinica",
            "DISPOSITION": "🏥 Raccomandazione Finale"
        }
        
        return names.get(phase, phase.replace("_", " ").title())


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_triage_controller: Optional[TriageController] = None


def get_triage_controller() -> TriageController:
    """Get singleton triage controller instance."""
    global _triage_controller
    if _triage_controller is None:
        _triage_controller = TriageController()
    return _triage_controller
