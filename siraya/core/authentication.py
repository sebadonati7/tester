"""
SIRAYA Health Navigator - Authentication
V1.0: Il "Portinaio" - Handles privacy consent and admin access.

This module provides:
- Privacy/GDPR consent management
- Admin authentication
- Access control decorators
"""

import streamlit as st
from typing import Optional, Callable
from functools import wraps
from datetime import datetime


# ============================================================================
# STATE KEYS
# ============================================================================

class AuthKeys:
    """Authentication-related state keys."""
    PRIVACY_ACCEPTED = "privacy_accepted"
    GDPR_TIMESTAMP = "gdpr_timestamp"
    ADMIN_LOGGED_IN = "admin_logged_in"
    ADMIN_USERNAME = "admin_username"


# ============================================================================
# AUTHENTICATION MANAGER
# ============================================================================

class AuthManager:
    """
    Manages authentication and privacy consent.
    
    Features:
    - GDPR privacy acceptance tracking
    - Simple admin authentication
    - Permission decorators
    
    Usage:
        from core.authentication import AuthManager
        
        auth = AuthManager()
        
        if auth.is_privacy_accepted():
            # Show chat
        else:
            # Show privacy prompt
    """
    
    # Default admin credentials (should be in secrets in production)
    DEFAULT_ADMIN_PASSWORD = "ciaociao"
    
    def __init__(self):
        """Initialize auth manager."""
        self._ensure_state()
    
    def _ensure_state(self) -> None:
        """Ensure auth state keys exist."""
        if AuthKeys.PRIVACY_ACCEPTED not in st.session_state:
            st.session_state[AuthKeys.PRIVACY_ACCEPTED] = False
        if AuthKeys.GDPR_TIMESTAMP not in st.session_state:
            st.session_state[AuthKeys.GDPR_TIMESTAMP] = None
        if AuthKeys.ADMIN_LOGGED_IN not in st.session_state:
            st.session_state[AuthKeys.ADMIN_LOGGED_IN] = False
        if AuthKeys.ADMIN_USERNAME not in st.session_state:
            st.session_state[AuthKeys.ADMIN_USERNAME] = None
    
    # ========================================================================
    # PRIVACY CONSENT
    # ========================================================================
    
    def is_privacy_accepted(self) -> bool:
        """
        Check if user has accepted privacy policy.
        
        Returns:
            True if privacy accepted
        """
        self._ensure_state()
        return st.session_state.get(AuthKeys.PRIVACY_ACCEPTED, False)
    
    def accept_privacy(self) -> None:
        """
        Record privacy acceptance with timestamp.
        
        Should be called when user clicks "Accept" on privacy notice.
        """
        st.session_state[AuthKeys.PRIVACY_ACCEPTED] = True
        st.session_state[AuthKeys.GDPR_TIMESTAMP] = datetime.now().isoformat()
    
    def revoke_privacy(self) -> None:
        """
        Revoke privacy acceptance.
        
        Used for "Reset" or "Delete my data" functionality.
        """
        st.session_state[AuthKeys.PRIVACY_ACCEPTED] = False
        st.session_state[AuthKeys.GDPR_TIMESTAMP] = None
    
    def get_privacy_timestamp(self) -> Optional[str]:
        """
        Get timestamp when privacy was accepted.
        
        Returns:
            ISO timestamp string or None
        """
        return st.session_state.get(AuthKeys.GDPR_TIMESTAMP)
    
    # ========================================================================
    # ADMIN AUTHENTICATION
    # ========================================================================
    
    def is_admin_logged_in(self) -> bool:
        """
        Check if admin is logged in.
        
        Returns:
            True if admin authenticated
        """
        self._ensure_state()
        return st.session_state.get(AuthKeys.ADMIN_LOGGED_IN, False)
    
    def admin_login(self, password: str) -> bool:
        """
        Attempt admin login.
        
        Password check order:
        1. st.secrets["ADMIN_PASSWORD"] (if set)
        2. st.secrets["BACKEND_PASSWORD"] (if set)
        3. DEFAULT_ADMIN_PASSWORD ("ciaociao")
        
        Args:
            password: Admin password to verify
            
        Returns:
            True if login successful
        """
        # Get password from secrets (try ADMIN_PASSWORD, then BACKEND_PASSWORD)
        correct_password = None
        try:
            correct_password = st.secrets.get("ADMIN_PASSWORD")
        except (KeyError, TypeError, AttributeError):
            pass
        if not correct_password:
            try:
                correct_password = st.secrets.get("BACKEND_PASSWORD")
            except (KeyError, TypeError, AttributeError):
                pass
        if not correct_password:
            correct_password = self.DEFAULT_ADMIN_PASSWORD
        
        if password == correct_password:
            st.session_state[AuthKeys.ADMIN_LOGGED_IN] = True
            st.session_state[AuthKeys.ADMIN_USERNAME] = "admin"
            logger.info("✅ Admin login successful")
            return True
        logger.warning("❌ Admin login failed: incorrect password")
        return False
    
    def admin_logout(self) -> None:
        """Log out admin user."""
        st.session_state[AuthKeys.ADMIN_LOGGED_IN] = False
        st.session_state[AuthKeys.ADMIN_USERNAME] = None
    
    def get_admin_username(self) -> Optional[str]:
        """
        Get logged in admin username.
        
        Returns:
            Username or None
        """
        return st.session_state.get(AuthKeys.ADMIN_USERNAME)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get singleton AuthManager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


