"""
SIRAYA Health Navigator - Chat View
V2.0: Visual Parity with legacy frontend.py

This view:
- Renders chat message history with avatars
- Handles user input via TriageController
- Shows AI responses with streaming effect
- Displays survey buttons (A/B/C options)
- Includes TTS (Text-to-Speech) support
"""

import streamlit as st
from typing import Optional, List, Dict, Any
import time

from ..core.state_manager import get_state_manager, StateKeys
from ..core.authentication import check_privacy_accepted, render_privacy_consent
from ..controllers.triage_controller import get_triage_controller


# ============================================================================
# CONSTANTS (Visual Parity)
# ============================================================================

BOT_AVATAR = "🩺"
USER_AVATAR = "👤"


# ============================================================================
# TEXT-TO-SPEECH COMPONENT
# ============================================================================

def text_to_speech_button(text: str, key: str, auto_play: bool = False) -> None:
    """
    Render TTS button for message audio playback.
    
    Uses browser's SpeechSynthesis API (no server-side audio).
    
    Args:
        text: Text to speak
        key: Unique key for button
        auto_play: Whether to auto-play (requires user interaction first)
    """
    # Sanitize text for JavaScript
    safe_text = text.replace("'", "\\'").replace("\n", " ").replace('"', '\\"')
    safe_text = safe_text[:1000]  # Limit length for TTS
    
    tts_html = f"""
    <script>
    function speakText_{key.replace('-', '_')}() {{
        if ('speechSynthesis' in window) {{
            // Cancel any ongoing speech
            window.speechSynthesis.cancel();
            
            const utterance = new SpeechSynthesisUtterance("{safe_text}");
            utterance.lang = 'it-IT';
            utterance.rate = 0.95;
            utterance.pitch = 1.0;
            
            // Get Italian voice if available
            const voices = window.speechSynthesis.getVoices();
            const italianVoice = voices.find(v => v.lang.startsWith('it'));
            if (italianVoice) {{
                utterance.voice = italianVoice;
            }}
            
            window.speechSynthesis.speak(utterance);
        }} else {{
            console.warn('TTS not supported in this browser');
        }}
    }}
    </script>
    <button onclick="speakText_{key.replace('-', '_')}()" 
            style="background: transparent; border: 1px solid #e5e7eb; 
                   border-radius: 6px; padding: 4px 8px; cursor: pointer;
                   font-size: 0.8em; color: #6b7280; margin-top: 4px;
                   transition: all 0.2s;">
        🔊 Ascolta
    </button>
    """
    
    st.markdown(tts_html, unsafe_allow_html=True)


# ============================================================================
# STEP TRACKER COMPONENT
# ============================================================================

def render_step_tracker() -> None:
    """Render dynamic step tracker showing triage progress."""
    controller = get_triage_controller()
    state = get_state_manager()
    
    current_phase = state.get(StateKeys.CURRENT_PHASE, "INTENT_DETECTION")
    completion = controller.get_completion_percentage()
    
    # Step definitions with icons
    steps = [
        ("LOCATION", "📍", "Dove"),
        ("CHIEF_COMPLAINT", "🩺", "Sintomo"),
        ("PAIN_ASSESSMENT", "📊", "Dolore"),
        ("RED_FLAGS", "🚨", "Allarmi"),
        ("DEMOGRAPHICS", "👤", "Dati"),
        ("DISPOSITION", "🏥", "Esito"),
    ]
    
    # Create step tracker HTML
    step_html = """
    <div style="display: flex; justify-content: space-around; 
                padding: 10px 0; margin-bottom: 15px;
                background: linear-gradient(135deg, #f8fafc 0%, #e3f2fd 100%);
                border-radius: 10px;">
    """
    
    phase_order = [s[0] for s in steps]
    current_idx = phase_order.index(current_phase) if current_phase in phase_order else -1
    
    for idx, (phase, icon, label) in enumerate(steps):
        if idx < current_idx:
            # Completed
            bg_color = "#10B981"
            text_color = "white"
            opacity = "1"
        elif idx == current_idx:
            # Current
            bg_color = "#4A90E2"
            text_color = "white"
            opacity = "1"
        else:
            # Pending
            bg_color = "#e5e7eb"
            text_color = "#9ca3af"
            opacity = "0.6"
        
        step_html += f"""
        <div style="text-align: center; opacity: {opacity};">
            <div style="width: 36px; height: 36px; border-radius: 50%;
                        background-color: {bg_color}; color: {text_color};
                        display: flex; align-items: center; justify-content: center;
                        margin: 0 auto 4px auto; font-size: 1.1em;">
                {icon}
            </div>
            <div style="font-size: 0.7em; color: {text_color};">{label}</div>
        </div>
        """
    
    step_html += "</div>"
    
    st.markdown(step_html, unsafe_allow_html=True)


