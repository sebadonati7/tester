"""
SIRAYA Health Navigator - Chat View
V2.1: Refactored to use TriageController AI-Driven Orchestrator

This view:
- Renders chat message history with avatars
- Handles user input via TriageController V2.1
- Shows AI responses with dynamic multiple choice options
- Displays survey buttons (A/B/C options) when available
- Includes TTS (Text-to-Speech) support
- Integrates with Supabase memory for conversational context
"""

import streamlit as st
from typing import Optional, List, Dict, Any
import time
import logging

from ..core.state_manager import get_state_manager, StateKeys
from ..core.authentication import check_privacy_accepted, render_privacy_consent
from ..controllers.triage_controller_v3 import get_triage_controller  # ✅ V3

# Setup logger
logger = logging.getLogger(__name__)


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
    if current_phase in ("DISPOSITION", "RECOMMENDATION", "sbar", "outcome") and messages:
        _render_disposition_summary()
        # Non bloccare l'input, ma mostra l'esito in un container distinto
        st.markdown("---")
    
    # === RENDER MULTIPLE CHOICE OPTIONS (NEW V2.1) ===
    # Recupera ultima risposta dal state
    last_response = state.get(StateKeys.LAST_BOT_RESPONSE, {})
    question_type = last_response.get("question_type", "open_text")
    options = last_response.get("options", None)
    
    # ✅ NUOVO: Rendering outcome con bottone download SBAR
    if question_type == "outcome":
        _render_sbar_download_buttons(state)
    
    # Se ci sono opzioni multiple choice E l'ultimo messaggio è del bot, mostra bottoni
    if question_type == "multiple_choice" and options and messages:
        if messages[-1]["role"] == "assistant":  # Verifica che sia l'ultimo messaggio del bot
            st.markdown("#### 💬 Seleziona una risposta:")
            
            # Mostra bottoni in una griglia (max 2 colonne)
            num_cols = min(len(options), 2)
            cols = st.columns(num_cols)
            
            for idx, option in enumerate(options):
                col_idx = idx % num_cols
                with cols[col_idx]:
                    # Usa unique key con timestamp per evitare duplicati
                    btn_key = f"option_{idx}_{len(messages)}"
                    if st.button(
                        option, 
                        key=btn_key, 
                        use_container_width=True,
                        type="secondary"
                    ):
                        # User clicked an option
                        _process_user_input(option, controller, state)
                        st.rerun()
            
            # Alternative: testo libero
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
    Process user input through TriageController V2.1 (AI-Driven Orchestrator).
    
    Uses the NEW refactored controller that:
    - Classifies branch (A/B/C/INFO)
    - Extracts data with memory
    - Generates questions via AI (including multiple choice options)
    - Saves state and returns response with options included
    """
    # ✅ Import esplicito per evitare NameError in alcuni contesti Streamlit
    # (necessario quando la funzione viene chiamata da callback di bottoni)
    from ..core.state_manager import StateKeys
    
    # Add user message to history
    state.add_message("user", user_input)

    # ✅ NUOVO: Usa TriageController refactorato
    with st.spinner("🔍 Analisi in corso..."):
        try:
            response = controller.process_user_input(user_input)
        except Exception as e:
            logger.error(f"❌ Errore in process_user_input: {e}")
            st.error(f"❌ Si è verificato un errore: {e}")
            return

    # Extract response components
    assistant_text = response.get("assistant_response", "")
    question_type = response.get("question_type", "open_text")
    options = response.get("options", None)
    metadata = response.get("metadata", {})
    
    # Add assistant response to history
    state.add_message("assistant", assistant_text)
    
    # ✅ Salva risposta nello state per rendering (con options)
    try:
        state.set(StateKeys.LAST_BOT_RESPONSE, response)
    except NameError as e:
        # Fallback: re-import se StateKeys non è disponibile
        logger.warning(f"⚠️ StateKeys non disponibile, re-importing: {e}")
        from ..core.state_manager import StateKeys
        state.set(StateKeys.LAST_BOT_RESPONSE, response)

    # ── Emergency alert (basato su metadata o branch) ──
    try:
        branch = state.get(StateKeys.TRIAGE_BRANCH, "")
    except NameError as e:
        # Fallback: re-import se StateKeys non è disponibile
        logger.warning(f"⚠️ StateKeys non disponibile, re-importing: {e}")
        from ..core.state_manager import StateKeys
        branch = state.get(StateKeys.TRIAGE_BRANCH, "")
    if branch == "A":  # Branch EMERGENCY
        st.error("🚨 **EMERGENZA RILEVATA** - Chiama immediatamente il **118**")
    elif branch == "B":  # Branch MENTAL_HEALTH
        st.warning("⚫ **Supporto Urgente** - Contatta il numero verde **800.274.274**")
    
    # Log success
    logger.info(f"✅ Processato input, type={question_type}, branch={branch}")



def _render_sbar_download_buttons(state) -> None:
    """
    Render bottoni per download SBAR (PDF e TXT) quando outcome è disponibile.
    ✅ V3: SBAR_REPORT_DATA è una stringa (non dict).
    """
    from ..services.pdf_service import get_pdf_service
    from datetime import datetime
    import json
    
    # ✅ V3: SBAR è una stringa diretta
    sbar_text = state.get(StateKeys.SBAR_REPORT_DATA)
    
    if not sbar_text:
        logger.warning("⚠️ SBAR data non disponibile per download")
        return
    
    st.markdown("---")
    st.markdown("### 📄 Report SBAR Disponibile")
    st.info("Puoi scaricare il report completo in formato PDF o testo.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Download PDF
        try:
            pdf_service = get_pdf_service()
            patient_data = state.get(StateKeys.COLLECTED_DATA, {})
            
            # Aggiungi session_id ai patient_data se mancante
            if "session_id" not in patient_data:
                patient_data["session_id"] = state.get(StateKeys.SESSION_ID, "unknown")
            
            pdf_bytes = pdf_service.generate_sbar_pdf(
                patient_data=patient_data,
                sbar_text=sbar_text,
                facility_name=None
            )
            
            st.download_button(
                label="📄 Scarica Report SBAR (PDF)",
                data=pdf_bytes,
                file_name=f"SBAR_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="download_sbar_pdf"
            )
        except Exception as e:
            logger.error(f"❌ Errore generazione PDF: {e}")
            st.error("❌ Errore nella generazione del PDF")
    
    with col2:
        # Download TXT
        try:
            session_id = state.get(StateKeys.SESSION_ID, "unknown")
            collected_data = state.get(StateKeys.COLLECTED_DATA, {})
            
            sbar_text_formatted = f"""
