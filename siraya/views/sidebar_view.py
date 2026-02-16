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
    import hashlib
    import logging
    
    logger = logging.getLogger(__name__)
    
    state = get_state_manager()
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    current_phase = state.get(StateKeys.CURRENT_PHASE, "intake")
    
    # Recupera stato precedente delle box
    last_state = state.get(StateKeys.INFO_BOXES_LAST_STATE, {})
    current_state = {}
    
    st.markdown("### 📋 Dati Raccolti")
    
    # === BOX 1: LOCALITÀ ===
    location = collected.get('current_location') or collected.get('location') or collected.get('patient_location')
    location_hash = hashlib.md5(str(location).encode()).hexdigest() if location else None
    
    # Aggiorna SOLO se cambiato
    if location_hash != last_state.get('location'):
        current_state['location'] = location_hash
        if location:
            logger.info(f"📍 Box Località aggiornata: {location}")
    
    st.success(f"📍 **Località:** {location if location else '⏳ In raccolta...'}")
    
    # === BOX 2: SINTOMO ===
    symptom = collected.get('chief_complaint') or collected.get('CHIEF_COMPLAINT') or collected.get('main_symptom')
    symptom_hash = hashlib.md5(str(symptom).encode()).hexdigest() if symptom else None
    
    if symptom_hash != last_state.get('symptom'):
        current_state['symptom'] = symptom_hash
        if symptom:
            logger.info(f"🩺 Box Sintomo aggiornata: {symptom[:30]}")
    
    st.info(f"🩺 **Sintomo:** {symptom[:50] if symptom else '⏳ In raccolta...'}")
    
    # === BOX 3: DOLORE ===
    pain = collected.get('pain_scale') or collected.get('PAIN_SCALE')
    pain_hash = hashlib.md5(str(pain).encode()).hexdigest() if pain else None
    
    if pain_hash != last_state.get('pain'):
        current_state['pain'] = pain_hash
        if pain:
            logger.info(f"📊 Box Dolore aggiornata: {pain}/10")
    
    if pain:
        try:
            pain_val = int(pain)
            st.progress(pain_val / 10)
            st.caption(f"📊 Intensità: {pain_val}/10")
        except:
            st.warning(f"📊 **Dolore:** {pain}")
    else:
        st.warning("📊 **Dolore:** Non valutato")
    
    # === BOX 4: ANAMNESI ===
    # Mostra contatore SOLO in fase CLINICAL_TRIAGE
    clinical_count = state.get(StateKeys.QUESTION_COUNT_CLINICAL, 0)  # ✅ Usa nuovo counter
    anamnesi_value = None
    
    if current_phase == "clinical_triage" and clinical_count > 0:
        anamnesi_value = f"{clinical_count} domande"
    elif current_phase in ("outcome", "sbar"):
        anamnesi_value = "Completata"
    
    anamnesi_hash = hashlib.md5(str(anamnesi_value).encode()).hexdigest() if anamnesi_value else None
    
    if anamnesi_hash != last_state.get('anamnesi'):
        current_state['anamnesi'] = anamnesi_hash
        if anamnesi_value:
            logger.info(f"📋 Box Anamnesi aggiornata: {anamnesi_value}")
    
    # Mostra anamnesi (età + genere + contatore se in fase clinica)
    age = collected.get('age') or collected.get('patient_age')
    gender = collected.get('gender') or collected.get('sex') or collected.get('patient_sex')
    
    if age:
        anamnesi_text = f"{age} anni"
        if gender:
            anamnesi_text += f", {gender}"
        if anamnesi_value:
            anamnesi_text += f" | {anamnesi_value}"
        st.success(f"👤 **Anamnesi:** {anamnesi_text}")
    else:
        if anamnesi_value:
            st.info(f"📋 **Anamnesi:** {anamnesi_value}")
        else:
            st.warning("👤 **Anamnesi:** Incompleta")
    
    # === BOX 5: ESITO ===
    # Mostra SOLO se in fase OUTCOME o SBAR_GENERATION
    outcome_value = None
    if current_phase == "outcome":
        outcome_value = "✅ Disponibile"
    elif current_phase == "sbar":
        outcome_value = "✅ Report pronto"
    
    outcome_hash = hashlib.md5(str(outcome_value).encode()).hexdigest() if outcome_value else None
    
    if outcome_hash != last_state.get('outcome'):
        current_state['outcome'] = outcome_hash
        if outcome_value:
            logger.info(f"✅ Box Esito aggiornata: {outcome_value}")
    
    if outcome_value:
        st.success(f"✅ **Esito:** {outcome_value}")
    else:
        st.warning("⏳ **Esito:** In attesa...")
    
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
