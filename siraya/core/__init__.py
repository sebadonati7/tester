"""
SIRAYA Core Package
Contains state management, navigation, and authentication.
"""
from .state_manager import StateManager, init_session_state, get_state, set_state
from .navigation import Navigation, switch_to, get_current_page
from .authentication import AuthManager, check_privacy_accepted, require_privacy