# ============================================================================
# SURVEY BUTTONS COMPONENT
# ============================================================================

def render_survey_buttons(options: List[str], question_key: str) -> Optional[str]:
    """
    Render A/B/C survey option buttons.
    
    Args:
        options: List of option strings
        question_key: Unique key for this question
        
    Returns:
        Selected option text or None
    """
    if not options:
        return None
    
    st.markdown("---")
    st.caption("Seleziona un'opzione:")
    
    cols = st.columns(len(options))
    
    for i, (col, opt) in enumerate(zip(cols, options)):
        with col:
            # Create unique key
            btn_key = f"survey_btn_{question_key}_{i}"
            
            if st.button(opt, key=btn_key, use_container_width=True):
                return opt
    
    return None


# ============================================================================
# DISCLAIMER COMPONENT
# ============================================================================

def render_disclaimer() -> None:
    """Render medical disclaimer notice."""
    st.markdown("""
    <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; 
                padding: 12px 16px; border-radius: 0 8px 8px 0; margin-bottom: 20px;">
        <strong>⚠️ Nota Importante</strong><br>
        <small>
        SIRAYA è un assistente di supporto al triage, <strong>non sostituisce</strong> 
        il parere medico. In caso di emergenza, chiama immediatamente il <strong>118</strong>.
        </small>
    </div>
    """, unsafe_allow_html=True)


# ============================================================================
# MAIN RENDER FUNCTION
# ============================================================================

