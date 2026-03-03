#!/usr/bin/env python3
"""
Test completo post-fix per i 5 problemi critici.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from siraya.controllers.triage_controller_v3 import UnifiedSlotFiller


def test_pain_extraction_no_age_conflict():
    """Test che 7-8 non estrae age=7."""
    # Scenario: User risponde alla scala dolore
    current_data = {
        "chief_complaint": "taglio",
        "location": "Bologna",
        "_current_phase": "pain_scale"  # Context
    }
    
    result = UnifiedSlotFiller.extract("7-8: Forte", current_data)
    
    assert "pain_scale" in result
    assert result["pain_scale"] == 7
    assert "age" not in result  # NO age extraction
    print("[OK] Pain extraction OK, no age conflict")


def test_age_extraction_only_in_demographics():
    """Test che age viene estratto solo in demographics phase."""
    # Scenario 1: In pain_scale phase, "56" non estrae age
    current_data = {
        "_current_phase": "pain_scale"
    }
    result = UnifiedSlotFiller.extract("56", current_data)
    assert "age" not in result
    print("[OK] Age NOT extracted in pain_scale phase")
    
    # Scenario 2: In demographics phase, "56" estrae age
    current_data = {
        "_current_phase": "demographics"
    }
    result = UnifiedSlotFiller.extract("56", current_data)
    assert "age" in result
    assert result["age"] == 56
    print("[OK] Age extracted correctly in demographics phase")


def test_fsm_exits_pain_scale():
    """Test che FSM esce da PAIN_SCALE quando pain_scale è presente."""
    from siraya.controllers.triage_controller_v3 import TriageFSM, TriageBranch, TriagePhase
    from unittest.mock import MagicMock
    
    state = MagicMock()
    fsm = TriageFSM(state)
    
    # Data WITH pain_scale
    data = {
        "chief_complaint": "taglio",
        "location": "Bologna",
        "pain_scale": 7  # Present
    }
    
    # TriageBranch.STANDARD è definito nel controller
    next_phase = fsm.next_phase(
        TriageBranch.STANDARD,
        TriagePhase.PAIN_SCALE,
        data,
        0
    )
    
    assert next_phase == TriagePhase.DEMOGRAPHICS
    print("[OK] FSM exits PAIN_SCALE correctly")
    
    # Data WITHOUT pain_scale
    data_no_pain = {
        "chief_complaint": "taglio",
        "location": "Bologna"
        # NO pain_scale
    }
    
    next_phase_stay = fsm.next_phase(
        TriageBranch.STANDARD,
        TriagePhase.PAIN_SCALE,
        data_no_pain,
        0
    )
    
    assert next_phase_stay == TriagePhase.PAIN_SCALE
    print("[OK] FSM stays in PAIN_SCALE if no pain_scale")


def test_rag_no_hardcoded_return():
    """Test RAG non ha return [] hardcoded e ritorna sempre chunks."""
    from siraya.services.rag_service import get_rag_service
    
    rag = get_rag_service()
    chunks = rag.retrieve_context("taglio al braccio", k=3)
    
    assert len(chunks) > 0, "RAG deve ritornare almeno 1 chunk"
    assert any("taglio" in c.get("content", "").lower() or "ferita" in c.get("content", "").lower() for c in chunks), "Deve trovare protocollo taglio/ferita"
    print(f"[OK] RAG active: {len(chunks)} chunks, no hardcoded return []")


def test_pain_parsing_safe():
    """Test parsing sicuro di pain_scale string '7-8'."""
    # Simula sidebar parsing
    import re
    
    pain = "7-8"
    if isinstance(pain, str):
        match = re.search(r'(\d+)', pain)
        if match:
            pain_val = int(match.group(1))
        else:
            pain_val = None
    else:
        pain_val = int(pain)
    
    assert pain_val == 7
    print("[OK] Safe pain parsing for '7-8' string")


if __name__ == "__main__":
    print("\nTEST FIXES CRITICI\n")
    try:
        test_pain_extraction_no_age_conflict()
        test_age_extraction_only_in_demographics()
        test_fsm_exits_pain_scale()
        test_rag_no_hardcoded_return()
        test_pain_parsing_safe()
        print("\nTUTTI I TEST PASSATI")
    except Exception as e:
        print(f"\nERRORE: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

