"""
SIRAYA Health Navigator - State Manager
V1.0: Il "Cervelletto" - Manages st.session_state safely.

This module handles:
- Session state initialization
- Safe read/write operations
- GDPR consent management
- Session persistence (Supabase integration)
"""

import uuid
import streamlit as st
from typing import Any, Dict, List, Optional, TypeVar
from datetime import datetime
from pathlib import Path

# Type variable for generic get/set
T = TypeVar('T')


# ============================================================================
# STATE KEYS DEFINITION
# ============================================================================

class StateKeys:
    """
    All session state keys in one place.
    Prevents typos and enables IDE autocompletion.
    """
    # Core session
    SESSION_ID = "session_id"
    USER_ID = "user_id"  # User identifier (anonymous o autenticato)
    TIMESTAMP_START = "timestamp_start"
    
    # Navigation
    CURRENT_PAGE = "current_page"
    PREVIOUS_PAGE = "previous_page"
    
    # Authentication & Privacy
    PRIVACY_ACCEPTED = "privacy_accepted"
    GDPR_TIMESTAMP = "gdpr_timestamp"
    ADMIN_LOGGED_IN = "admin_logged_in"
    
    # Chat/Triage
    MESSAGES = "messages"
    COLLECTED_DATA = "collected_data"
    CURRENT_PHASE = "current_phase"
    TRIAGE_PATH = "triage_path"
    QUESTION_COUNT = "question_count"
    TRIAGE_BRANCH = "triage_branch"  # Branch A/B/C/INFO
    LAST_BOT_RESPONSE = "last_bot_response"  # Ultima risposta bot per UI (include options)
    
    # Patient data
    PATIENT_AGE = "patient_age"
    PATIENT_SEX = "patient_sex"
    PATIENT_LOCATION = "patient_location"
    CHIEF_COMPLAINT = "chief_complaint"
    PAIN_SCALE = "pain_scale"
    RED_FLAGS = "red_flags"
    
    # Clinical
    SPECIALIZATION = "specialization"
    URGENCY_LEVEL = "urgency_level"
    DISPOSITION = "disposition"
    
    # Orchestrator
    ORCHESTRATOR = "orchestrator"
    
    # Service catalog
    SERVICE_CATALOG = "service_catalog"


# ============================================================================
# DEFAULT VALUES
# ============================================================================

DEFAULT_STATE: Dict[str, Any] = {
    # Core
    StateKeys.SESSION_ID: None,  # Will be generated
    StateKeys.USER_ID: "anonymous",  # Default: anonymous user
    StateKeys.TIMESTAMP_START: None,  # Will be set
    
    # Navigation
    StateKeys.CURRENT_PAGE: "CHAT",  # Default page
    StateKeys.PREVIOUS_PAGE: None,
    
    # Auth
    StateKeys.PRIVACY_ACCEPTED: False,
    StateKeys.GDPR_TIMESTAMP: None,
    StateKeys.ADMIN_LOGGED_IN: False,
    
    # Chat
    StateKeys.MESSAGES: [],
    StateKeys.COLLECTED_DATA: {},
    StateKeys.CURRENT_PHASE: "intake",           # V2.1: lowercase per match con TriagePhase enum
    StateKeys.TRIAGE_PATH: None,             # V3: None until SmartRouter assigns it
    StateKeys.TRIAGE_BRANCH: None,           # V2.1: Branch A/B/C/INFO from TriageController
    StateKeys.QUESTION_COUNT: 0,
    StateKeys.LAST_BOT_RESPONSE: {},         # V2.1: Ultima risposta con type/options
    
    # Patient
    StateKeys.PATIENT_AGE: None,
    StateKeys.PATIENT_SEX: None,
    StateKeys.PATIENT_LOCATION: None,
    StateKeys.CHIEF_COMPLAINT: None,
    StateKeys.PAIN_SCALE: None,
    StateKeys.RED_FLAGS: [],
    
    # Clinical
    StateKeys.SPECIALIZATION: "Generale",
    StateKeys.URGENCY_LEVEL: 3,
    StateKeys.DISPOSITION: None,
    
    # Orchestrator (initialized separately)
    StateKeys.ORCHESTRATOR: None,
    
    # Services
    StateKeys.SERVICE_CATALOG: [],
}


# ============================================================================
# STATE MANAGER CLASS
# ============================================================================

