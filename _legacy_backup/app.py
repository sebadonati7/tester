"""
SIRAYA Health Navigator - Entry Point Monolitico
Selettore di modalità: Chatbot Triage / Analytics Dashboard
"""

import streamlit as st

# CONFIGURAZIONE PAGINA - DEVE ESSERE LA PRIMA ISTRUZIONE STREAMLIT
st.set_page_config(
    page_title="SIRAYA Health Navigator",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Import locali dopo st.set_page_config
import os
import sys
from pathlib import Path

# ============================================================================
# CENTRALIZZAZIONE PERSISTENZA LOG (V3.2)
# ============================================================================
# Definisce il path assoluto di triage_logs.jsonl e lo gestisce centralmente
# per garantire che Streamlit Cloud salvi correttamente le conversazioni

# Path assoluto del file log (compatibile con Streamlit Cloud)
LOG_FILE_PATH = Path(__file__).parent.absolute() / "triage_logs.jsonl"

# Inizializza il file log se non esiste
if not LOG_FILE_PATH.exists():
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
            pass  # Crea file vuoto
    except Exception as e:
        print(f"⚠️ Errore creazione file log: {e}")

# Passa il path ai moduli tramite session_state
if "log_file_path" not in st.session_state:
    st.session_state.log_file_path = str(LOG_FILE_PATH)

# Inietta CSS SIRAYA personalizzato
try:
    from ui_components import inject_siraya_css
    inject_siraya_css()
except ImportError:
    # Fallback CSS se ui_components non disponibile
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    * { font-family: 'Inter', sans-serif !important; }
    .stApp { background-color: #f8fafc; }
    section[data-testid="stSidebar"] { background-color: #1e293b !important; }
    section[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    
    /* Sidebar: Colori Bianco/Panna per expander e box evidenziati */
    .streamlit-expanderHeader {
        background-color: #FDFCF0 !important;
        color: #1e293b !important;
    }
    .streamlit-expanderContent {
        background-color: #FDFCF0 !important;
        color: #1e293b !important;
    }
    [data-testid="stSidebar"] [class*="stAlert"] {
        background-color: #FDFCF0 !important;
        color: #1e293b !important;
    }
    [data-testid="stSidebar"] [class*="metric-container"] {
        background-color: #FDFCF0 !important;
        color: #1e293b !important;
    }
    
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)


# ============================================================================
# SELEZIONE MODALITÀ
# ============================================================================

def render_mode_selector():
    """Renderizza selettore modalità nella sidebar."""
    st.sidebar.markdown("## 🎯 Modalità SIRAYA")
    
    mode = st.sidebar.radio(
        "Seleziona modalità:",
        ["🤖 Chatbot Triage", "📈 Analytics Dashboard"],
        key="mode_selector",
        label_visibility="visible"
    )
    
    return mode


# ============================================================================
# PASSWORD GATE PER ANALYTICS DASHBOARD
# ============================================================================

def check_backend_authentication():
    """
    Verifica autenticazione per Analytics Dashboard.
    Returns: True se autenticato, False altrimenti.
    """
    # Se già autenticato, salta il check
    if st.session_state.get("authenticated", False):
        return True
    
    # Mostra password input nella sidebar
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔒 Autenticazione Richiesta")
    
    password = st.sidebar.text_input(
        "Password di Accesso",
        type="password",
        key="backend_password_input",
        label_visibility="visible"
    )
    
    # Verifica password
    if password:
        # Carica password da secrets
        try:
            backend_password = st.secrets.get("BACKEND_PASSWORD", "")
            
            if not backend_password:
                st.sidebar.error("⚠️ BACKEND_PASSWORD non configurata in secrets.toml")
                return False
            
            if password == backend_password:
                st.session_state.authenticated = True
                st.sidebar.success("✅ Accesso autorizzato")
                st.rerun()
            else:
                st.sidebar.error("❌ Accesso Negato: Password errata")
                return False
                
        except Exception as e:
            st.sidebar.error(f"❌ Errore autenticazione: {e}")
            return False
    
    return False


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Entry point principale con selettore modalità."""
    
    # Selettore modalità
    selected_mode = render_mode_selector()
    
    # Routing basato su modalità selezionata
    if selected_mode == "🤖 Chatbot Triage":
        # Carica frontend (Chatbot Triage)
        try:
            # Import dinamico per evitare conflitti
            if "frontend_loaded" not in st.session_state:
                # Reset autenticazione quando si cambia modalità
                if "authenticated" in st.session_state:
                    del st.session_state.authenticated
                
                st.session_state.frontend_loaded = True
            
            # Import e esecuzione frontend con path log centralizzato
            import frontend
            # Passa il path del log al frontend
            frontend.main(log_file_path=st.session_state.log_file_path)
            
        except Exception as e:
            st.error(f"❌ Errore caricamento Chatbot Triage: {e}")
            st.info("💡 Verifica che frontend.py sia presente nella directory root.")
    
    elif selected_mode == "📈 Analytics Dashboard":
        # Password Gate per Analytics
        if not check_backend_authentication():
            # Mostra messaggio di accesso negato
            st.title("🔒 Analytics Dashboard")
            st.markdown("---")
            st.warning("⚠️ **Accesso Negato**")
            st.info(
                "Per accedere all'Analytics Dashboard, inserisci la password corretta nella sidebar.\n\n"
                "Se non hai la password, contatta l'amministratore del sistema."
            )
            st.stop()
        
        # Se autenticato, carica backend (Analytics Dashboard)
        try:
            # Reset frontend_loaded quando si cambia modalità
            if "frontend_loaded" in st.session_state:
                del st.session_state.frontend_loaded
            
            # Import e esecuzione backend con path log centralizzato
            import backend
            # Passa il path del log al backend
            backend.main(log_file_path=st.session_state.log_file_path)
            
        except Exception as e:
            st.error(f"❌ Errore caricamento Analytics Dashboard: {e}")
            st.info("💡 Verifica che backend.py sia presente nella directory root.")


if __name__ == "__main__":
    main()

