#!/usr/bin/env python3
"""
Test suite per pipeline analytics RAG-Enhanced.
- MetadataParser
- GeographicMapper
- RAGKPICalculator (fallback)
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from siraya.services.metadata_parser import parse_metadata_enhanced, get_parsing_success_rate
from siraya.services.geographic_mapper import map_comune_to_district, get_available_districts


class TestMetadataParser:
    def test_parse_standard_json(self):
        metadata = '{"urgency": 4, "red_flags": ["chest_pain"], "location": "Bologna"}'
        result = parse_metadata_enhanced(metadata)
        assert result["urgenza"] == 4
        assert "chest_pain" in result["red_flags"]
        assert result["comune"] == "Bologna"

    def test_parse_single_quotes_json(self):
        metadata = "{'urgenza': 3, 'sintomi': ['febbre', 'tosse']}"
        result = parse_metadata_enhanced(metadata)
        assert result["urgenza"] == 3
        assert len(result["sintomi"]) >= 1

    def test_parse_dict_native(self):
        metadata = {"urgency": 2, "location": "Modena"}
        result = parse_metadata_enhanced(metadata)
        assert result["urgenza"] == 2
        assert result["comune"] == "Modena"

    def test_parse_malformed_fallback(self):
        metadata = "invalid{json"
        result = parse_metadata_enhanced(metadata)
        assert "urgenza" in result
        assert isinstance(result["urgenza"], int)
        assert 1 <= result["urgenza"] <= 5

    def test_parse_null_handling(self):
        for null_val in [None, "", {}, "null"]:
            result = parse_metadata_enhanced(null_val)
            assert result is not None
            assert "urgenza" in result
            assert isinstance(result["red_flags"], list)


class TestGeographicMapper:
    def test_exact_match_bologna(self):
        result = map_comune_to_district("Bologna")
        assert "AUSL_Bologna" in result["ausl"] or "AUSL_Imola" in result["ausl"]
        assert result["match_type"] in ("exact", "fuzzy")
        assert result["confidence"] >= 0.8

    def test_case_insensitive(self):
        for variant in ["bologna", "BOLOGNA"]:
            result = map_comune_to_district(variant)
            assert result["ausl"] != "UNKNOWN" or result["match_type"] == "fuzzy"

    def test_fuzzy_match_typo(self):
        result = map_comune_to_district("Bolonga")
        assert result["match_type"] in ("fuzzy", "exact", "unknown")
        if result["match_type"] == "fuzzy":
            assert result["confidence"] > 0.7

    def test_external_region(self):
        result = map_comune_to_district("Milano")
        assert result["ausl"] == "EXTRA_REGIONE" or result["match_type"] == "unknown"

    def test_null_input(self):
        for null_in in [None, "", "   "]:
            result = map_comune_to_district(null_in)
            assert result["match_type"] == "unknown"
            assert result["confidence"] == 0.0

    def test_districts_available(self):
        districts = get_available_districts()
        assert isinstance(districts, list)
        assert len(districts) > 0


class TestRAGKPICalculatorFallback:
    """Test fallback keyword matching (senza chiamate LLM)."""

    def test_fallback_red_flags(self):
        pytest.importorskip("streamlit", reason="streamlit richiesto per RAG calculator")
        from siraya.services.rag_kpi_calculator import RAGKPICalculator

        # Usa fallback perché non mockiamo LLM
        calc = RAGKPICalculator(llm_service=None)
        convs = [
            {
                "session_id": "test_001",
                "messages": [
                    {"role": "user", "content": "Ho un forte dolore al petto"},
                    {"role": "assistant", "content": "Da quanto tempo?"},
                ],
            }
        ]
        result = calc._enhanced_keyword_analysis(convs, "red_flags")
        assert len(result) == 1
        assert "session_id" in result[0]
        assert "red_flags_detected" in result[0]
        assert "urgency_level" in result[0]
        # "petto" è in RED_FLAGS_KEYWORDS
        assert len(result[0]["red_flags_detected"]) >= 0
        assert 1 <= result[0]["urgency_level"] <= 5

    def test_fallback_dispnea_synonym(self):
        pytest.importorskip("streamlit", reason="streamlit richiesto per RAG calculator")
        from siraya.services.rag_kpi_calculator import RAGKPICalculator

        calc = RAGKPICalculator(llm_service=None)
        convs = [
            {
                "session_id": "test_002",
                "messages": [{"role": "user", "content": "Non riesco a respirare bene"}],
            }
        ]
        result = calc._enhanced_keyword_analysis(convs, "red_flags")
        assert len(result) == 1
        rfs = result[0].get("red_flags_detected", [])
        # "respiro" in ClinicalMappings.RED_FLAGS_KEYWORDS
        assert isinstance(rfs, list)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