class StateManager:
    """
    Centralized state management for Streamlit session.
    
    Features:
    - Safe initialization with defaults
    - Type-safe get/set operations
    - GDPR consent tracking
    - Session persistence hooks
    
    Usage:
        from core.state_manager import StateManager
        
        state = StateManager()
        state.init()
        
        # Read
        session_id = state.get(StateKeys.SESSION_ID)
        
        # Write
        state.set(StateKeys.CURRENT_PAGE, "DASHBOARD")
    """
    
    def __init__(self):
        """Initialize state manager."""
        self._initialized = False
    
    def init(self) -> None:
        """
        Initialize all session state variables.
        
        Safe to call multiple times - only initializes missing keys.
        """
        # Generate session ID if not exists
        if StateKeys.SESSION_ID not in st.session_state or st.session_state[StateKeys.SESSION_ID] is None:
            st.session_state[StateKeys.SESSION_ID] = str(uuid.uuid4())
        
        # Set timestamp if not exists
        if StateKeys.TIMESTAMP_START not in st.session_state or st.session_state[StateKeys.TIMESTAMP_START] is None:
            st.session_state[StateKeys.TIMESTAMP_START] = datetime.now().isoformat()
        
        # Initialize all other keys with defaults
        for key, default_value in DEFAULT_STATE.items():
            if key not in st.session_state:
                # Deep copy for mutable types
                if isinstance(default_value, (list, dict)):
                    st.session_state[key] = default_value.copy() if default_value else type(default_value)()
                else:
                    st.session_state[key] = default_value
        
        self._initialized = True
    
    def get(self, key: str, default: T = None) -> T:
        """
        Safely get a value from session state.
        
        Args:
            key: State key to retrieve
            default: Default value if key doesn't exist
            
        Returns:
            Value or default
        """
        return st.session_state.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """
        Set a value in session state.
        
        Args:
            key: State key to set
            value: Value to store
        """
        st.session_state[key] = value
    
    def update(self, updates: Dict[str, Any]) -> None:
        """
        Update multiple state values at once.
        
        Args:
            updates: Dictionary of key-value pairs to update
        """
        for key, value in updates.items():
            st.session_state[key] = value
    
    def reset_triage(self) -> None:
        """
        Reset triage-related state while preserving session info.
        
        Useful for "New Triage" functionality.
        """
        triage_keys = [
            StateKeys.MESSAGES,
            StateKeys.COLLECTED_DATA,
            StateKeys.CURRENT_PHASE,
            StateKeys.TRIAGE_PATH,
            StateKeys.TRIAGE_BRANCH,        # V2.1
            StateKeys.QUESTION_COUNT,
            StateKeys.LAST_BOT_RESPONSE,    # V2.1
            StateKeys.PATIENT_AGE,
            StateKeys.PATIENT_SEX,
            StateKeys.PATIENT_LOCATION,
            StateKeys.CHIEF_COMPLAINT,
            StateKeys.PAIN_SCALE,
            StateKeys.RED_FLAGS,
            StateKeys.SPECIALIZATION,
            StateKeys.URGENCY_LEVEL,
            StateKeys.DISPOSITION,
        ]
        
        for key in triage_keys:
            default = DEFAULT_STATE.get(key)
            if isinstance(default, (list, dict)):
                st.session_state[key] = type(default)()
            else:
                st.session_state[key] = default
        
        # Generate new session ID for new triage
        st.session_state[StateKeys.SESSION_ID] = str(uuid.uuid4())
        st.session_state[StateKeys.TIMESTAMP_START] = datetime.now().isoformat()
    
    def get_patient_data(self) -> Dict[str, Any]:
        """
        Get all patient-related data as a dictionary.
        
        Returns:
            Dictionary with patient information
        """
        return {
            "session_id": self.get(StateKeys.SESSION_ID),
            "age": self.get(StateKeys.PATIENT_AGE),
            "sex": self.get(StateKeys.PATIENT_SEX),
            "location": self.get(StateKeys.PATIENT_LOCATION),
            "chief_complaint": self.get(StateKeys.CHIEF_COMPLAINT),
            "pain_scale": self.get(StateKeys.PAIN_SCALE),
            "red_flags": self.get(StateKeys.RED_FLAGS, []),
            "specialization": self.get(StateKeys.SPECIALIZATION),
            "urgency_level": self.get(StateKeys.URGENCY_LEVEL),
        }
    
    def update_collected_data(self, key: str, value: Any) -> None:
        """
        Update the collected_data dictionary with a new value.
        
        Args:
            key: Data key (e.g., "LOCATION", "CHIEF_COMPLAINT")
            value: Value to store
        """
        collected = self.get(StateKeys.COLLECTED_DATA, {})
        collected[key] = value
        self.set(StateKeys.COLLECTED_DATA, collected)
    
    def add_message(self, role: str, content: str) -> None:
        """
        Add a message to the chat history.
        
        Args:
            role: "user" or "assistant"
            content: Message text
        """
        messages = self.get(StateKeys.MESSAGES, [])
        messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.set(StateKeys.MESSAGES, messages)
    
    def accept_privacy(self) -> None:
        """Record GDPR privacy acceptance with timestamp."""
        self.set(StateKeys.PRIVACY_ACCEPTED, True)
        self.set(StateKeys.GDPR_TIMESTAMP, datetime.now().isoformat())
    
    def is_privacy_accepted(self) -> bool:
        """Check if privacy has been accepted."""
        return self.get(StateKeys.PRIVACY_ACCEPTED, False)
    
    @property
    def session_id(self) -> str:
        """Get current session ID."""
        return self.get(StateKeys.SESSION_ID, "")
    
    @property
    def current_page(self) -> str:
        """Get current page name."""
        return self.get(StateKeys.CURRENT_PAGE, "CHAT")
    
    @property
    def messages(self) -> List[Dict[str, str]]:
        """Get chat message history."""
        return self.get(StateKeys.MESSAGES, [])


# ============================================================================
# MODULE-LEVEL CONVENIENCE FUNCTIONS
# ============================================================================

_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get singleton StateManager instance."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


def init_session_state() -> None:
    """
    Initialize session state.
    
    Convenience function for use in app.py.
    """
    manager = get_state_manager()
    manager.init()


def get_state(key: str, default: T = None) -> T:
    """
    Get a state value.
    
    Convenience function for use throughout the app.
    """
    return st.session_state.get(key, default)


def set_state(key: str, value: Any) -> None:
    """
    Set a state value.
    
    Convenience function for use throughout the app.
    """
    st.session_state[key] = value

