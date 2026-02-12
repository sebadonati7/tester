"""
SIRAYA Health Navigator - Dashboard View
V2.0: Visual Parity with legacy backend.py

This view:
- Shows all 15 KPIs from AnalyticsService
- Renders Plotly charts (same style as backend.py)
- Displays log tables
- Provides filtering and export functionality
"""

import streamlit as st
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import Counter

from ..core.authentication import get_auth_manager, render_admin_login
from ..services.analytics_service import get_analytics_service


# ============================================================================
# PLOTLY CHART FUNCTIONS (Visual Parity with backend.py)
# ============================================================================

def render_throughput_chart(kpi: Dict[str, Any]) -> None:
    """
    Render hourly throughput bar chart.
    
    Visual Parity: Exact style from backend.py render_throughput_chart()
    """
    throughput = kpi.get('throughput_orario', {})
    
    if not throughput or len(throughput) == 0:
        st.info("ℹ️ Nessun dato disponibile per throughput orario.")
        return
    
    try:
        import plotly.graph_objects as go
        
        hours = sorted(throughput.keys())
        counts = [throughput[h] for h in hours]
        
        fig = go.Figure(data=[
            go.Bar(
                x=hours,
                y=counts,
                marker_color='#4A90E2',
                hovertemplate='<b>Ora %{x}:00</b><br>Accessi: %{y}<extra></extra>'
            )
        ])
        
        fig.update_layout(
            title="Throughput Orario (Distribuzione Accessi)",
            xaxis_title="Ora del Giorno",
            yaxis_title="N° Interazioni",
            height=400,
            hovermode='x unified',
            plot_bgcolor='white',
            paper_bgcolor='white',
            font=dict(family="Arial, sans-serif", size=12)
        )
        
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#e5e7eb')
        fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='#e5e7eb')
        
        st.plotly_chart(fig, use_container_width=True)
        
    except ImportError:
        st.warning("⚠️ Plotly non installato. Installa con: `pip install plotly`")
        # Fallback text display
        st.json(dict(throughput))


