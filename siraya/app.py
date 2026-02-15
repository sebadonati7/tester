"""
SIRAYA Health Navigator - Application Entry Point
V2.0: Final Assembly with proper routing.

This is the ONLY file that Streamlit executes directly.

Responsibilities:
- Page configuration (MUST be first Streamlit call)
- CSS loading
- State initialization
- Page routing
- Error handling

Usage:
    cd siraya
    streamlit run app.py
"""

# ============================================================================
# CRITICAL FIX: sys.path adjustment BEFORE any imports
# ============================================================================
import sys
from pathlib import Path

# Get absolute paths
_current_file = Path(__file__).resolve()
_siraya_dir = _current_file.parent  # siraya/
_project_root = _siraya_dir.parent  # parent of siraya/

# Add project root to sys.path (first priority)
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ============================================================================
# STREAMLIT IMPORTS
# ============================================================================
import streamlit as st

# ============================================================================
# PAGE CONFIGURATION (MUST BE FIRST STREAMLIT CALL)
# ============================================================================

st.set_page_config(
    page_title="SIRAYA Health Navigator",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# ABSOLUTE IMPORTS (After page config and path setup)
# ============================================================================

try:
    from siraya.config.settings import Settings, UI_THEME
    from siraya.core.state_manager import init_session_state, get_state, StateKeys
    from siraya.core.navigation import get_navigation, PageName
    from siraya.core.authentication import get_auth_manager
    
    # Import views
    from siraya.views import chat_view, sidebar_view, dashboard_view, map_view, report_view
    
    IMPORTS_OK = True
except ImportError as e:
    IMPORTS_OK = False
    IMPORT_ERROR = str(e)


# ============================================================================
# CSS LOADING
# ============================================================================

def load_css() -> None:
    """Load CSS from external file or inline fallback."""
    css_path = _siraya_dir / "config" / "styles.css"
    
    if css_path.exists():
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
                st.markdown(f"<style>{css_content}</style>", unsafe_allow_html=True)
        except Exception as e:
            print(f"CSS load error: {e}")
            _inject_fallback_css()
    else:
        _inject_fallback_css()


def _inject_fallback_css() -> None:
    """Inject fallback CSS if external file not found."""
    st.markdown("""
    <style>
        /* Blue Sidebar Style - Visual Parity with frontend.py */
        .main { background-color: #F8FAFC; }
        
        [data-testid="stSidebar"] {
            background-color: #f0f4f8 !important;
            background-image: linear-gradient(180deg, #E3F2FD 0%, #FFFFFF 100%) !important;
            border-right: 1px solid #d1d5db !important;
        }
        
        [data-testid="stSidebar"] .stMarkdown, 
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] h1, h2, h3, h4 {
            color: #1f2937 !important;
        }
        
        [data-testid="stSidebar"] label {
            color: #1f2937 !important;
        }
        
        [data-testid="stSidebar"] button {
            background-color: #ffffff !important;
            color: #1f2937 !important;
            border: 1px solid #d1d5db !important;
        }
        
        [data-testid="stSidebar"] button:hover {
            background-color: #e3f2fd !important;
            border-color: #90caf9 !important;
        }
        
        /* Professional Buttons */
        .stButton > button {
            width: 100%;
            border-radius: 8px;
            height: 3em;
            font-weight: 500;
            transition: all 0.3s ease;
            border: 1px solid #e5e7eb;
        }
        
        .stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(74, 144, 226, 0.2);
            border-color: #4A90E2;
        }
        
        /* Hide Streamlit header */
        .st-emotion-cache-15zrgzn { display: none; }
        
        /* Alert styling */
        .stAlert {
            border-radius: 8px;
        }
        
        /* Import Inter font */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif !important; }
    </style>
    """, unsafe_allow_html=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main() -> None:
    """
    Main application entry point.
    
    Flow:
    1. Check imports
    2. Load CSS
    3. Initialize session state
    4. Render sidebar (returns selected page)
    5. Route to appropriate view
    """
    # Step 0: Check if imports succeeded
    if not IMPORTS_OK:
        st.error(f"❌ Errore di importazione: {IMPORT_ERROR}")
        st.info("💡 Verifica che tutti i moduli siraya siano presenti.")
        st.code("""
# Struttura richiesta:
siraya/
├── app.py          ← (questo file)
├── config/
│   ├── settings.py
│   └── styles.css
├── core/
│   ├── state_manager.py
│   ├── navigation.py
│   └── authentication.py
├── services/
│   ├── llm_service.py
│   ├── data_loader.py
│   └── analytics_service.py
├── controllers/
│   └── triage_controller.py
└── views/
    ├── chat_view.py
    ├── dashboard_view.py
    ├── map_view.py
    ├── sidebar_view.py
    └── report_view.py
        """)
        return
    
    # Step 1: Load CSS
    load_css()
    
    # ============================================================================
    # DEBUG TEMPORANEO - Verifica Secrets Supabase
    # ============================================================================
    st.sidebar.markdown("### 🔍 DEBUG Supabase")
    try:
        # #region agent log
        import json
        import os
        try:
            log_data = {
                "location": "app.py:207",
                "message": "Verifica secrets Supabase",
                "data": {
                    "has_secrets": hasattr(st, "secrets"),
                    "secrets_type": str(type(st.secrets)) if hasattr(st, "secrets") else "N/A",
                    "cwd": os.getcwd(),
                    "project_root": str(_project_root),
                    "secrets_file_exists": os.path.exists(_project_root / ".streamlit" / "secrets.toml")
                },
                "timestamp": int(__import__("time").time() * 1000),
                "runId": "debug_secrets",
                "hypothesisId": "A"
            }
            log_dir = _project_root / ".cursor"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "debug.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + "\n")
        except Exception as log_err:
            # Ignora errori di logging, non bloccare il debug
            pass
        # #endregion
        
        # Verifica percorso secrets.toml
        secrets_path = _project_root / ".streamlit" / "secrets.toml"
        st.sidebar.write(f"**Percorso secrets.toml:** `{secrets_path}`")
        st.sidebar.write(f"**File esiste:** {secrets_path.exists()}")
        st.sidebar.write(f"**CWD:** `{os.getcwd()}`")
        
        # Verifica se st.secrets esiste
        if not hasattr(st, "secrets"):
            st.sidebar.error("❌ st.secrets non esiste!")
            return
        
        # Lista tutte le chiavi disponibili
        try:
            all_keys = list(st.secrets.keys()) if hasattr(st.secrets, "keys") else []
            st.sidebar.write(f"**Chiavi disponibili:** {', '.join(all_keys[:10])}")
        except Exception as e:
            st.sidebar.write(f"**Errore lista chiavi:** {e}")
        
        # Prova lettura diretta
        url_direct = st.secrets.get("SUPABASE_URL", "NON TROVATO")
        key_direct = st.secrets.get("SUPABASE_KEY", "NON TROVATO")
        
        # #region agent log
        try:
            log_data2 = {
                "location": "app.py:240",
                "message": "Lettura diretta secrets",
                "data": {
                    "url_direct": str(url_direct)[:30] if url_direct != "NON TROVATO" else "NON TROVATO",
                    "key_direct": str(key_direct)[:20] if key_direct != "NON TROVATO" else "NON TROVATO"
                },
                "timestamp": int(__import__("time").time() * 1000),
                "runId": "debug_secrets",
                "hypothesisId": "B"
            }
            log_dir = _project_root / ".cursor"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "debug.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_data2, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # #endregion
        
        st.sidebar.write(f"**URL diretto:** {str(url_direct)[:30]}...")
        st.sidebar.write(f"**KEY diretto:** {str(key_direct)[:20]}...")
        
        # Prova accesso diretto (non .get())
        try:
            url_direct2 = st.secrets["SUPABASE_URL"]
            key_direct2 = st.secrets["SUPABASE_KEY"]
            st.sidebar.success(f"✅ Accesso diretto OK!")
            st.sidebar.write(f"**URL [ ]:** {str(url_direct2)[:30]}...")
            st.sidebar.write(f"**KEY [ ]:** {str(key_direct2)[:20]}...")
        except KeyError as ke:
            st.sidebar.warning(f"⚠️ KeyError: {ke}")
        except Exception as e2:
            st.sidebar.warning(f"⚠️ Errore accesso diretto: {e2}")
        
        # Prova con SupabaseConfig
        from siraya.config.settings import SupabaseConfig
        url_config = SupabaseConfig.get_url()
        key_config = SupabaseConfig.get_key()
        is_conf = SupabaseConfig.is_configured()
        
        # #region agent log
        try:
            log_data3 = {
                "location": "app.py:280",
                "message": "Lettura via SupabaseConfig",
                "data": {
                    "url_config": str(url_config)[:30] if url_config else "VUOTO",
                    "key_config": str(key_config)[:20] if key_config else "VUOTO",
                    "is_configured": is_conf
                },
                "timestamp": int(__import__("time").time() * 1000),
                "runId": "debug_secrets",
                "hypothesisId": "C"
            }
            log_dir = _project_root / ".cursor"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "debug.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_data3, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # #endregion
        
        st.sidebar.write(f"**URL da Config:** {str(url_config)[:30] if url_config else 'VUOTO'}...")
        st.sidebar.write(f"**KEY da Config:** {str(key_config)[:20] if key_config else 'VUOTO'}...")
        st.sidebar.write(f"**is_configured():** {is_conf}")
        
    except Exception as e:
        st.sidebar.error(f"❌ Errore: {e}")
        import traceback
        st.sidebar.code(traceback.format_exc())
        
        # #region agent log
        try:
            log_data4 = {
                "location": "app.py:310",
                "message": "Errore durante debug secrets",
                "data": {
                    "error": str(e),
                    "error_type": type(e).__name__
                },
                "timestamp": int(__import__("time").time() * 1000),
                "runId": "debug_secrets",
                "hypothesisId": "D"
            }
            log_dir = _project_root / ".cursor"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "debug.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_data4, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # #endregion
    # ============================================================================
    
    # Step 2: Initialize session state
    init_session_state()
    
    # Step 3: Render sidebar and get navigation
    with st.sidebar:
        selected_page = sidebar_view.render()
        sidebar_view.render_reset_button()
    
    # Step 4: Route to appropriate view
    route_to_page(selected_page)


def route_to_page(page_name: str) -> None:
    """
    Route to the appropriate page view.
    
    Args:
        page_name: Name of the page to render ("CHAT", "DASHBOARD", "MAP", "REPORT")
    """
    try:
        if page_name == "DASHBOARD":
            dashboard_view.render()
        
        elif page_name == "MAP":
            map_view.render()
        
        elif page_name == "REPORT":
            report_view.render()
        
        else:
            # Default to chat
            chat_view.render()
    
    except Exception as e:
        st.error(f"❌ Errore nel caricamento della pagina: {e}")
        
        # Show error details in expander
        with st.expander("🔍 Dettagli Errore"):
            import traceback
            st.code(traceback.format_exc())
        
        st.info("💡 Prova a ricaricare la pagina o tornare al Chatbot.")
        
        if st.button("🏠 Torna al Chatbot"):
            try:
                from siraya.core.navigation import switch_to
                switch_to(PageName.CHAT)
            except:
                # Hard reset
                st.session_state["current_page"] = "CHAT"
                st.rerun()


# ============================================================================
# ERROR BOUNDARY FOR IMPORTS
# ============================================================================

def render_import_error_page() -> None:
    """Render a helpful error page when imports fail."""
    st.title("⚠️ SIRAYA - Errore di Avvio")
    
    st.error("""
    **Impossibile avviare l'applicazione.**
    
    Questo può accadere se:
    - Mancano alcuni file del progetto
    - Le dipendenze non sono installate
    - Ci sono errori di sintassi nei moduli
    """)
    
    st.markdown("### 🔧 Suggerimenti")
    
    st.markdown("""
    1. **Verifica le dipendenze:**
       ```bash
       pip install streamlit supabase groq google-generativeai plotly folium streamlit-folium
       ```
    
    2. **Verifica la struttura del progetto:**
       ```
       siraya/
       ├── app.py
       ├── config/
       ├── core/
       ├── services/
       ├── controllers/
       └── views/
       ```
    
    3. **Controlla i log per errori specifici.**
    """)
    
    # Try to import each module and show status
    st.markdown("### 📋 Status Moduli")
    
    modules_to_check = [
        ("siraya.config.settings", "Configurazione"),
        ("siraya.core.state_manager", "State Manager"),
        ("siraya.core.navigation", "Navigazione"),
        ("siraya.core.authentication", "Autenticazione"),
        ("siraya.services.llm_service", "LLM Service"),
        ("siraya.services.data_loader", "Data Loader"),
        ("siraya.services.analytics_service", "Analytics Service"),
        ("siraya.controllers.triage_controller", "Triage Controller"),
        ("siraya.views.chat_view", "Chat View"),
        ("siraya.views.dashboard_view", "Dashboard View"),
        ("siraya.views.map_view", "Map View"),
        ("siraya.views.sidebar_view", "Sidebar View"),
    ]
    
    for module_path, module_name in modules_to_check:
        try:
            __import__(module_path)
            st.success(f"✅ {module_name}")
        except ImportError as e:
            st.error(f"❌ {module_name}: {e}")
        except Exception as e:
            st.warning(f"⚠️ {module_name}: {e}")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
