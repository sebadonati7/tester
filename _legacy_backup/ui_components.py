"""
SIRAYA Health Navigator - UI Components
V5.0: Clean Slate - Only components used by frontend.py
"""

import streamlit as st
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
import pandas as pd

# ============================================================================
# ADMIN TOOLS
# ============================================================================

def show_admin_logs(limit: int = 50):
    """
    Visualizza log recenti da Supabase per debugging.
    Mostra gli ultimi N record in un dataframe interattivo.
    
    Args:
        limit: Numero massimo di log da visualizzare (default 50)
    """
    st.markdown("### 🔍 Admin Panel - Recent Logs")
    st.caption(f"Ultimi {limit} log da Supabase")
    
    try:
        from session_storage import get_logger
        
        logger = get_logger()
        
        if not logger.client:
            st.error("❌ Connessione Supabase non disponibile")
            st.info("💡 Verifica che le credenziali SUPABASE_URL e SUPABASE_KEY siano configurate in st.secrets")
            return
        
        # Recupera log
        with st.spinner("Caricamento log da Supabase..."):
            logs = logger.get_recent_logs(limit=limit)
        
        if not logs:
            st.warning("⚠️ Nessun log disponibile")
            return
        
        # Converti a DataFrame per visualizzazione
        df_data = []
        for log in logs:
            # Parse metadata JSON
            try:
                metadata = json.loads(log.get('metadata', '{}'))
            except:
                metadata = {}
            
            df_data.append({
                'Session ID': log.get('session_id', 'N/A')[:8],  # Prime 8 char
                'Timestamp': log.get('timestamp', 'N/A'),
                'User Input': log.get('user_input', '')[:50],  # Prime 50 char
                'Bot Response': log.get('bot_response', '')[:50],
                'Duration (ms)': log.get('duration_ms', 0),
                'Triage Step': metadata.get('triage_step', 'N/A'),
                'Urgency Code': metadata.get('urgency_code', 'N/A')
            })
        
        df = pd.DataFrame(df_data)
        
        # Statistiche rapide
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📊 Total Logs", len(logs))
        with col2:
            unique_sessions = len(set(log.get('session_id') for log in logs))
            st.metric("👥 Unique Sessions", unique_sessions)
        with col3:
            avg_duration = sum(log.get('duration_ms', 0) for log in logs) / len(logs) if logs else 0
            st.metric("⚡ Avg Response (ms)", f"{avg_duration:.0f}")
        
        st.divider()
        
        # Dataframe interattivo
        st.dataframe(
            df,
            use_container_width=True,
            height=400
        )
        
        # Export JSON completo (espandibile)
        with st.expander("📥 Export Raw JSON"):
            st.json(logs)
        
    except ImportError as e:
        st.error(f"❌ Errore import: {e}")
        st.info("💡 Assicurati che session_storage.py sia presente nel progetto")
    except Exception as e:
        st.error(f"❌ Errore visualizzazione log: {e}")


# ============================================================================
# NAVIGATION COMPONENT
# ============================================================================

def render_navigation_sidebar() -> str:
    """
    Renderizza sidebar di navigazione unificata.
    DEVE essere chiamata all'interno di st.sidebar context.
    
    Returns:
        str: Pagina selezionata ("Chatbot" o "Analytics")
    """
    # Logo e Header
    st.markdown("""
    <div style="text-align: center; padding: 20px 0;">
        <div style="font-size: 2em; font-weight: 300; letter-spacing: 0.15em; color: #4A90E2;">
            SIRAYA
        </div>
        <div style="font-size: 0.85em; color: #6b7280; margin-top: 5px;">
            Health Navigator
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    # Navigation Radio
    page = st.radio(
        "🧭 Navigazione",
        ["🤖 Chatbot Triage", "📊 Analytics Dashboard"],
        label_visibility="collapsed"
    )
    
    st.divider()
    
    # Privacy Acceptance
    privacy_accepted = st.checkbox(
        "✅ Accetto l'informativa privacy",
        value=st.session_state.get("privacy_accepted", False),
        key="privacy_checkbox"
    )
    st.session_state.privacy_accepted = privacy_accepted
    
    st.divider()
    
    # Connection Status
    st.markdown("**📡 Stato Sistema**")
    
    # Check Supabase connection
    try:
        from session_storage import get_logger
        logger = get_logger()
        if logger.client:
            st.success("✅ Database Connesso")
        else:
            st.warning("⚠️ Database Offline")
    except:
        st.error("❌ Errore Sistema")
    
    # Map selection to clean string
    if "Analytics" in page:
        return "Analytics"
    else:
        return "Chatbot"
