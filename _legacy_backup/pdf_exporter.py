"""
PDF Exporter for SIRAYA Health Navigator
Generates professional triage reports with logo, SBAR clinical summary, and urgency level.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import io

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False
    FPDF = None

logger = logging.getLogger(__name__)


class TriagePDF(FPDF):
    """Custom PDF class for SIRAYA triage reports."""
    
    def __init__(self):
        super().__init__()
        self.logo_path = None
        self.report_title = "SIRAYA - Triage Report"
        
    def header(self):
        """Add header with logo and title."""
        # Logo (if available)
        logo_png = Path("siraya_logo.png")
        if logo_png.exists():
            try:
                self.image(str(logo_png), 10, 8, 30)
            except Exception as e:
                logger.warning(f"Could not add logo to PDF: {e}")
        
        # Title
        self.set_font('Arial', 'B', 16)
        self.set_text_color(6, 182, 212)  # SIRAYA primary color
        self.cell(0, 10, self.report_title, 0, 0, 'C')
        self.ln(20)
    
    def footer(self):
        """Add footer with page number."""
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')
    
    def chapter_title(self, title: str):
        """Add a chapter title."""
        self.set_font('Arial', 'B', 14)
        self.set_text_color(15, 23, 42)  # Dark gray
        self.cell(0, 10, title, 0, 1, 'L')
        self.ln(2)
    
    def chapter_body(self, body: str):
        """Add chapter body text."""
        self.set_font('Arial', '', 11)
        self.set_text_color(51, 51, 51)
        self.multi_cell(0, 6, body)
        self.ln(4)
    
    def add_field(self, label: str, value: str):
        """Add a labeled field (key-value pair)."""
        self.set_font('Arial', 'B', 11)
        self.set_text_color(71, 85, 105)  # Medium gray
        self.cell(60, 8, f"{label}:", 0, 0, 'L')
        
        self.set_font('Arial', '', 11)
        self.set_text_color(15, 23, 42)
        self.cell(0, 8, value, 0, 1, 'L')
    
    def add_urgency_box(self, urgency_level: str, color_code: str):
        """Add colored urgency level box."""
        # Color mapping
        color_map = {
            "ROSSO": (239, 68, 68),
            "ARANCIONE": (251, 146, 60),
            "GIALLO": (250, 204, 21),
            "VERDE": (34, 197, 94),
            "BIANCO": (226, 232, 240),
            "NERO": (15, 23, 42)
        }
        
        color = color_map.get(urgency_level.upper(), (100, 116, 139))
        
        # Draw colored box
        self.set_fill_color(*color)
        self.set_draw_color(200, 200, 200)
        self.rect(10, self.get_y(), 190, 15, 'DF')
        
        # Add text
        self.set_font('Arial', 'B', 12)
        self.set_text_color(255, 255, 255)
        self.cell(0, 15, f"LIVELLO DI URGENZA: {urgency_level}", 0, 1, 'C')
        self.ln(5)


def generate_triage_pdf(
    session_id: str,
    patient_info: Dict[str, Any],
    clinical_data: Dict[str, Any],
    sbar_report: Dict[str, Any],
    disposition: Dict[str, Any],
    urgency_level: str = "VERDE",
    color_code: str = "VERDE"
) -> Optional[bytes]:
    """
    Generate a PDF triage report.
    
    Args:
        session_id: Unique session identifier
        patient_info: Patient information dict (age, gender, location)
        clinical_data: Clinical data dict (symptoms, pain scale, red flags)
        sbar_report: SBAR structured clinical summary
        disposition: Disposition recommendation
        urgency_level: Urgency level (ROSSO, ARANCIONE, GIALLO, VERDE, BIANCO, NERO)
        color_code: Color code for triage
    
    Returns:
        bytes: PDF file content, or None if generation failed
    """
    if not FPDF_AVAILABLE:
        logger.error("fpdf2 not available - cannot generate PDF")
        return None
    
    try:
        pdf = TriagePDF()
        pdf.add_page()
        
        # ================ HEADER SECTION ================
        pdf.set_font('Arial', '', 10)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 6, f"Generato: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 0, 1, 'R')
        pdf.cell(0, 6, f"ID Sessione: {session_id}", 0, 1, 'R')
        pdf.ln(10)
        
        # ================ URGENCY LEVEL ================
        pdf.add_urgency_box(urgency_level, color_code)
        
        # ================ PATIENT INFO ================
        pdf.chapter_title("1. INFORMAZIONI PAZIENTE")
        
        age = patient_info.get("age", "N/D")
        gender = patient_info.get("gender", "N/D")
        location = patient_info.get("location", "N/D")
        
        pdf.add_field("Età", str(age) if age != "N/D" else "Non specificata")
        pdf.add_field("Sesso", gender if gender != "N/D" else "Non specificato")
        pdf.add_field("Località", location if location != "N/D" else "Non specificata")
        pdf.ln(5)
        
        # ================ CLINICAL DATA ================
        pdf.chapter_title("2. DATI CLINICI")
        
        chief_complaint = clinical_data.get("chief_complaint", "N/D")
        pain_scale = clinical_data.get("pain_scale")
        symptoms = clinical_data.get("symptoms", [])
        onset_time = clinical_data.get("onset_time", "N/D")
        
        pdf.add_field("Sintomo Principale", chief_complaint if chief_complaint != "N/D" else "Non specificato")
        
        if pain_scale is not None:
            pdf.add_field("Scala del Dolore", f"{pain_scale}/10")
        
        if symptoms and isinstance(symptoms, list):
            pdf.add_field("Sintomi Riportati", ", ".join(symptoms) if symptoms else "Nessuno")
        
        pdf.add_field("Insorgenza", onset_time if onset_time != "N/D" else "Non specificata")
        pdf.ln(5)
        
        # ================ SBAR REPORT ================
        pdf.chapter_title("3. SBAR - SINTESI CLINICA")
        
        if sbar_report:
            # Situation
            situation = sbar_report.get("situation", "N/D")
            pdf.set_font('Arial', 'B', 11)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(0, 8, "SITUATION (Situazione):", 0, 1, 'L')
            pdf.chapter_body(situation if situation != "N/D" else "Non disponibile")
            
            # Background
            background = sbar_report.get("background", "N/D")
            pdf.set_font('Arial', 'B', 11)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(0, 8, "BACKGROUND (Anamnesi):", 0, 1, 'L')
            pdf.chapter_body(background if background != "N/D" else "Non disponibile")
            
            # Assessment
            assessment = sbar_report.get("assessment", "N/D")
            pdf.set_font('Arial', 'B', 11)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(0, 8, "ASSESSMENT (Valutazione):", 0, 1, 'L')
            pdf.chapter_body(assessment if assessment != "N/D" else "Non disponibile")
            
            # Recommendation
            recommendation = sbar_report.get("recommendation", "N/D")
            pdf.set_font('Arial', 'B', 11)
            pdf.set_text_color(71, 85, 105)
            pdf.cell(0, 8, "RECOMMENDATION (Raccomandazione):", 0, 1, 'L')
            pdf.chapter_body(recommendation if recommendation != "N/D" else "Non disponibile")
        else:
            pdf.chapter_body("Report SBAR non disponibile")
        
        pdf.ln(5)
        
        # ================ DISPOSITION ================
        pdf.chapter_title("4. RACCOMANDAZIONE FINALE")
        
        if disposition:
            service_type = disposition.get("service_type", "N/D")
            facility_name = disposition.get("facility_name", "N/D")
            urgency = disposition.get("urgency", "N/D")
            rationale = disposition.get("rationale", "N/D")
            
            pdf.add_field("Servizio Consigliato", service_type if service_type != "N/D" else "Non specificato")
            pdf.add_field("Struttura", facility_name if facility_name != "N/D" else "Non specificata")
            pdf.add_field("Urgenza", urgency if urgency != "N/D" else "Non specificata")
            
            if rationale and rationale != "N/D":
                pdf.set_font('Arial', 'B', 11)
                pdf.set_text_color(71, 85, 105)
                pdf.cell(0, 8, "Motivazione:", 0, 1, 'L')
                pdf.chapter_body(rationale)
        else:
            pdf.chapter_body("Raccomandazione non disponibile")
        
        pdf.ln(10)
        
        # ================ DISCLAIMER ================
        pdf.set_font('Arial', 'I', 9)
        pdf.set_text_color(156, 163, 175)
        disclaimer = (
            "DISCLAIMER: Questo report è generato da SIRAYA Health Navigator, un sistema di supporto al triage "
            "basato su intelligenza artificiale. NON sostituisce la valutazione medica professionale. "
            "In caso di emergenza, chiamare il 118. La responsabilità clinica finale rimane del medico curante."
        )
        pdf.multi_cell(0, 5, disclaimer)
        
        # Generate PDF bytes
        pdf_bytes = pdf.output(dest='S').encode('latin-1')
        
        logger.info(f"✅ PDF generated successfully for session {session_id}")
        return pdf_bytes
        
    except Exception as e:
        logger.error(f"❌ Failed to generate PDF: {e}", exc_info=True)
        return None


def export_to_pdf_streamlit(
    session_state: Any,
    filename: Optional[str] = None
) -> Optional[bytes]:
    """
    Export current Streamlit session to PDF.
    Convenience wrapper for Streamlit apps.
    
    Args:
        session_state: Streamlit session_state object
        filename: Optional custom filename
    
    Returns:
        bytes: PDF content or None
    """
    if not filename:
        session_id = getattr(session_state, 'session_id', 'unknown')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"siraya_triage_{session_id}_{timestamp}.pdf"
    
    # Extract data from session_state
    patient_info = {
        "age": getattr(session_state, 'age', None),
        "gender": getattr(session_state, 'gender', None),
        "location": getattr(session_state, 'comune', None)
    }
    
    clinical_data = {
        "chief_complaint": getattr(session_state, 'chief_complaint', None),
        "pain_scale": getattr(session_state, 'pain_scale', None),
        "symptoms": getattr(session_state, 'symptoms', []),
        "onset_time": getattr(session_state, 'onset_time', None)
    }
    
    sbar_report = getattr(session_state, 'sbar_report', {})
    disposition = getattr(session_state, 'disposition', {})
    urgency_level = getattr(session_state, 'urgency_level', "VERDE")
    color_code = getattr(session_state, 'color_code', "VERDE")
    session_id = getattr(session_state, 'session_id', 'unknown')
    
    return generate_triage_pdf(
        session_id=session_id,
        patient_info=patient_info,
        clinical_data=clinical_data,
        sbar_report=sbar_report,
        disposition=disposition,
        urgency_level=urgency_level,
        color_code=color_code
    )


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_pdf_available() -> bool:
    """Check if PDF generation is available."""
    return FPDF_AVAILABLE


def get_pdf_not_available_message() -> str:
    """Get user-friendly message when PDF is not available."""
    return (
        "⚠️ Generazione PDF non disponibile. "
        "Installa fpdf2: `pip install fpdf2>=2.7.0`"
    )

