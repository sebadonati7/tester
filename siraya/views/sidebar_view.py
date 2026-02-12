"""
SIRAYA Health Navigator - Sidebar View
V2.0: Visual Parity with legacy frontend.py sidebar.

This view:
- Renders logo and branding
- Shows navigation options
- Displays progress bar
- Handles privacy consent
- Shows system status
"""

import streamlit as st
from typing import Optional

from ..core.state_manager import get_state_manager, StateKeys
from ..core.navigation import get_navigation, PageName
from ..core.authentication import get_auth_manager


# ============================================================================
# LOGO AND BRANDING
# ============================================================================

def _render_logo() -> None:
    """Render the SIRAYA logo - Visual Parity with frontend.py."""
    import streamlit.components.v1 as components
    
    html = """
    <div style="text-align: center; padding: 20px 0;">
        <div style="font-size: 2.2em; font-weight: 300; letter-spacing: 0.15em; color: #4A90E2;">
            SIRAYA
        </div>
        <div style="font-size: 0.85em; color: #6b7280; margin-top: 5px;">
            Health Navigator
        </div>
        <div style="margin-top: 10px; font-size: 1.5em;">
            🩺
        </div>
    </div>
    """
    
    # Use components.html() for reliable HTML rendering
    components.html(html, height=150)


# ============================================================================
# NAVIGATION
# ============================================================================

def _render_navigation() -> str:
    """
    Render navigation radio buttons.
    
    Returns:
        Selected page name ("CHAT" or "DASHBOARD")
    """
    page_options = [
        "🤖 Chatbot Triage",
        "📊 Analytics Dashboard",
    ]
    
    # Get current page to set default
    nav = get_navigation()
    current = nav.current_page
    default_idx = 1 if current == "DASHBOARD" else 0
    
    selected = st.radio(
        "🧭 Navigazione",
        page_options,
        index=default_idx,
        label_visibility="collapsed"
    )
    
    # Map selection to page name
    if "Analytics" in selected:
        return "DASHBOARD"
    else:
        return "CHAT"


def _render_extended_navigation() -> str:
    """
    Render extended navigation with Map and Report options.
    
    Returns:
        Selected page name
    """
    page_options = [
        "🤖 Chatbot Triage",
        "🗺️ Mappa Strutture",
        "📋 Report SBAR",
        "📊 Analytics Dashboard",
    ]
    
    nav = get_navigation()
    current = nav.current_page
    
    # Map current page to index
    page_to_idx = {
        "CHAT": 0,
        "MAP": 1,
        "REPORT": 2,
        "DASHBOARD": 3,
    }
    default_idx = page_to_idx.get(current, 0)
    
    selected = st.radio(
        "🧭 Navigazione",
        page_options,
        index=default_idx,
        label_visibility="collapsed"
    )
    
    # Map selection to page name
    if "Analytics" in selected:
        return "DASHBOARD"
    elif "Mappa" in selected:
        return "MAP"
    elif "Report" in selected:
        return "REPORT"
    else:
        return "CHAT"


# ============================================================================
# PRIVACY CONSENT
# ============================================================================

def _render_privacy_checkbox() -> None:
    """Render privacy consent checkbox."""
    auth = get_auth_manager()
    state = get_state_manager()
    
    current_value = auth.is_privacy_accepted()
    
    accept = st.checkbox(
        "✅ Accetto l'informativa privacy",
        value=current_value,
        key="sidebar_privacy_checkbox"
    )
    
    if accept and not current_value:
        auth.accept_privacy()
        st.rerun()
    elif not accept and current_value:
        auth.revoke_privacy()
        st.rerun()


# ============================================================================
# TRIAGE PROGRESS
# ============================================================================

def _render_progress() -> None:
    """Render triage progress bar - Visual Parity with frontend.py step tracker."""
    state = get_state_manager()
    
    # Calculate progress based on phase
    phase_progress = {
        "INTENT_DETECTION": 0,
        "LOCATION": 15,
        "CHIEF_COMPLAINT": 30,
        "PAIN_ASSESSMENT": 45,
        "RED_FLAGS": 60,
        "DEMOGRAPHICS": 75,
        "ANAMNESIS": 85,
        "DISPOSITION": 100,
    }
    
    current_phase = state.get(StateKeys.CURRENT_PHASE, "INTENT_DETECTION")
    progress = phase_progress.get(current_phase, 0)
    
    st.markdown("**📊 Progresso Triage**")
    st.progress(progress / 100)
    
    # Human-readable phase name
    phase_names = {
        "INTENT_DETECTION": "Identificazione",
        "LOCATION": "Localizzazione",
        "CHIEF_COMPLAINT": "Sintomo",
        "PAIN_ASSESSMENT": "Dolore",
        "RED_FLAGS": "Allarmi",
        "DEMOGRAPHICS": "Dati",
        "ANAMNESIS": "Anamnesi",
        "DISPOSITION": "Esito",
    }
    
    phase_display = phase_names.get(current_phase, current_phase)
    st.caption(f"Fase: {phase_display}")