def render() -> None:
    """
    Render the chat interface.
    
    Main entry point for the chat view.
    Visual Parity with legacy frontend.py.
    """
    # Get services
    state = get_state_manager()
    controller = get_triage_controller()
    
    # Check privacy (shows consent form if not accepted)
    if not check_privacy_accepted():
        render_privacy_consent()
        return
    
    # === HEADER ===
    st.markdown("""
    <div style="text-align: center; padding: 10px 0;">
        <h1 style="color: #4A90E2; font-weight: 300; letter-spacing: 0.1em; margin: 0;">
            🏥 SIRAYA Health Navigator
        </h1>
        <p style="color: #6b7280; font-size: 0.9em; margin-top: 5px;">
            Assistente Intelligente per la Navigazione Sanitaria
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # === DISCLAIMER ===
    render_disclaimer()
    
    # === STEP TRACKER ===
    render_step_tracker()
    
    st.markdown("---")
    
    # === CHECK LLM AVAILABILITY ===
    llm = controller.llm
    if not llm.is_available():
        st.error("❌ Servizio AI non disponibile. Riprova più tardi.")
        st.info("💡 Verifica che le chiavi API siano configurate in `st.secrets`.")
        return
    
    # === RENDER CHAT HISTORY ===
    messages = state.get(StateKeys.MESSAGES, [])
    auto_speech = st.session_state.get('auto_speech', False)
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Select avatar based on role
        avatar = BOT_AVATAR if role == "assistant" else USER_AVATAR
        
        with st.chat_message(role, avatar=avatar):
            st.markdown(content)
            
            # TTS button for assistant messages
            if role == "assistant":
                is_last_message = (i == len(messages) - 1)
                auto_play = auto_speech and is_last_message
                
                text_to_speech_button(
                    text=content,
                    key=f"tts_msg_{i}",
                    auto_play=auto_play
                )
    
    # === CHECK IF DISPOSITION COMPLETE ===
    current_phase = state.get(StateKeys.CURRENT_PHASE, "")
    if current_phase == "DISPOSITION" and messages:
        _render_disposition_summary()
        return
    
    # === RENDER PENDING SURVEY OPTIONS ===
    pending_options = controller.get_survey_options()
    if pending_options:
        selected = render_survey_buttons(
            pending_options,
            f"phase_{state.get(StateKeys.CURRENT_PHASE, 'init')}"
        )
        
        if selected:
            # User clicked a survey button
            _process_user_input(selected, controller, state)
            controller.clear_survey_options()
            st.rerun()
        
        # Still show text input as alternative
        st.caption("💡 Oppure scrivi liberamente:")
    
    # === CHAT INPUT ===
    if prompt := st.chat_input("Ciao, come posso aiutarti oggi?"):
        _process_user_input(prompt, controller, state)
        st.rerun()


def _process_user_input(
    user_input: str,
    controller,
    state
) -> None:
    """
    Process user input through the controller.
    
    Args:
        user_input: User's message
        controller: TriageController instance
        state: StateManager instance
    """
    # Add user message to history
    state.add_message("user", user_input)
    
    # Get session ID
    session_id = state.get(StateKeys.SESSION_ID, "unknown")
    
    # Process through controller (handles LLM call + Supabase logging)
    with st.spinner("Analisi in corso..."):
        response, metadata = controller.handle_user_input(user_input, session_id)
    
    # Add assistant response to history
    state.add_message("assistant", response)
    
    # Check if response has survey options
    if metadata.get("opzioni"):
        controller.set_survey_options(metadata["opzioni"])
    
    # Check for emergency - show alert
    if metadata.get("is_emergency"):
        emergency_type = metadata.get("emergency_type", "")
        
        if emergency_type == "CRITICAL":
            st.error("🚨 **EMERGENZA RILEVATA** - Chiama immediatamente il **118**")
        elif emergency_type == "MENTAL_HEALTH":
            st.warning("⚫ **Supporto Urgente** - Contatta il numero verde **800.274.274**")


def _render_disposition_summary() -> None:
    """Render final disposition summary with SBAR report."""
    state = get_state_manager()
    
    st.markdown("---")
    st.success("✅ **Triage Completato**")
    
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    urgency = state.get(StateKeys.URGENCY_LEVEL, 3)
    
    # SBAR Summary
    st.markdown("### 📋 Riepilogo SBAR")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**S - Situazione**")
        st.write(f"Sintomo: {collected.get('CHIEF_COMPLAINT', 'N/D')}")
        st.write(f"Dolore: {collected.get('PAIN_SCALE', 'N/D')}/10")
        
        st.markdown("**B - Background**")
        st.write(f"Età: {collected.get('age', 'N/D')}")
        st.write(f"Località: {collected.get('LOCATION', 'N/D')}")
    
    with col2:
        st.markdown("**A - Assessment**")
        urgency_labels = {1: "🟢 Verde", 2: "🟡 Giallo", 3: "🟠 Arancione", 4: "🔴 Rosso", 5: "⚫ Critico"}
        st.write(f"Urgenza: {urgency_labels.get(urgency, urgency)}")
        
        red_flags = collected.get('RED_FLAGS', [])
        st.write(f"Red Flags: {', '.join(red_flags) if red_flags else 'Nessuno'}")
        
        st.markdown("**R - Raccomandazione**")
        st.write(f"Path: {state.get(StateKeys.TRIAGE_PATH, 'C')}")
    
    # Action buttons
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🗺️ Trova Struttura", use_container_width=True):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.MAP)
    
    with col2:
        if st.button("📄 Genera PDF", use_container_width=True):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.REPORT)
    
    with col3:
        if st.button("🔄 Nuovo Triage", use_container_width=True):
            controller = get_triage_controller()
            controller.reset_triage()
            st.rerun()


# ============================================================================
# QUICK ACTIONS
# ============================================================================

def render_quick_actions() -> None:
    """Render quick action buttons below chat."""
    st.markdown("### 🔧 Azioni Rapide")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🔄 Nuovo Triage", use_container_width=True, key="qa_reset"):
            controller = get_triage_controller()
            controller.reset_triage()
            st.rerun()
    
    with col2:
        if st.button("📋 Genera Report", use_container_width=True, key="qa_report"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.REPORT)
    
    with col3:
        if st.button("🗺️ Vedi Mappa", use_container_width=True, key="qa_map"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.MAP)