def render_urgenza_pie(kpi: Dict[str, Any]) -> None:
    """
    Render urgency stratification pie chart.
    
    Visual Parity: Exact style from backend.py render_urgenza_pie()
    """
    stratificazione = kpi.get('stratificazione_urgenza', {})
    
    if not stratificazione or len(stratificazione) == 0:
        st.info("ℹ️ Nessun dato disponibile per stratificazione urgenza.")
        return
    
    try:
        import plotly.graph_objects as go
        
        labels = [f"Codice {k}" for k in sorted(stratificazione.keys())]
        values = [stratificazione[k] for k in sorted(stratificazione.keys())]
        
        # Clinical color palette (green → red)
        colors = ['#00C853', '#FFEB3B', '#FF9800', '#FF5722', '#B71C1C']
        
        fig = go.Figure(data=[
            go.Pie(
                labels=labels,
                values=values,
                marker_colors=colors[:len(labels)],
                hovertemplate='<b>%{label}</b><br>Casi: %{value}<br>Percentuale: %{percent}<extra></extra>',
                textinfo='label+percent',
                textposition='auto'
            )
        ])
        
        fig.update_layout(
            title="Stratificazione Urgenza (Codici 1-5)",
            height=400,
            showlegend=True,
            plot_bgcolor='white',
            paper_bgcolor='white',
            font=dict(family="Arial, sans-serif", size=12)
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except ImportError:
        st.warning("⚠️ Plotly non installato")
        for label, val in sorted(stratificazione.items()):
            st.write(f"Codice {label}: {val}")


def render_funnel_chart(kpi: Dict[str, Any]) -> None:
    """Render funnel drop-off visualization."""
    funnel = kpi.get('funnel_dropoff', {})
    
    if not funnel:
        return
    
    try:
        import plotly.graph_objects as go
        
        early_abandon = funnel.get('early_abandon', 0)
        completed = funnel.get('completed', 0)
        dropoff_rate = funnel.get('dropoff_rate', 0)
        
        fig = go.Figure(data=[
            go.Bar(
                x=['Abbandoni Precoci', 'Completate'],
                y=[early_abandon, completed],
                marker_color=['#EF4444', '#10B981'],
                text=[f'{early_abandon}', f'{completed}'],
                textposition='auto',
                hovertemplate='%{x}: %{y}<extra></extra>'
            )
        ])
        
        fig.update_layout(
            title=f"Funnel Sessioni (Drop-off: {dropoff_rate:.1f}%)",
            height=300,
            plot_bgcolor='white',
            paper_bgcolor='white',
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except ImportError:
        st.metric("Drop-off Rate", f"{funnel.get('dropoff_rate', 0):.1f}%")


def render_geographic_chart(kpi: Dict[str, Any]) -> None:
    """Render geographic coverage chart."""
    geo = kpi.get('copertura_geografica', {})
    
    if not geo:
        return
    
    distribution = geo.get('distribuzione_distretti', {})
    
    if not distribution:
        st.info("Nessun dato geografico disponibile")
        return
    
    try:
        import plotly.graph_objects as go
        
        districts = list(distribution.keys())
        counts = list(distribution.values())
        
        fig = go.Figure(data=[
            go.Bar(
                x=districts,
                y=counts,
                marker_color='#6366F1',
                hovertemplate='<b>%{x}</b><br>Sessioni: %{y}<extra></extra>'
            )
        ])
        
        fig.update_layout(
            title=f"Copertura Geografica ({geo.get('distretti_attivi', 0)} distretti attivi)",
            xaxis_title="Distretto",
            yaxis_title="N° Sessioni",
            height=350,
            plot_bgcolor='white',
            paper_bgcolor='white',
            xaxis_tickangle=-45
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except ImportError:
        for district, count in distribution.items():
            st.write(f"- {district}: {count}")


def render_sintomi_table(kpi: Dict[str, Any]) -> None:
    """Render symptom spectrum table."""
    spettro = kpi.get('spettro_sintomi', {})
    
    if not spettro:
        st.info("Nessun sintomo rilevato nei dati.")
        return
    
    st.subheader("📋 Spettro Sintomatologico Completo")
    
    # Sort by frequency
    sintomi_list = sorted(spettro.items(), key=lambda x: x[1], reverse=True)
    
    # Render as dataframe
    st.dataframe(
        {
            'Sintomo': [s[0].title() for s in sintomi_list],
            'Frequenza': [s[1] for s in sintomi_list]
        },
        use_container_width=True,
        height=400
    )


# ============================================================================
# CRITICAL ALERTS COMPONENT
# ============================================================================

def render_critical_alerts(analytics) -> None:
    """Render live critical case alerts."""
    st.markdown("### 🚨 Live Critical Alerts")
    
    critical_cases = analytics.get_recent_critical_cases(hours=1)
    
    if critical_cases:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%); 
                    border-left: 4px solid #dc2626; 
                    border-radius: 12px; 
                    padding: 20px; 
                    margin-bottom: 20px;
                    box-shadow: 0 2px 8px rgba(220, 38, 38, 0.1);'>
            <h4 style='margin: 0 0 10px 0; color: #991b1b;'>
                ⚠️ {len(critical_cases)} Casi Critici (Ultima Ora)
            </h4>
        </div>
        """, unsafe_allow_html=True)
        
        with st.expander("📋 Dettagli Casi Critici", expanded=False):
            for i, case in enumerate(critical_cases[:10], 1):
                urgency = case.get('urgency', 3)
                urgency_emoji = "🔴" if urgency >= 4 else "🟠"
                
                st.markdown(f"""
                **{urgency_emoji} Caso {i}**  
                - **Sessione**: `{case.get('session_id', 'N/D')[:8]}...`  
                - **Urgenza**: {urgency}/5  
                - **Input**: {case.get('user_input', 'N/D')[:80]}...
                """)
                st.divider()
    else:
        st.success("✅ Nessun caso critico nell'ultima ora")
    
    st.markdown("---")


# ============================================================================
# MAIN RENDER FUNCTION
# ============================================================================

def render() -> None:
    """
    Render the analytics dashboard.
    
    Visual Parity with legacy backend.py render_dashboard()
    """
    # Check admin access (optional - remove for open access)
    auth = get_auth_manager()
    if not auth.is_admin_logged_in():
        st.title("🔒 Analytics Dashboard - Login Richiesto")
        
        # ✅ SHOW PASSWORD INFO
        st.info("""
        **📋 Credenziali di Accesso**
        
        - **Password predefinita**: `ciaociao`
        - Per cambiarla: aggiungi `BACKEND_PASSWORD = "tuapassword"` in `.streamlit/secrets.toml`
        
        💡 Se non riesci ad accedere, verifica che il file secrets.toml esista e sia configurato correttamente.
        """)
        
        render_admin_login()
        return
    
    # === INLINE CSS ===
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    * { font-family: 'Inter', sans-serif !important; }
    .stApp { background-color: #f8fafc; }
    </style>
    """, unsafe_allow_html=True)
    
    # === HEADER ===
    st.title("🧬 SIRAYA Analytics | Dashboard Professionale")
    
    # === GET ANALYTICS SERVICE ===
    analytics = get_analytics_service()
    
    # === LOAD DATA ===
    with st.spinner("📡 Caricamento dati da Supabase..."):
        logs = analytics.get_all_logs()
    
    if not logs:
        st.warning("⚠️ Nessun dato disponibile.")
        st.info("💡 Inizia alcune conversazioni per popolare i log.")
        return
    
    st.caption(f"📊 Dati: {len(logs)} interazioni caricate")
    
    # === RENDER CRITICAL ALERTS ===
    render_critical_alerts(analytics)
    
    # === CALCULATE ALL KPIs ===
    with st.spinner("🔢 Calcolo KPI in corso..."):
        try:
            kpi_completo = analytics.calculate_kpi_completo()
        except Exception as e:
            st.error(f"❌ Errore calcolo KPI: {e}")
            kpi_completo = {}
    
    if not kpi_completo:
        st.warning("Impossibile calcolare i KPI.")
        return
    
    # === SECTION 1: KPI VOLUMETRICI ===
    st.header("📈 KPI Volumetrici")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric("Sessioni Uniche", f"{kpi_completo.get('sessioni_uniche', 0)}")
    
    with col2:
        interactions = len(logs)
        st.metric("Interazioni Totali", f"{interactions}")
    
    with col3:
        completion = kpi_completo.get('tasso_completamento', 0)
        st.metric("Completion Rate", f"{completion:.1f}%")
    
    with col4:
        tempo = kpi_completo.get('tempo_mediano_triage_minuti', 0)
        st.metric("Tempo Mediano", f"{tempo:.1f} min")
    
    with col5:
        sessions = kpi_completo.get('sessioni_uniche', 1)
        profondita = interactions / sessions if sessions > 0 else 0
        st.metric("Profondità Media", f"{profondita:.1f}")
    
    st.divider()
    
    # Throughput Chart
    render_throughput_chart(kpi_completo)
    
    # === SECTION 2: KPI CLINICI ===
    st.header("🏥 KPI Clinici ed Epidemiologici")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Prevalenza Red Flags
        red_flags_count = sum(1 for log in logs if log.get('metadata', {}).get('red_flags'))
        red_flags_rate = (red_flags_count / len(logs) * 100) if logs else 0
        st.metric("Prevalenza Red Flags", f"{red_flags_rate:.1f}%")
        
        # Red Flags Detail
        st.subheader("🚨 Red Flags per Tipo")
        red_flags_detail = kpi_completo.get('red_flags_dettaglio', {})
        if red_flags_detail:
            for rf, count in sorted(red_flags_detail.items(), key=lambda x: x[1], reverse=True)[:10]:
                st.write(f"**{rf.title()}**: {count}")
        else:
            st.info("Nessun red flag rilevato")
    
    with col2:
        render_urgenza_pie(kpi_completo)
    
    st.divider()
    
    # Symptoms Table
    render_sintomi_table(kpi_completo)
    
    # === SECTION 3: KPI CONTEXT-AWARE ===
    st.header("🎯 KPI Context-Aware")
    
    col1, col2 = st.columns(2)
    
    with col1:
        deviazione_ps = kpi_completo.get('tasso_deviazione_ps', 0)
        deviazione_terr = kpi_completo.get('tasso_deviazione_territoriale', 0)
        
        st.metric("Deviazione Pronto Soccorso", f"{deviazione_ps:.1f}%")
        st.metric("Deviazione Territoriale", f"{deviazione_terr:.1f}%")
        
        # Efficiency metrics
        efficienza = kpi_completo.get('efficienza_reindirizzamento', 0)
        st.metric("Efficienza Reindirizzamento", f"{efficienza:.1f}%",
                  help="% casi non urgenti indirizzati verso strutture territoriali invece del PS")
    
    with col2:
        # Urgenza per Specializzazione
        st.subheader("⚕️ Urgenza Media per Specializzazione")
        urgenza_spec = kpi_completo.get('urgenza_media_per_spec', {})
        if urgenza_spec:
            for spec, urg in sorted(urgenza_spec.items(), key=lambda x: x[1], reverse=True):
                st.write(f"**{spec}**: {urg:.2f}")
        else:
            st.info("Nessun dato disponibile")
    
    st.divider()
    
    # === SECTION 4: KPI AVANZATI (15 KPIs) ===
    st.header("📊 KPI Clinici Avanzati")
    
    # Row 1
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        accuratezza = kpi_completo.get('accuratezza_clinica', 0)
        st.metric("Accuratezza Clinica", f"{accuratezza:.1f}%",
                  help="Coerenza tra sintomi dichiarati e disposizione finale")
    
    with col2:
        latenza = kpi_completo.get('latenza_media_secondi', 0)
        st.metric("Latenza Media", f"{latenza:.1f}s",
                  help="Tempo medio di risposta AI")
    
    with col3:
        aderenza = kpi_completo.get('aderenza_protocolli', 0)
        st.metric("Aderenza Protocolli", f"{aderenza:.1f}%",
                  help="% sessioni che seguono il flusso completo (età, location, sintomi)")
    
    with col4:
        sentiment = kpi_completo.get('sentiment_medio', 0)
        sentiment_emoji = "😊" if sentiment > 0 else ("😐" if sentiment == 0 else "😟")
        st.metric("Sentiment Medio", f"{sentiment:.2f} {sentiment_emoji}",
                  help="Analisi del tono dell'utente (-2 = urgente, +1 = positivo)")
    
    # Row 2
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        divergenza = kpi_completo.get('tasso_divergenza_algoritmica', 0)
        st.metric("Divergenza Algoritmica", f"{divergenza:.1f}%",
                  help="Differenza tra urgenza AI e urgenza keyword-based")
    
    with col2:
        omissione_rf = kpi_completo.get('tasso_omissione_red_flags', 0)
        st.metric("Omissione Red Flags", f"{omissione_rf:.1f}%",
                  help="Red flags menzionati ma non catturati")
    
    with col3:
        esitazione = kpi_completo.get('indice_esitazione_secondi', 0)
        st.metric("Indice Esitazione", f"{esitazione:.1f}s",
                  help="Tempo medio di risposta utente")
    
    with col4:
        fast_track = kpi_completo.get('fast_track_efficiency_ratio', 0)
        st.metric("Fast Track Ratio", f"{fast_track:.2f}x",
                  help="Velocità gestione casi critici vs standard")
    
    st.divider()
    
    # Funnel and Geographic charts
    col1, col2 = st.columns(2)
    
    with col1:
        render_funnel_chart(kpi_completo)
    
    with col2:
        render_geographic_chart(kpi_completo)
    
    # === FOOTER ===
    st.divider()
    st.caption("SIRAYA Health Navigator V2.0 | Analytics Engine | Supabase-Powered")


# ============================================================================
# FILTER SIDEBAR (Optional)
# ============================================================================

def render_filters_sidebar() -> Dict[str, Any]:
    """
    Render filter controls in sidebar.
    
    Returns:
        Dict with filter values
    """
    st.sidebar.markdown("### 🔍 Filtri")
    
    # Date range
    col1, col2 = st.sidebar.columns(2)
    with col1:
        date_from = st.date_input("Dal", value=None, key="filter_from")
    with col2:
        date_to = st.date_input("Al", value=None, key="filter_to")
    
    # District filter
    district = st.sidebar.selectbox(
        "Distretto",
        ["Tutti", "Bologna", "Modena", "Parma", "Reggio Emilia", "Ferrara", "Ravenna", "Forlì-Cesena", "Rimini"],
        key="filter_district"
    )
    
    # Urgency filter
    urgency = st.sidebar.multiselect(
        "Urgenza",
        [1, 2, 3, 4, 5],
        default=[1, 2, 3, 4, 5],
        key="filter_urgency"
    )
    
    return {
        "date_from": date_from,
        "date_to": date_to,
        "district": district if district != "Tutti" else None,
        "urgency": urgency
    }
