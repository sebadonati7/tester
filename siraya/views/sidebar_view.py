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
    
    # ✅ V3: Usa session state direttamente (più semplice, no event store)
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    current_phase = state.get(StateKeys.CURRENT_PHASE, "intake")
    phase_q = state.get("phase_question_count", 0)  # ✅ V3: Counter unico
    
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
    
    # ===== BOX 2: SINTOMO (ORIGINALE + dettagli) =====
    symptom_original = collected.get('chief_complaint')  # ✅ V3: Chiave canonica unica
    symptom_details = collected.get('symptom_details', [])
    
    if symptom_original:
        if symptom_details:
            symptom_display = f"{symptom_original} ({', '.join(symptom_details)})"
        else:
            symptom_display = symptom_original
        symptom_hash = hashlib.md5(symptom_display.encode()).hexdigest()
    else:
        symptom_display = None
        symptom_hash = None
    
    if symptom_hash and symptom_hash != last_state.get('symptom'):
        current_state['symptom'] = symptom_hash
        logger.info(f"🩺 Box Sintomo aggiornata: {symptom_display[:30]}")
    elif symptom_hash:
        current_state['symptom'] = symptom_hash
    
    if symptom_display:
        st.success(f"🩺 **Sintomo:** {symptom_display[:60]}")
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
    
    # ===== BOX 4: ANAMNESI + COUNTER ===
    # ✅ NUOVO: Mostra conteggio domande SE in fase clinica
    age = collected.get('age')
    gender = collected.get('gender') or collected.get('sex')
    
    if current_phase.upper() in ["CLINICAL_TRIAGE", "FAST_TRIAGE", "RISK_ASSESSMENT"]:
        # Determina target domande per branch
        branch = state.get(StateKeys.TRIAGE_BRANCH, "STANDARD")
        target_questions = {
            "EMERGENCY": "3-4",
            "MENTAL_HEALTH": "4-5",
            "STANDARD": "5-7"
        }.get(branch, "5-7")
        
        anamnesi_text = f"📋 **Anamnesi:** {phase_q} domande (target: {target_questions})"
        anamnesi_hash = hashlib.md5(f"{phase_q}_{branch}".encode()).hexdigest()
        
        # Progress bar per domande
        if branch == "EMERGENCY":
            progress_val = min(phase_q / 4, 1.0)
        elif branch == "MENTAL_HEALTH":
            progress_val = min(phase_q / 5, 1.0)
        else:
            progress_val = min(phase_q / 7, 1.0)
        
        if anamnesi_hash != last_state.get('anamnesi'):
            current_state['anamnesi'] = anamnesi_hash
            logger.info(f"📋 Box Anamnesi: {phase_q} domande (target: {target_questions})")
        elif anamnesi_hash:
            current_state['anamnesi'] = anamnesi_hash
        
        st.info(anamnesi_text)
        st.progress(progress_val)
        
        # Mostra anche età/genere se disponibili
        if age:
            st.caption(f"Età: {age} anni" + (f", {gender}" if gender else ""))
        
    elif current_phase.upper() == "OUTCOME":
        st.success("📋 **Anamnesi:** ✅ Completata")
        if age:
            st.caption(f"Età: {age} anni" + (f", {gender}" if gender else ""))
    else:
        st.warning("📋 **Anamnesi:** In attesa...")
        if age:
            st.caption(f"Età: {age} anni" + (f", {gender}" if gender else ""))
    
    # ===== BOX 5: ESITO ===
    if current_phase.upper() == "OUTCOME":
        outcome_value = "✅ Raccomandazione pronta"
        outcome_color = "success"  # ✅ VERDE
    elif current_phase.upper() in ["CLINICAL_TRIAGE", "FAST_TRIAGE", "RISK_ASSESSMENT"]:
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
