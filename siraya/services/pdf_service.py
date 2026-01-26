"""
SIRAYA Health Navigator - PDF Service
V1.0: PDF generation for SBAR reports.

This service:
- Generates PDF reports
- Creates SBAR summaries
- Returns bytes for download
"""

import io
from typing import Dict, Any, Optional
from datetime import datetime


# ============================================================================
# PDF SERVICE CLASS
# ============================================================================

class PDFService:
    """
    Service for PDF generation.
    
    Creates downloadable SBAR reports.
    """
    
    def generate_sbar_pdf(
        self,
        patient_data: Dict[str, Any],
        sbar_text: str,
        facility_name: Optional[str] = None
    ) -> bytes:
        """
        Generate SBAR report PDF.
        
        Args:
            patient_data: Patient information dictionary
            sbar_text: SBAR summary text
            facility_name: Recommended facility
            
        Returns:
            PDF as bytes
        """
        # Try to use reportlab if available
        try:
            return self._generate_with_reportlab(patient_data, sbar_text, facility_name)
        except ImportError:
            # Fallback to simple text file
            return self._generate_text_fallback(patient_data, sbar_text, facility_name)
    
    def _generate_with_reportlab(
        self,
        patient_data: Dict[str, Any],
        sbar_text: str,
        facility_name: Optional[str]
    ) -> bytes:
        """Generate PDF using reportlab."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=20,
            textColor=colors.HexColor('#1565C0')
        )
        
        section_style = ParagraphStyle(
            'Section',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10,
            textColor=colors.HexColor('#4A90E2')
        )
        
        elements = []
        
        # Header
        elements.append(Paragraph("SIRAYA Health Navigator", title_style))
        elements.append(Paragraph("Report SBAR", styles['Heading2']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Timestamp
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        elements.append(Paragraph(f"Generato: {now}", styles['Normal']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Patient info table
        patient_info = [
            ["Campo", "Valore"],
            ["Session ID", patient_data.get("session_id", "N/D")[:8]],
            ["Età", str(patient_data.get("age", "N/D"))],
            ["Sesso", patient_data.get("sex", "N/D")],
            ["Località", patient_data.get("location", "N/D")],
        ]
        
        table = Table(patient_info, colWidths=[5*cm, 10*cm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4A90E2')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F8FAFC')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#E5E7EB')),
        ]))
        
        elements.append(table)
        elements.append(Spacer(1, 1*cm))
        
        # SBAR sections
        elements.append(Paragraph("Report SBAR", section_style))
        
        for line in sbar_text.split('\n'):
            if line.strip():
                elements.append(Paragraph(line, styles['Normal']))
                elements.append(Spacer(1, 0.2*cm))
        
        elements.append(Spacer(1, 0.5*cm))
        
        # Recommendation
        if facility_name:
            elements.append(Paragraph("Struttura Consigliata", section_style))
            elements.append(Paragraph(facility_name, styles['Normal']))
        
        # Footer
        elements.append(Spacer(1, 2*cm))
        footer_text = "Questo report è generato automaticamente e non costituisce diagnosi medica."
        elements.append(Paragraph(footer_text, styles['Italic']))
        
        doc.build(elements)
        buffer.seek(0)
        return buffer.getvalue()
    
    def _generate_text_fallback(
        self,
        patient_data: Dict[str, Any],
        sbar_text: str,
        facility_name: Optional[str]
    ) -> bytes:
        """Generate text file as fallback."""
        lines = [
            "=" * 60,
            "SIRAYA Health Navigator - Report SBAR",
            "=" * 60,
            "",
            f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"Session ID: {patient_data.get('session_id', 'N/D')}",
            "",
            "-" * 40,
            "DATI PAZIENTE",
            "-" * 40,
            f"Età: {patient_data.get('age', 'N/D')}",
            f"Sesso: {patient_data.get('sex', 'N/D')}",
            f"Località: {patient_data.get('location', 'N/D')}",
            "",
            "-" * 40,
            "REPORT SBAR",
            "-" * 40,
            sbar_text,
            "",
        ]
        
        if facility_name:
            lines.extend([
                "-" * 40,
                "STRUTTURA CONSIGLIATA",
                "-" * 40,
                facility_name,
                "",
            ])
        
        lines.extend([
            "=" * 60,
            "Questo report non costituisce diagnosi medica.",
            "=" * 60,
        ])
        
        return "\n".join(lines).encode('utf-8')


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_pdf_service: Optional[PDFService] = None


def get_pdf_service() -> PDFService:
    """Get singleton PDF service instance."""
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service