# ============================================================================
# SYSTEM STATUS
# ============================================================================

def _render_system_status() -> None:
    """Render system connection status with detailed diagnostics."""
    st.markdown("**📡 Stato Sistema**")
    
    # Check Supabase connection
    try:
        from ..config.settings import SupabaseConfig
        
        if not SupabaseConfig.is_configured():
            st.error("❌ Supabase: Credenziali mancanti")
            with st.expander("🔍 Debug Info"):
                st.code(f"URL configurato: {bool(SupabaseConfig.get_url())}")
                st.code(f"KEY configurato: {bool(SupabaseConfig.get_key())}")
            return
        
        # Test connection
        try:
            from supabase import create_client
            client = create_client(
                SupabaseConfig.get_url(),
                SupabaseConfig.get_key()
            )
            
            # Test query
            result = client.table(SupabaseConfig.TABLE_LOGS).select("session_id").limit(1).execute()
            
            if result.data is not None:  # Even empty list [] is valid
                st.success(f"✅ Database Connesso")
                if len(result.data) > 0:
                    st.caption(f"Ultimo test: OK")
            else:
                st.warning("⚠️ Database connesso ma query fallita")
                
        except Exception as e:
            st.error(f"❌ Database Error: {type(e).__name__}")
            with st.expander("🔍 Error Details"):
                st.code(str(e))
    
    except Exception as e:
        st.error(f"❌ Config Error: {str(e)[:50]}")
    
    # Check LLM availability
    try:
        from ..services.llm_service import get_llm_service
        llm = get_llm_service()
        
        if llm.is_available():
            st.success("✅ AI Disponibile")
        else:
            st.warning("⚠️ AI Non Configurata")
    except:
        st.error("❌ Servizio AI non disponibile")


# ============================================================================
# COLLECTED DATA PREVIEW
# ============================================================================

def _render_collected_data_preview() -> None:
    """Show preview of collected triage data."""
    state = get_state_manager()
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    if collected:
        with st.expander("📋 Dati Raccolti", expanded=False):
            for key, value in collected.items():
                if value:
                    # Format key
                    display_key = key.replace("_", " ").title()
                    
                    # Format value
                    if isinstance(value, list):
                        display_value = ", ".join(str(v) for v in value)
                    else:
                        display_value = str(value)
                    
                    st.caption(f"**{display_key}:** {display_value[:50]}")


# ============================================================================
# ADMIN SECTION
# ============================================================================

def render_admin_section() -> None:
    """Render admin section in sidebar (if logged in)."""
    auth = get_auth_manager()
    
    if auth.is_admin_logged_in():
        st.divider()
        st.markdown("**👤 Admin**")
        st.write(f"Logged in: {auth.get_admin_username()}")
        
        if st.button("🚪 Logout", use_container_width=True):
            auth.admin_logout()
            st.rerun()


# ============================================================================
# RESET BUTTON
# ============================================================================

def render_reset_button() -> None:
    """Render session reset button."""
    st.divider()
    
    if st.button("🔄 Nuova Sessione", use_container_width=True):
        state = get_state_manager()
        state.reset_triage()
        st.rerun()


# ============================================================================
# MAIN RENDER FUNCTION
# ============================================================================

def render() -> str:
    """
    Render the complete sidebar.
    
    Returns:
        Selected page name
    """
    # Logo and branding
    _render_logo()
    
    st.divider()
    
    # Navigation
    selected_page = _render_extended_navigation()
    
    st.divider()
    
    # Privacy consent
    _render_privacy_checkbox()
    
    st.divider()
    
    # Progress bar (for chat view only)
    nav = get_navigation()
    if nav.is_current(PageName.CHAT) or selected_page == "CHAT":
        _render_progress()
        st.divider()
    
    # Collected data preview
    _render_collected_data_preview()
    
    st.divider()
    
    # System status
    _render_system_status()
    
    # Admin section
    render_admin_section()
    
    return selected_page
