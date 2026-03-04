"""
Test suite per validare fix metadata parsing.
Usa dati reali dai log Supabase.
"""

import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from siraya.services.metadata_parser import parse_metadata_enhanced
from siraya.services.geographic_mapper import map_comune_to_district


TEST_METADATA = [
    {
        "input": '{"step": "CHIEF_COMPLAINT", "specialization": "Generale", "urgency_level": 3, "location": "Ravenna", "version": "interaction-1.0"}',
        "expected_location": "Ravenna",
        "expected_urgency": 3,
    },
    {
        "input": '{"phase":"INTAKE","urgenza":3,"percorso":"C","collected_data":{"chief_complaint":"Dolore addominale","current_location":"Ravenna"},"question_count":4,"specializzazione":"Generale"}',
        "expected_location": "Ravenna",
        "expected_urgency": 3,
    },
    {
        "input": '{"branch":"C","complete":true}',
        "expected_location": "",
        "expected_urgency": 3,
    },
]


def test_parsing():
    """Test parsing multi-formato."""
    print("=" * 60)
    print("TEST METADATA PARSING")
    print("=" * 60)
    all_ok = True
    for i, case in enumerate(TEST_METADATA, 1):
        print(f"\n[Test {i}] Input: {case['input'][:80]}...")
        result = parse_metadata_enhanced(case["input"])
        success_location = result["comune"] == case["expected_location"]
        success_urgency = result["urgenza"] == case["expected_urgency"]
        if not success_location:
            all_ok = False
        if not success_urgency:
            all_ok = False
        print(f"  Location: {result['comune']} {'[OK]' if success_location else '[FAIL]'}")
        print(f"  Urgenza: {result['urgenza']} {'[OK]' if success_urgency else '[FAIL]'}")
        if not (success_location and success_urgency):
            print(f"  DEBUG: {json.dumps(result, indent=2)}")
    return all_ok


def test_geographic_mapping():
    """Test mappatura geografica con comuni reali dai log."""
    print("\n" + "=" * 60)
    print("TEST GEOGRAPHIC MAPPING")
    print("=" * 60)
    test_comuni = [
        ("Ravenna", "AUSL_Romagna", "Ravenna"),
        ("Bologna", "AUSL_Bologna", "Bologna"),
        ("Budrio", "AUSL_Bologna", "Pianura"),
        ("Piacenza", "AUSL_Piacenza", "Piacenza"),
    ]
    all_ok = True
    for comune, expected_ausl, expected_distretto_fragment in test_comuni:
        result = map_comune_to_district(comune)
        ausl_ok = expected_ausl in result["ausl"] or result["ausl"] == expected_ausl
        dist_ok = expected_distretto_fragment in result["distretto"]
        if not ausl_ok or not dist_ok:
            all_ok = False
        print(f"\n{comune}:")
        print(f"  AUSL: {result['ausl']} {'[OK]' if ausl_ok else '[FAIL]'}")
        print(f"  Distretto: {result['distretto']} {'[OK]' if dist_ok else '[FAIL]'}")
        print(f"  Match Type: {result['match_type']} (confidence: {result['confidence']})")
    return all_ok


if __name__ == "__main__":
    ok1 = test_parsing()
    ok2 = test_geographic_mapping()
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if ok1 and ok2:
        print("[OK] Tutti i test passano. Il fix e' completo.")
    else:
        print("[FAIL] Alcuni test falliscono. Verifica l'implementazione.")
        sys.exit(1)
