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
    st.markdown("""
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
    """, unsafe_allow_html=True)


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
    """Render system connection status."""
    st.markdown("**📡 Stato Sistema**")
    
    # Check Database connection (usando nuovo db_service)
    try:
        from ..services.db_service import get_db_service
        
        db = get_db_service()
        status_msg = db.get_status_message()
        
        if "✅" in status_msg:
            st.success(status_msg)
        elif "💾" in status_msg:
            st.info(status_msg)
        else:
            st.warning(status_msg)
            
    except Exception as e:
        st.error(f"❌ Errore DB: {str(e)[:30]}")
    
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
    """
    Visualizza 5 box: Località, Sintomo, Dolore, Anamnesi, Esito.
    Update SOLO quando il valore cambia (dirty checking).
    """
    from ..core.state_manager import get_state_manager, StateKeys
    from ..core.event_store import get_event_store
    import hashlib
    import logging
    
    logger = logging.getLogger(__name__)
    
    state = get_state_manager()
    event_store = get_event_store()
    event_store = get_event_store()
    
    # ✅ Ricostruisci dati da eventi (source of truth)
    collected = event_store.get_collected_data_from_events()
    current_phase = event_store.get_current_phase_from_events()
    
    # Fallback a session state se eventi non disponibili (backward compatibility)
    if not collected:
        collected = state.get(StateKeys.COLLECTED_DATA, {})
    if current_phase == "intake" and not event_store.get_events():
        current_phase = state.get(StateKeys.CURRENT_PHASE, "intake")
    
    # Tracking stato precedente
    last_state = state.get(StateKeys.INFO_BOXES_LAST_STATE, {})
    current_state = {}
    
    st.markdown("### 📋 Dati Raccolti")
    
    # ===== BOX 1: LOCALITÀ =====
    location = collected.get('location') or collected.get('current_location')
    location_hash = hashlib.md5(str(location).encode()).hexdigest() if location else None
    
    if location_hash and location_hash != last_state.get('location'):
        current_state['location'] = location_hash
        logger.info(f"📍 Box Località aggiornata: {location}")
    elif location_hash:
        current_state['location'] = location_hash  # Mantieni hash
    
    # Colore: verde se completo, warning se mancante
    if location:
        st.success(f"📍 **Località:** {location}")
    else:
        st.warning("📍 **Località:** ⏳ In raccolta...")
    
    # ===== BOX 2: SINTOMO =====
    symptom = collected.get('main_symptom') or collected.get('chief_complaint')
    symptom_hash = hashlib.md5(str(symptom).encode()).hexdigest() if symptom else None
    
    if symptom_hash and symptom_hash != last_state.get('symptom'):
        current_state['symptom'] = symptom_hash
        logger.info(f"🩺 Box Sintomo aggiornata: {symptom[:30]}")
    elif symptom_hash:
        current_state['symptom'] = symptom_hash
    
    if symptom:
        st.success(f"🩺 **Sintomo:** {symptom[:50]}")
    else:
        st.warning("🩺 **Sintomo:** ⏳ In raccolta...")
    
    # ===== BOX 3: DOLORE =====
    pain = collected.get('pain_scale')
    pain_hash = hashlib.md5(str(pain).encode()).hexdigest() if pain else None
    
    if pain_hash and pain_hash != last_state.get('pain'):
        current_state['pain'] = pain_hash
        logger.info(f"📊 Box Dolore aggiornata: {pain}/10")
    elif pain_hash:
        current_state['pain'] = pain_hash
    
    if pain:
        pain_val = int(pain)
        st.success("📊 **Dolore:**")
        st.progress(pain_val / 10)
        st.caption(f"Intensità: {pain_val}/10")
    else:
        st.warning("📊 **Dolore:** Non valutato")
    
    # ===== BOX 4: ANAMNESI =====
    # Conta domande cliniche dalla event store
    clinical_count = event_store.count_questions_in_phase("clinical_triage")
    fast_count = event_store.count_questions_in_phase("fast_triage")
    risk_count = event_store.count_questions_in_phase("risk_assessment")
    
    total_clinical = clinical_count + fast_count + risk_count
    
    anamnesi_value = None
    if current_phase in ["clinical_triage", "fast_triage", "risk_assessment"] and total_clinical > 0:
        anamnesi_value = f"{total_clinical} domande"
    elif current_phase in ["outcome", "sbar"]:
        anamnesi_value = "✅ Completata"
    
    anamnesi_hash = hashlib.md5(str(anamnesi_value).encode()).hexdigest() if anamnesi_value else None
    
    if anamnesi_hash and anamnesi_hash != last_state.get('anamnesi'):
        current_state['anamnesi'] = anamnesi_hash
        logger.info(f"📋 Box Anamnesi aggiornata: {anamnesi_value}")
    elif anamnesi_hash:
        current_state['anamnesi'] = anamnesi_hash
    
    # Mostra anamnesi (età + genere + contatore se in fase clinica)
    age = collected.get('age')
    gender = collected.get('gender') or collected.get('sex')
    
    if age:
        anamnesi_text = f"{age} anni"
        if gender:
            anamnesi_text += f", {gender}"
        if anamnesi_value:
            anamnesi_text += f" | {anamnesi_value}"
        st.success(f"👤 **Anamnesi:** {anamnesi_text}")
    elif anamnesi_value:
        st.info(f"📋 **Anamnesi:** {anamnesi_value}")
    else:
        st.warning("👤 **Anamnesi:** In corso...")
    
    # ===== BOX 5: ESITO =====
    outcome_value = None
    outcome_color = "warning"  # Default giallo
    
    if current_phase == "outcome":
        outcome_value = "✅ Raccomandazione pronta"
        outcome_color = "success"  # ✅ Verde
    elif current_phase == "sbar":
        outcome_value = "✅ Report completo"
        outcome_color = "success"  # ✅ Verde
    elif current_phase in ["clinical_triage", "fast_triage", "risk_assessment"]:
        outcome_value = "⏳ In elaborazione..."
        outcome_color = "info"  # Blu
    else:
        outcome_value = "⏳ In attesa..."
        outcome_color = "warning"  # Giallo
    
    outcome_hash = hashlib.md5(str(outcome_value).encode()).hexdigest()
    
    if outcome_hash != last_state.get('outcome'):
        current_state['outcome'] = outcome_hash
        logger.info(f"🏥 Box Esito aggiornata: {outcome_value} (color: {outcome_color})")
    else:
        current_state['outcome'] = outcome_hash
    
    # Render con colore dinamico
    if outcome_color == "success":
        st.success(f"🏥 **Esito:** {outcome_value}")
    elif outcome_color == "info":
        st.info(f"🏥 **Esito:** {outcome_value}")
    else:
        st.warning(f"🏥 **Esito:** {outcome_value}")
    
    # Salva stato corrente per prossima iterazione
    state.set(StateKeys.INFO_BOXES_LAST_STATE, current_state)


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
    
    # Privacy consent checkbox removed - now handled by central button in main view
    # _render_privacy_checkbox()  # REMOVED - no longer needed
    
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
