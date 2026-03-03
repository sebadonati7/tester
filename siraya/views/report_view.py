"""
SIRAYA Health Navigator - Report View
V1.0: SBAR report generation and export.

This view:
- Displays collected patient data
- Shows SBAR summary
- Provides PDF download
"""

import streamlit as st
from typing import Optional
from datetime import datetime

from ..core.state_manager import get_state_manager, StateKeys
from ..core.authentication import check_privacy_accepted
from ..services.pdf_service import get_pdf_service


def render() -> None:
    """
    Render the report view.
    
    Main entry point for report generation.
    """
    if not check_privacy_accepted():
        st.warning("⚠️ Accetta l'informativa privacy per accedere al report.")
        return
    
    st.title("📋 Report SBAR")
    st.markdown("---")
    
    state = get_state_manager()
    
    # Get patient data
    patient_data = state.get_patient_data()
    
    # Check if we have data
    if not patient_data.get("chief_complaint") and not patient_data.get("location"):
        st.warning("⚠️ Non ci sono ancora dati sufficienti per generare un report.")
        st.info("Completa alcune domande nel Chatbot prima di generare il report.")
        
        if st.button("🤖 Vai al Chatbot"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.CHAT)
        return
    
    # Render report sections
    _render_patient_info(patient_data)
    _render_sbar_summary(patient_data)
    _render_recommendation(patient_data)
    _render_export_options(patient_data)


def _render_patient_info(patient_data: dict) -> None:
    """Render patient information table."""
    st.markdown("### 👤 Dati Paziente")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown(f"""
        | Campo | Valore |
        |-------|--------|
        | **Session ID** | `{patient_data.get('session_id', 'N/D')[:8]}...` |
        | **Età** | {patient_data.get('age', 'N/D')} |
        | **Sesso** | {patient_data.get('sex', 'N/D')} |
        | **Località** | {patient_data.get('location', 'N/D')} |
        """)
    
    with col2:
        urgency = patient_data.get('urgency_level', 3)
        urgency_color = {1: "🟢", 2: "🟢", 3: "🟡", 4: "🟠", 5: "🔴"}.get(urgency, "⚪")
        
        st.markdown(f"""
        | Campo | Valore |
        |-------|--------|
        | **Sintomo** | {patient_data.get('chief_complaint', 'N/D')} |
        | **Dolore** | {patient_data.get('pain_scale', 'N/D')}/10 |
        | **Urgenza** | {urgency_color} {urgency}/5 |
        | **Area** | {patient_data.get('specialization', 'Generale')} |
        """)
    
    st.markdown("---")


def _render_sbar_summary(patient_data: dict) -> None:
    """Render SBAR structured summary."""
    st.markdown("### 📝 Report SBAR")
    
    # Generate SBAR
    sbar = _generate_sbar(patient_data)
    
    # Styled SBAR container
    st.markdown("""
    <style>
    .sbar-container {
        background: #ffffff;
        border: 2px solid #4A90E2;
        border-radius: 12px;
        padding: 20px;
        margin: 10px 0;
    }
    .sbar-section {
        margin-bottom: 15px;
        padding-bottom: 15px;
        border-bottom: 1px solid #e5e7eb;
    }
    .sbar-section:last-child {
        margin-bottom: 0;
        padding-bottom: 0;
        border-bottom: none;
    }
    .sbar-label {
        color: #4A90E2;
        font-weight: 600;
        font-size: 0.9em;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # S - Situation
    with st.container():
        st.markdown("**S - SITUATION (Situazione)**")
        st.info(sbar["situation"])
    
    # B - Background
    with st.container():
        st.markdown("**B - BACKGROUND (Contesto)**")
        st.info(sbar["background"])
    
    # A - Assessment
    with st.container():
        st.markdown("**A - ASSESSMENT (Valutazione)**")
        st.info(sbar["assessment"])
    
    # R - Recommendation
    with st.container():
        st.markdown("**R - RECOMMENDATION (Raccomandazione)**")
        st.success(sbar["recommendation"])
    
    st.markdown("---")


def _generate_sbar(patient_data: dict) -> dict:
    """Generate SBAR structured data."""
    # Situation
    complaint = patient_data.get("chief_complaint", "Non specificato")
    pain = patient_data.get("pain_scale")
    pain_text = f"Dolore {pain}/10." if pain is not None else ""
    situation = f"{complaint}. {pain_text}"
    
    # Background
    age = patient_data.get("age")
    sex = patient_data.get("sex")
    location = patient_data.get("location", "Non specificata")
    
    age_text = f"{age} anni" if age else "Età non specificata"
    sex_text = sex or "Sesso non specificato"
    
    background = f"Paziente: {age_text}, {sex_text}. Località: {location}."
    
    # Assessment
    red_flags = patient_data.get("red_flags", [])
    urgency = patient_data.get("urgency_level", 3)
    
    if red_flags:
        flags_text = f"Red flags rilevati: {', '.join(red_flags)}."
    else:
        flags_text = "Nessun red flag rilevato."
    
    assessment = f"{flags_text} Urgenza assegnata: {urgency}/5."
    
    # Recommendation
    specialization = patient_data.get("specialization", "Generale")
    recommendation = f"Area clinica: {specialization}. Consultare un professionista sanitario per valutazione."
    
    return {
        "situation": situation,
        "background": background,
        "assessment": assessment,
        "recommendation": recommendation,
    }


def _render_recommendation(patient_data: dict) -> None:
    """Render facility recommendation."""
    st.markdown("### 🏥 Struttura Consigliata")
    
    location = patient_data.get("location", "")
    urgency = patient_data.get("urgency_level", 3)
    
    if urgency >= 4:
        st.error("🚨 Urgenza alta - Si consiglia Pronto Soccorso o chiamare 118")
    elif urgency >= 3:
        st.warning("⚠️ Urgenza media - Si consiglia CAU o Guardia Medica")
    else:
        st.info("ℹ️ Urgenza bassa - Si consiglia Medico di Medicina Generale")
    
    if location:
        st.markdown(f"📍 Comune di riferimento: **{location}**")
        
        if st.button("🗺️ Vedi Strutture Vicine"):
            from ..core.navigation import switch_to, PageName
            switch_to(PageName.MAP)
    
    st.markdown("---")


def _render_export_options(patient_data: dict) -> None:
    """Render export/download options."""
    st.markdown("### 📥 Esporta Report")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Generate PDF
        pdf_service = get_pdf_service()
        sbar = _generate_sbar(patient_data)
        sbar_text = f"""
SITUAZIONE: {sbar['situation']}
BACKGROUND: {sbar['background']}
VALUTAZIONE: {sbar['assessment']}
RACCOMANDAZIONE: {sbar['recommendation']}
        """
        
        pdf_bytes = pdf_service.generate_sbar_pdf(
            patient_data,
            sbar_text,
            facility_name=None
        )
        
        st.download_button(
            label="📄 Scarica PDF",
            data=pdf_bytes,
            file_name=f"SBAR_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    
    with col2:
        # Copy to clipboard (text version)
        sbar_text_clean = f"""
SIRAYA Health Navigator - Report SBAR
Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}

SITUAZIONE: {sbar['situation']}
BACKGROUND: {sbar['background']}
VALUTAZIONE: {sbar['assessment']}
RACCOMANDAZIONE: {sbar['recommendation']}
        """
        
        st.download_button(
            label="📋 Scarica TXT",
            data=sbar_text_clean.encode(),
            file_name=f"SBAR_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
            mime="text/plain",
            use_container_width=True
        )
    
    st.caption("⚠️ Questo report non costituisce diagnosi medica.")

