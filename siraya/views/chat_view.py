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
    """
    Render 5 quadrati per mostrare lo stato di raccolta dati:
    LOCALIZZAZIONE, SINTOMO PRINCIPALE, DOLORE, ANAMNESI, ESITO
    """
    state = get_state_manager()
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    current_phase = state.get(StateKeys.CURRENT_PHASE, "INTAKE")
    
    # Definizione dei 5 quadrati
    squares = [
        {
            "key": "LOCALIZZAZIONE",
            "icon": "📍",
            "data_key": "current_location",
            "alt_keys": ["location", "patient_location"]
        },
        {
            "key": "SINTOMO PRINCIPALE",
            "icon": "🩺",
            "data_key": "chief_complaint",
            "alt_keys": ["CHIEF_COMPLAINT"]
        },
        {
            "key": "DOLORE",
            "icon": "😣",
            "data_key": "pain_scale",
            "alt_keys": ["PAIN_SCALE"]
        },
        {
            "key": "ANAMNESI",
            "icon": "📋",
            "data_key": "anamnesis",
            "alt_keys": ["question_count"]  # Considerato completo se question_count > 0
        },
        {
            "key": "ESITO",
            "icon": "🏥",
            "data_key": "disposition",
            "alt_keys": ["DISPOSITION", "recommendation"]
        }
    ]
    
    # Funzione per verificare se un quadrato è completato
    def is_completed(square):
        # Controlla la chiave principale
        if collected.get(square["data_key"]):
            return True
        
        # Controlla chiavi alternative
        for alt_key in square["alt_keys"]:
            if collected.get(alt_key) or state.get(alt_key):
                return True
        
        # Caso speciale per ANAMNESI: completato se ci sono state domande
        if square["key"] == "ANAMNESI":
            question_count = state.get(StateKeys.QUESTION_COUNT, 0)
            if question_count > 0:
                return True
        
        # Caso speciale per ESITO: completato se siamo in fase RECOMMENDATION
        if square["key"] == "ESITO":
            if current_phase in ("RECOMMENDATION", "DISPOSITION"):
                return True
        
        return False
    
    # Funzione per ottenere il valore da mostrare
    def get_value(square):
        # Prova chiave principale
        value = collected.get(square["data_key"])
        if value:
            return str(value)[:20]  # Limita lunghezza
        
        # Prova chiavi alternative
        for alt_key in square["alt_keys"]:
            value = collected.get(alt_key) or state.get(alt_key)
            if value:
                return str(value)[:20]
        
        return None
    
    # Render dei 5 quadrati
    with st.container():
        cols = st.columns(5)
        
        for idx, square in enumerate(squares):
            with cols[idx]:
                completed = is_completed(square)
                value = get_value(square) if completed else None
                
                # Stile del quadrato
                if completed:
                    # Quadrato completato (verde con bordo)
                    st.markdown(f"""
                    <div style="
                        border: 3px solid #10B981;
                        border-radius: 8px;
                        padding: 12px;
                        text-align: center;
                        background-color: #f0fdf4;
                        min-height: 100px;
                    ">
                        <div style="font-size: 1.5em; margin-bottom: 5px;">{square['icon']}</div>
                        <div style="font-weight: bold; font-size: 0.75em; color: #059669;">{square['key']}</div>
                        {f'<div style="font-size: 0.65em; color: #047857; margin-top: 5px;">{value}</div>' if value else ''}
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    # Quadrato vuoto (grigio)
                    st.markdown(f"""
                    <div style="
                        border: 2px dashed #d1d5db;
                        border-radius: 8px;
                        padding: 12px;
                        text-align: center;
                        background-color: #f9fafb;
                        min-height: 100px;
                        opacity: 0.6;
                    ">
                        <div style="font-size: 1.5em; margin-bottom: 5px;">{square['icon']}</div>
                        <div style="font-weight: bold; font-size: 0.75em; color: #6b7280;">{square['key']}</div>
                    </div>
                    """, unsafe_allow_html=True)


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
    """Render medical disclaimer notice using native Streamlit."""
    st.warning("⚠️ **Nota Importante:** SIRAYA è un assistente di supporto al triage, **non sostituisce** il parere medico. In caso di emergenza, chiama immediatamente il **118**.")


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
    st.markdown("### 🏥 SIRAYA Health Navigator")
    st.caption("Assistente Intelligente per la Navigazione Sanitaria")
    
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
    if current_phase in ("DISPOSITION", "RECOMMENDATION") and messages:
        _render_disposition_summary()
        # Non bloccare l'input, ma mostra l'esito in un container distinto
        st.markdown("---")
    
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
    Process user input through the LLM state-machine.

    V3: delega tutta la logica a LLMService.generate_response().
    Il controller NON è più il decision-maker; è mantenuto solo per
    backward-compat (reset, survey options).
    """
    # Add user message to history
    state.add_message("user", user_input)

    # ── Call the state machine ──
    from ..services.llm_service import get_llm_service
    llm = get_llm_service()

    with st.spinner("Analisi in corso..."):
        response = llm.generate_response(user_input, st.session_state)

    # Add assistant response to history
    state.add_message("assistant", response)

    # ── Survey options (parsed inside generate_response) ──
    pending = st.session_state.get("pending_survey_options")
    if pending:
        controller.set_survey_options(pending)

    # ── Emergency alert ──
    urgency = st.session_state.get("urgency_level", 3)
    if urgency >= 5:
        st.error("🚨 **EMERGENZA RILEVATA** - Chiama immediatamente il **118**")
    elif urgency >= 4:
        phase = st.session_state.get("current_phase", "")
        if st.session_state.get("triage_path") == "B":
            st.warning("⚫ **Supporto Urgente** - Contatta il numero verde **800.274.274**")


def _render_disposition_summary() -> None:
    """
    Render final disposition summary with SBAR report.
    
    Mostra l'SBAR in un container distinto con bordo per differenziarlo dalla chat.
    """
    state = get_state_manager()
    
    st.markdown("---")
    st.success("✅ **Triage Completato**")
    
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    urgency = state.get(StateKeys.URGENCY_LEVEL, 3)
    
    # SBAR Container distinto usando container nativo di Streamlit
    with st.container(border=True):
        st.markdown("### 📋 Report SBAR")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**S - Situazione**")
            st.write(f"Sintomo: {collected.get('chief_complaint', collected.get('CHIEF_COMPLAINT', 'N/D'))}")
            st.write(f"Dolore: {collected.get('pain_scale', collected.get('PAIN_SCALE', 'N/D'))}/10")
            
            st.markdown("**B - Background**")
            st.write(f"Età: {collected.get('age', 'N/D')}")
            st.write(f"Sesso: {collected.get('sex', 'N/D')}")
            st.write(f"Località: {collected.get('current_location', collected.get('location', collected.get('LOCATION', 'N/D')))}")
        
        with col2:
            st.markdown("**A - Assessment**")
            urgency_labels = {1: "🟢 Verde", 2: "🟡 Giallo", 3: "🟠 Arancione", 4: "🔴 Rosso", 5: "⚫ Critico"}
            st.write(f"Urgenza: {urgency_labels.get(urgency, urgency)}")
            
            red_flags = collected.get('red_flags', collected.get('RED_FLAGS', []))
            st.write(f"Red Flags: {', '.join(red_flags) if red_flags else 'Nessuno'}")
            
            st.markdown("**R - Raccomandazione**")
            triage_path = state.get(StateKeys.TRIAGE_PATH, 'C')
            path_labels = {"A": "Emergenza", "B": "Salute Mentale", "C": "Standard", "INFO": "Informazioni"}
            st.write(f"Percorso: {path_labels.get(triage_path, triage_path)}")
    
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
