"""
SIRAYA Health Navigator - Excel Reporter
RAG-Enhanced Analytics V2: Professional Excel report generation with charts.
"""

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
    import xlsxwriter
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False


class SIRAYAExcelReporter:
    """
    Generatore report Excel professional-grade:
    - Multi-sheet (Executive Summary, KPI, Dettagli)
    - Grafici nativi Excel
    - Palette colori medica
    """

    def __init__(self):
        self.color_palette = {
            "primary": "#2C5F8D",
            "secondary": "#4A90E2",
            "danger": "#E74C3C",
            "warning": "#F39C12",
            "success": "#27AE60",
            "header_bg": "#34495E",
            "header_text": "#FFFFFF",
        }

    def generate_professional_report(
        self,
        kpi_data: Dict[str, Any],
        filters: Optional[Dict] = None,
        red_flags_data: Optional[List[Dict]] = None,
        symptoms_data: Optional[List[Dict]] = None,
    ) -> BytesIO:
        """
        Genera report Excel completo.

        Args:
            kpi_data: KPI da analytics_service.calculate_kpi_completo
            filters: Filtri applicati (period, districts)
            red_flags_data: Risultati RAG red flags (opzionale)
            symptoms_data: Risultati estrazione sintomi (opzionale)

        Returns:
            BytesIO buffer Excel
        """
        filters = filters or {}
        if not HAS_XLSX:
            raise ImportError("xlsxwriter e pandas richiesti per Excel report")

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            workbook = writer.book
            formats = self._define_formats(workbook)

            self._create_executive_summary(writer, workbook, formats, kpi_data, filters)
            self._create_volumetric_sheet(writer, workbook, formats, kpi_data)

            if red_flags_data or symptoms_data:
                self._create_clinical_sheet(
                    writer, workbook, formats, red_flags_data, symptoms_data
                )

            self._create_geographic_sheet(writer, workbook, formats, kpi_data)

        buffer.seek(0)
        return buffer

    def _define_formats(self, workbook) -> Dict:
        """Formati Excel riutilizzabili."""
        return {
            "title": workbook.add_format({
                "bold": True, "font_size": 18, "font_color": self.color_palette["primary"],
                "align": "left", "valign": "vcenter",
            }),
            "header": workbook.add_format({
                "bold": True, "bg_color": self.color_palette["header_bg"],
                "font_color": self.color_palette["header_text"],
                "align": "center", "valign": "vcenter", "border": 1,
            }),
            "metric": workbook.add_format({
                "font_size": 14, "bold": True, "align": "center", "valign": "vcenter",
            }),
            "percent": workbook.add_format({"num_format": "0.0%", "align": "center"}),
        }

    def _create_executive_summary(
        self,
        writer: pd.ExcelWriter,
        workbook,
        formats: Dict,
        kpi_data: Dict,
        filters: Dict,
    ) -> None:
        """Sheet Executive Summary."""
        ws = workbook.add_worksheet("Executive Summary")
        ws.write("A1", "SIRAYA Health Navigator - Analytics Report", formats["title"])
        ws.write("A2", f"Generato: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        ws.write("A3", f"Periodo: {filters.get('period', 'N/A')}")
        ws.write("A4", f"Distretti: {', '.join(filters.get('districts', ['Tutti']))}")

        sessioni = kpi_data.get("sessioni_uniche", 0)
        completion = kpi_data.get("tasso_completamento", 0)
        tempo = kpi_data.get("tempo_mediano_triage_minuti", 0)
        prev_rf = kpi_data.get("prevalenza_red_flags", 0)
        if not prev_rf and "red_flags_dettaglio" in kpi_data:
            rf_detail = kpi_data.get("red_flags_dettaglio", {})
            total_rf = sum(rf_detail.values())
            interazioni = kpi_data.get("interazioni_totali", 1)
            prev_rf = (total_rf / interazioni * 100) if interazioni else 0

        row = 6
        ws.write(row, 0, "KPI", formats["header"])
        ws.write(row, 1, "Valore", formats["header"])
        ws.write(row, 2, "Benchmark", formats["header"])
        row += 1
        kpi_rows = [
            ("Sessioni Uniche", sessioni, "> 0"),
            ("Completion Rate", f"{completion:.1f}%", "> 80%"),
            ("Prevalenza Red Flags", f"{prev_rf:.1f}%", "< 30%"),
            ("Tempo Mediano (min)", f"{tempo:.1f}", "< 15"),
        ]
        for name, val, bench in kpi_rows:
            ws.write(row, 0, name)
            ws.write(row, 1, val, formats["metric"])
            ws.write(row, 2, bench)
            row += 1
        ws.set_column("A:A", 28)
        ws.set_column("B:C", 15)

    def _create_volumetric_sheet(
        self,
        writer: pd.ExcelWriter,
        workbook,
        formats: Dict,
        kpi_data: Dict,
    ) -> None:
        """Sheet KPI Volumetrici con throughput."""
        ws = workbook.add_worksheet("KPI Volumetrici")
        throughput = kpi_data.get("throughput_orario", {})
        if isinstance(throughput, dict):
            hours = sorted(throughput.keys())
            ws.write("A1", "Throughput Orario", formats["title"])
            ws.write("A2", "Ora")
            ws.write("B2", "Accessi")
            for i, h in enumerate(hours, start=3):
                ws.write(i, 0, f"{h:02d}:00")
                ws.write(i, 1, throughput[h])
            ws.set_column("A:A", 12)
            ws.set_column("B:B", 10)

    def _create_clinical_sheet(
        self,
        writer: pd.ExcelWriter,
        workbook,
        formats: Dict,
        red_flags_data: Optional[List[Dict]],
        symptoms_data: Optional[List[Dict]],
    ) -> None:
        """Sheet KPI Clinici con analisi AI."""
        ws = workbook.add_worksheet("KPI Clinici")
        ws.write("A1", "Analisi Clinica Avanzata", formats["title"])
        row = 3
        if red_flags_data:
            ws.write(row, 0, "Red Flags Rilevati", formats["header"])
            row += 1
            for rf in red_flags_data:
                if isinstance(rf, dict) and rf.get("red_flags_detected"):
                    ws.write(row, 0, rf.get("session_id", "")[:12])
                    ws.write(row, 1, ", ".join(rf.get("red_flags_detected", [])))
                    ws.write(row, 2, rf.get("urgency_code", ""))
                    ws.write(row, 3, rf.get("confidence_score", 0))
                    row += 1
            row += 2
        if symptoms_data:
            ws.write(row, 0, "Sintomatologia Estratta", formats["header"])
            row += 1
            for s in symptoms_data[:20]:
                if isinstance(s, dict):
                    sym = s.get("symptoms_extracted", s.get("symptoms", {}))
                    if isinstance(sym, dict):
                        for name, detail in list(sym.items())[:5]:
                            ws.write(row, 0, name)
                            ws.write(row, 1, str(detail)[:100])
                            row += 1
        ws.set_column("A:A", 18)
        ws.set_column("B:D", 25)

    def _create_geographic_sheet(
        self,
        writer: pd.ExcelWriter,
        workbook,
        formats: Dict,
        kpi_data: Dict,
    ) -> None:
        """Sheet distribuzione geografica."""
        ws = workbook.add_worksheet("Geografia")
        ws.write("A1", "Copertura Territoriale", formats["title"])
        geo = kpi_data.get("copertura_geografica", {})
        distr = geo.get("distribuzione_distretti", geo.get("distribuzione_geografica", {}))
        if isinstance(distr, dict):
            ws.write("A2", "Distretto")
            ws.write("B2", "Sessioni")
            row = 3
            for dist, count in list(distr.items())[:20]:
                ws.write(row, 0, str(dist))
                ws.write(row, 1, count)
                row += 1
        ws.set_column("A:A", 25)
        ws.set_column("B:B", 12)


def get_excel_reporter() -> SIRAYAExcelReporter:
    """Singleton reporter."""
    if not hasattr(get_excel_reporter, "_instance"):
        get_excel_reporter._instance = SIRAYAExcelReporter()
    return get_excel_reporter._instance
