"""
SIRAYA Health Navigator - Analytics Dashboard
Phase 2: UI Layer Implementation

This module provides:
- Interactive Streamlit dashboard for SIRAYA analytics
- Hierarchical filters (AUSL → Districts → Temporal)
- Hero metrics visualization
- Choropleth map of Emilia-Romagna
- Sankey diagram for triage flow
- Temporal heatmap
- LLM performance scatter plot
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

# Import data services
from siraya.services.analytics_data_loader import DataLoader, DISTRETTI_ER
from siraya.services.metric_calculator import MetricCalculator

logger = logging.getLogger(__name__)

# Configure Polars display
pl.Config.set_tbl_rows(50)


class DashboardUI:
    """Orchestratore UI della dashboard SIRAYA"""
    
    def __init__(self):
        self.setup_page_config()
        self.init_session_state()
        self.inject_custom_css()
    
    def setup_page_config(self):
        """Configure Streamlit page settings"""
        # Note: set_page_config must be called before any other Streamlit commands
        # This is handled in main() before instantiating DashboardUI
        pass
    
    def init_session_state(self):
        """Pattern VINCOLANTE per gestione stato"""
        defaults = {
            "selected_ausl": [],  # Lista AUSL selezionate
            "selected_distretti": [],  # Filtro gerarchico
            "date_start": datetime.now() - timedelta(days=90),
            "date_end": datetime.now(),
            "force_reload": False,
            "last_clicked_district": None  # Per cross-filtering mappa
        }
        for key, val in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = val
    
    def inject_custom_css(self):
        """Stili SIRAYA - palette medica professionale"""
        st.markdown("""
        <style>
            /* Nascondi menu Streamlit default */
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            
            /* Palette SIRAYA */
            :root {
                --primary-blue: #1E40AF;
                --secondary-teal: #0D9488;
                --alert-red: #DC2626;
                --warning-orange: #F59E0B;
                --success-green: #059669;
            }
            
            /* Header metriche */
            div[data-testid="metric-container"] {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            
            div[data-testid="metric-container"] label {
                color: white !important;
            }
            
            div[data-testid="metric-container"] [data-testid="stMetricValue"] {
                color: white !important;
            }
            
            div[data-testid="metric-container"] [data-testid="stMetricDelta"] {
                color: #f0f0f0 !important;
            }
            
            /* Sidebar professionale */
            section[data-testid="stSidebar"] {
                background: linear-gradient(180deg, #1E3A8A 0%, #1E40AF 100%);
            }
            
            section[data-testid="stSidebar"] * {
                color: white !important;
            }
            
            section[data-testid="stSidebar"] .stButton button {
                background-color: rgba(255,255,255,0.2);
                border: 1px solid white;
            }
            
            section[data-testid="stSidebar"] .stButton button:hover {
                background-color: rgba(255,255,255,0.3);
            }
        </style>
        """, unsafe_allow_html=True)
    
    def render_sidebar(self, df_master: pl.DataFrame):
        """
        Sidebar con filtri gerarchici AUSL → Distretti → Temporali
        
        LOGICA CRITICA:
        - Cambio AUSL → resetta selezione Distretti
        - Distretti disponibili dipendono da AUSL selezionate
        """
        with st.sidebar:
            st.title("🏥 SIRAYA Analytics")
            st.markdown("---")
            st.title("🔍 Filtri Analisi")
            
            # --- FILTRO TEMPORALE ---
            st.subheader("📅 Periodo")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Oggi", use_container_width=True):
                    st.session_state.date_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    st.session_state.date_end = datetime.now()
                    st.rerun()
            with col2:
                if st.button("Ultimi 7gg", use_container_width=True):
                    st.session_state.date_start = datetime.now() - timedelta(days=7)
                    st.session_state.date_end = datetime.now()
                    st.rerun()
            
            # Date range picker
            start_date = st.date_input(
                "Data inizio",
                value=st.session_state.date_start,
                key="date_start_picker"
            )
            end_date = st.date_input(
                "Data fine",
                value=st.session_state.date_end,
                key="date_end_picker"
            )
            
            # Update session state
            if start_date != st.session_state.date_start.date():
                st.session_state.date_start = datetime.combine(start_date, datetime.min.time())
            if end_date != st.session_state.date_end.date():
                st.session_state.date_end = datetime.combine(end_date, datetime.max.time())
            
            st.markdown("---")
            
            # --- FILTRO AUSL ---
            st.subheader("🏥 AUSL")
            all_ausl = list(DISTRETTI_ER.keys())
            
            selected_ausl = st.multiselect(
                "Seleziona AUSL",
                options=all_ausl,
                default=st.session_state.selected_ausl,
                key="ausl_selector"
            )
            
            # Aggiorna stato E resetta distretti se AUSL cambia
            if selected_ausl != st.session_state.selected_ausl:
                st.session_state.selected_ausl = selected_ausl
                st.session_state.selected_distretti = []
            
            # --- FILTRO DISTRETTI (GERARCHICO) ---
            st.subheader("📍 Distretti")
            
            # Calcola distretti disponibili basati su AUSL selezionate
            available_districts = []
            if selected_ausl:
                for ausl in selected_ausl:
                    available_districts.extend(DISTRETTI_ER[ausl])
            else:
                # Se nessuna AUSL selezionata, mostra tutti i distretti
                for dists in DISTRETTI_ER.values():
                    available_districts.extend(dists)
            
            selected_districts = st.multiselect(
                "Seleziona Distretti",
                options=available_districts,
                default=[d for d in st.session_state.selected_distretti 
                        if d in available_districts],
                key="district_selector"
            )
            st.session_state.selected_distretti = selected_districts
            
            st.markdown("---")
            
            # --- RESET ---
            if st.button("🔄 Reset Filtri", type="primary", use_container_width=True):
                for key in list(st.session_state.keys()):
                    if key.startswith('selected_') or key.startswith('date_'):
                        del st.session_state[key]
                st.rerun()
            
            st.markdown("---")
            st.caption(f"📊 Dataset: {df_master.height:,} sessioni totali")
    
    @staticmethod
    def apply_filters(df: pl.DataFrame) -> pl.DataFrame:
        """
        Applica mascheramento in-memory del dataset master
        
        CRUCIALE: Non muta il dataframe cachato, restituisce SLICE
        """
        filtered = df.clone()
        
        # Filtro temporale - handle timezone compatibility
        if 'timestamp_finale' in filtered.columns:
            # Convert session state dates to UTC-aware datetimes to match column timezone
            from datetime import timezone
            
            date_start = st.session_state.date_start
            date_end = st.session_state.date_end
            
            # Make timezone-aware if not already
            if date_start.tzinfo is None:
                date_start = date_start.replace(tzinfo=timezone.utc)
            if date_end.tzinfo is None:
                date_end = date_end.replace(tzinfo=timezone.utc)
            
            # Filter with timezone-aware datetimes
            filtered = filtered.filter(
                (pl.col('timestamp_finale') >= date_start) &
                (pl.col('timestamp_finale') <= date_end)
            )
        
        # Filtro AUSL → Distretti
        if st.session_state.selected_distretti:
            filtered = filtered.filter(
                pl.col('distretto').is_in(st.session_state.selected_distretti)
            )
        elif st.session_state.selected_ausl:
            # Se solo AUSL selezionate, includi TUTTI i loro distretti
            ausl_districts = []
            for ausl in st.session_state.selected_ausl:
                ausl_districts.extend(DISTRETTI_ER[ausl])
            filtered = filtered.filter(pl.col('distretto').is_in(ausl_districts))
        
        return filtered
    
    def render_hero_metrics(self, df: pl.DataFrame):
        """4 KPI principali in cards colorate"""
        
        if df.height == 0:
            st.warning("⚠️ Nessun dato disponibile per i filtri selezionati.")
            return
        
        # Calcola metriche aggregate
        total_triage = df.height
        avg_time = df['tempo_totale_ms'].mean() / 1000 if 'tempo_totale_ms' in df.columns else 0
        
        # Count urgent cases
        urgent = 0
        if 'codice_triage_finale' in df.columns:
            urgent = df.filter(
                pl.col('codice_triage_finale').is_in(['ROSSO', 'ARANCIONE', '3', '2'])
            ).height
        
        tasso_urgenza = (urgent / total_triage * 100) if total_triage > 0 else 0
        tasso_abbandono = MetricCalculator.calcola_tasso_abbandono(df)
        
        # Display metrics in 4 columns
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                label="📊 Totale Triage",
                value=f"{total_triage:,}",
                delta=None
            )
        
        with col2:
            st.metric(
                label="⏱️ Tempo Medio",
                value=f"{avg_time:.1f}s",
                delta=None
            )
        
        with col3:
            # Colorazione semaforica
            urgenza_color = "🔴" if tasso_urgenza > 25 else "🟡" if tasso_urgenza > 10 else "🟢"
            st.metric(
                label=f"{urgenza_color} Codici Urgenti",
                value=f"{tasso_urgenza:.1f}%",
                delta=f"{urgent} casi"
            )
        
        with col4:
            st.metric(
                label="🚪 Tasso Abbandono",
                value=f"{tasso_abbandono:.1f}%",
                delta=None
            )
    
    def render_choropleth_map(self, df: pl.DataFrame, geojson: Dict):
        """
        Mappa interattiva con volumi triage per distretto
        """
        st.subheader("🗺️ Distribuzione Geografica Triage")
        
        if df.height == 0 or 'distretto' not in df.columns:
            st.warning("⚠️ Nessun dato disponibile per la mappa.")
            return
        
        # Aggrega volumi per distretto
        map_data = (
            df.filter(pl.col('distretto') != 'Non Identificato')
            .group_by('distretto')
            .agg([
                pl.count().alias('volume_triage'),
                pl.col('specialita').mode().first().alias('specialita_prevalente')
            ])
            .to_pandas()  # Plotly richiede Pandas
        )
        
        if map_data.empty:
            st.warning("⚠️ Nessun dato georeferenziato disponibile.")
            return
        
        # For this demo, create a simple bar chart since proper GeoJSON mapping requires more setup
        fig = px.bar(
            map_data.sort_values('volume_triage', ascending=False),
            x='distretto',
            y='volume_triage',
            color='volume_triage',
            color_continuous_scale='Viridis',
            hover_data=['specialita_prevalente'],
            labels={'volume_triage': 'Volume Triage', 'distretto': 'Distretto'},
            title='Distribuzione per Distretto Sanitario'
        )
        
        fig.update_layout(
            height=500,
            xaxis_tickangle=-45,
            showlegend=False
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    def render_sankey_flow(self, df: pl.DataFrame):
        """
        Diagramma Sankey: Intent → Specialità → Codice Triage → Struttura
        
        Mostra il "percorso" dei pazienti attraverso il sistema
        """
        st.subheader("🌊 Flusso del Triage Sanitario")
        
        if df.height == 0:
            st.warning("⚠️ Nessun dato disponibile per il diagramma di flusso.")
            return
        
        # Prepara nodi e link
        nodes = []
        nodes_map = {}  # {label: index}
        
        # Columns to include in flow
        flow_cols = ['detected_intent', 'specialita', 'codice_triage_finale', 'struttura_suggerita']
        
        # Collect all unique values
        for col in flow_cols:
            if col in df.columns:
                unique_vals = df[col].unique().to_list()
                for val in unique_vals:
                    if val and val != "N/D" and val not in nodes_map:
                        nodes_map[val] = len(nodes)
                        nodes.append(val)
        
        if len(nodes) < 2:
            st.info("ℹ️ Dati insufficienti per creare il diagramma di flusso.")
            return
        
        # Link: conta transizioni tra colonne consecutive
        links = {"source": [], "target": [], "value": []}
        
        transitions = [
            ('detected_intent', 'specialita'),
            ('specialita', 'codice_triage_finale'),
            ('codice_triage_finale', 'struttura_suggerita')
        ]
        
        for src_col, tgt_col in transitions:
            if src_col not in df.columns or tgt_col not in df.columns:
                continue
                
            flow = (
                df.filter(
                    (pl.col(src_col).is_not_null()) &
                    (pl.col(tgt_col).is_not_null()) &
                    (pl.col(src_col) != "N/D") & 
                    (pl.col(tgt_col) != "N/D")
                )
                .group_by([src_col, tgt_col])
                .agg(pl.count().alias('flow_count'))
            )
            
            for row in flow.iter_rows(named=True):
                src_val = row[src_col]
                tgt_val = row[tgt_col]
                
                if src_val in nodes_map and tgt_val in nodes_map:
                    links["source"].append(nodes_map[src_val])
                    links["target"].append(nodes_map[tgt_val])
                    links["value"].append(row['flow_count'])
        
        if not links["source"]:
            st.info("ℹ️ Nessun flusso trovato nei dati.")
            return
        
        # Costruisci figura Sankey
        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=15,
                thickness=20,
                line=dict(color="black", width=0.5),
                label=nodes,
                color="lightblue"
            ),
            link=dict(
                source=links["source"],
                target=links["target"],
                value=links["value"],
                color="rgba(0,0,255,0.2)"
            )
        )])
        
        fig.update_layout(
            title_text="Percorso Diagnostico dei Pazienti",
            font_size=10,
            height=500
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    def render_temporal_heatmap(self, df: pl.DataFrame):
        """
        Heatmap: Ore (X) × Giorni settimana (Y) → Intensità colore = Volume
        
        Identifica picchi di carico del sistema
        """
        st.subheader("🔥 Picchi di Richieste per Orario")
        
        if df.height == 0 or 'timestamp_finale' not in df.columns:
            st.warning("⚠️ Nessun dato disponibile per l'heatmap temporale.")
            return
        
        # Estrai ora e giorno della settimana
        heatmap_data = (
            df.with_columns([
                pl.col('timestamp_finale').dt.hour().alias('ora'),
                pl.col('timestamp_finale').dt.weekday().alias('giorno_settimana')
            ])
            .group_by(['ora', 'giorno_settimana'])
            .agg(pl.count().alias('volume'))
            .to_pandas()
        )
        
        if heatmap_data.empty:
            st.info("ℹ️ Nessun dato temporale disponibile.")
            return
        
        # Pivot per heatmap
        pivot = heatmap_data.pivot(
            index='giorno_settimana',
            columns='ora',
            values='volume'
        ).fillna(0)
        
        # Mappa giorni → nomi italiani
        giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
        pivot.index = [giorni[int(i)] if i < 7 else f"Giorno {int(i)}" for i in pivot.index]
        
        fig = px.imshow(
            pivot,
            labels=dict(x="Ora del Giorno", y="Giorno", color="N° Richieste"),
            x=[f"{h:02d}:00" for h in range(24)],
            y=pivot.index,
            color_continuous_scale="Reds",
            aspect="auto"
        )
        
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)
    
    def render_llm_performance_scatter(self, df: pl.DataFrame):
        """
        Scatter: Tokens Used (X) vs Processing Time (Y)
        Colore: Model Version | Hover: Session ID
        
        Identifica outliers di performance
        """
        st.subheader("⚙️ Performance Tecnica Modelli LLM")
        
        required_cols = ['tokens_totali', 'tempo_totale_ms', 'session_id']
        if df.height == 0 or not all(col in df.columns for col in required_cols):
            st.warning("⚠️ Dati insufficienti per l'analisi di performance.")
            return
        
        scatter_data = df.select([
            'session_id',
            'tokens_totali',
            'tempo_totale_ms',
            'model_version'
        ]).to_pandas()
        
        if scatter_data.empty:
            st.info("ℹ️ Nessun dato di performance disponibile.")
            return
        
        fig = px.scatter(
            scatter_data,
            x='tokens_totali',
            y='tempo_totale_ms',
            color='model_version',
            hover_data=['session_id'],
            labels={
                'tokens_totali': 'Tokens Consumati',
                'tempo_totale_ms': 'Tempo Elaborazione (ms)'
            },
            title="Efficienza Costo-Tempo per Versione Modello"
        )
        
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)


def main():
    """Punto di ingresso applicazione"""
    
    # Set page config (must be first Streamlit command)
    st.set_page_config(
        page_title="SIRAYA Analytics | Emilia-Romagna",
        page_icon="🏥",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Initialize dashboard
    dashboard = DashboardUI()
    
    # Carica dati (cachati)
    with st.spinner("🔄 Caricamento dati..."):
        df_master, geojson = DataLoader.load_master_data()
        
        if df_master.height > 0:
            df_master = MetricCalculator.calcola_iui(df_master)
    
    # Render sidebar (aggiorna session_state)
    dashboard.render_sidebar(df_master)
    
    # Applica filtri
    df_filtered = dashboard.apply_filters(df_master)
    
    # Header
    st.title("🏥 SIRAYA Healthcare Analytics Dashboard")
    st.caption(f"Regione Emilia-Romagna | {df_filtered.height:,} sessioni analizzate")
    
    # Riga 1: Metriche
    dashboard.render_hero_metrics(df_filtered)
    
    st.divider()
    
    # Riga 2: Mappa + Heatmap
    col1, col2 = st.columns([3, 2])
    with col1:
        dashboard.render_choropleth_map(df_filtered, geojson)
    with col2:
        dashboard.render_temporal_heatmap(df_filtered)
    
    st.divider()
    
    # Riga 3: Sankey + Scatter
    col3, col4 = st.columns(2)
    with col3:
        dashboard.render_sankey_flow(df_filtered)
    with col4:
        dashboard.render_llm_performance_scatter(df_filtered)
    
    # Footer: Export dati
    st.divider()
    
    if df_filtered.height > 0:
        csv = df_filtered.to_pandas().to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Scarica Dataset Filtrato (CSV)",
            data=csv,
            file_name=f"siraya_export_{datetime.now():%Y%m%d}.csv",
            mime="text/csv"
        )
    
    # Footer info
    st.markdown("---")
    st.caption("© 2026 SIRAYA Health Navigator - Regione Emilia-Romagna")


if __name__ == "__main__":
    main()