def check_privacy_accepted() -> bool:
    """
    Check if privacy is accepted.
    
    Convenience function for use in views.
    """
    auth = get_auth_manager()
    return auth.is_privacy_accepted()


def require_privacy(func: Callable) -> Callable:
    """
    Decorator that requires privacy acceptance.
    
    Usage:
        @require_privacy
        def render_chat():
            # This only runs if privacy accepted
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = get_auth_manager()
        if not auth.is_privacy_accepted():
            st.warning("⚠️ Devi accettare l'informativa sulla privacy per continuare.")
            st.info("Clicca sul checkbox nella barra laterale per accettare.")
            return None
        return func(*args, **kwargs)
    return wrapper


def require_admin(func: Callable) -> Callable:
    """
    Decorator that requires admin authentication.
    
    Usage:
        @require_admin
        def render_dashboard():
            # This only runs if admin logged in
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = get_auth_manager()
        if not auth.is_admin_logged_in():
            st.warning("🔒 Accesso riservato agli amministratori.")
            render_admin_login()
            return None
        return func(*args, **kwargs)
    return wrapper


def render_admin_login() -> bool:
    """
    Render admin login form.
    
    Returns:
        True if login successful
    """
    auth = get_auth_manager()
    
    st.markdown("### 🔐 Admin Login")
    
    with st.form("admin_login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        
        if submitted:
            if auth.admin_login(password):
                st.success("✅ Login effettuato!")
                st.rerun()
            else:
                st.error("❌ Password errata")
                return False
    
    return auth.is_admin_logged_in()


def render_privacy_consent() -> bool:
    """
    Render privacy consent checkbox/form.
    
    Returns:
        True if privacy accepted
    """
    auth = get_auth_manager()
    
    if auth.is_privacy_accepted():
        return True
    
    st.markdown("### 📜 Informativa Privacy")
    
    st.markdown("""
    Prima di continuare, leggi e accetta l'informativa sulla privacy.
    
    **SIRAYA Health Navigator** raccoglie dati per:
    - Fornire assistenza nella navigazione sanitaria
    - Migliorare la qualità del servizio
    - Analisi statistiche anonime
    
    I tuoi dati **non vengono venduti** a terzi e sono trattati 
    nel rispetto del GDPR (Regolamento UE 2016/679).
    """)
    
    accept = st.checkbox("✅ Accetto l'informativa sulla privacy e i termini di servizio")
    
    if accept:
        auth.accept_privacy()
        st.success("Grazie! Ora puoi utilizzare il servizio.")
        st.rerun()
    
    return False