SIRAYA Health Navigator - Report SBAR
Generato: {datetime.now().strftime('%d/%m/%Y %H:%M')}
Session ID: {session_id}

═══════════════════════════════════════
REPORT TRIAGE COMPLETO
═══════════════════════════════════════

{sbar_text}

═══════════════════════════════════════
DATI RACCOLTI
═══════════════════════════════════════
{json.dumps(collected_data, indent=2, ensure_ascii=False)}

═══════════════════════════════════════
NOTA IMPORTANTE
═══════════════════════════════════════
Questo report è generato automaticamente e non costituisce diagnosi medica.
In caso di emergenza, chiama immediatamente il 118.
            """.strip()
            
            st.download_button(
                label="📋 Scarica Report SBAR (TXT)",
                data=sbar_text_formatted.encode('utf-8'),
                file_name=f"SBAR_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                mime="text/plain",
                use_container_width=True,
                key="download_sbar_txt"
            )
        except Exception as e:
            logger.error(f"❌ Errore generazione TXT: {e}")
            st.error("❌ Errore nella generazione del TXT")


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
            state = get_state_manager()
            state.reset_triage()
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
            state = get_state_manager()
            state.reset_triage()
            st.rerun()
    
    with col2:
        if st.button("📋 Genera Report", use_container_width=True, key="qa_report"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.REPORT)
    
    with col3:
        if st.button("🗺️ Vedi Mappa", use_container_width=True, key="qa_map"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.MAP)
