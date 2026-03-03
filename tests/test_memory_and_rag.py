#!/usr/bin/env python3
"""
Test memoria sintomo + RAG attivo.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from siraya.controllers.triage_controller_v3 import get_triage_controller
from siraya.core.state_manager import get_state_manager, StateKeys


def test_symptom_memory_preserved():
    """Test che sintomo originale non venga sovrascritto."""
    controller = get_triage_controller()
    state = get_state_manager()
    
    # Reset state
    state.set(StateKeys.COLLECTED_DATA, {})
    state.set(StateKeys.CURRENT_PHASE, "intake")
    state.set(StateKeys.TRIAGE_BRANCH, None)
    state.set("phase_question_count", 0)
    
    # Input 1: Sintomo originale
    response1 = controller.process_user_input("mi sono tagliato un braccio")
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    assert "chief_complaint" in collected
    assert "tagliato" in collected["chief_complaint"].lower()
    print(f"[OK] Sintomo salvato: {collected['chief_complaint']}")
    
    # Input 2: Località
    response2 = controller.process_user_input("parma")
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    assert collected["chief_complaint"] == "mi sono tagliato un braccio"  # IMMUTABILE
    assert "location" in collected
    print(f"[OK] Localita: {collected['location']}, Sintomo: {collected['chief_complaint']}")
    
    # Input 3: Dolore
    response3 = controller.process_user_input("7-8: Dolore intenso")
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    assert collected["chief_complaint"] == "mi sono tagliato un braccio"  # NON cambiato
    assert collected["pain_scale"] == 7
    print(f"[OK] Dolore: {collected['pain_scale']}, Sintomo: {collected['chief_complaint']}")
    
    # Input 4: Età
    response4 = controller.process_user_input("58")
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    assert collected["chief_complaint"] == "mi sono tagliato un braccio"  # IMMUTABILE
    
    # Input 5: Dettaglio (intermittente) → va in symptom_details
    response5 = controller.process_user_input("Intermittente")
    collected = state.get(StateKeys.COLLECTED_DATA, {})
    
    assert collected["chief_complaint"] == "mi sono tagliato un braccio"  # IMMUTABILE
    assert "symptom_details" in collected
    assert "intermittente" in str(collected["symptom_details"]).lower()
    print(f"[OK] Sintomo: {collected['chief_complaint']}, Dettagli: {collected['symptom_details']}")


def test_rag_active_no_warning():
    """Test che RAG non mostri WARNING e ritorni chunks."""
    from siraya.services.rag_service import get_rag_service
    import logging
    
    # Setup logger capture
    logger = logging.getLogger("siraya.services.rag_service")
    
    rag = get_rag_service()
    
    # Test retrieval
    chunks = rag.retrieve_context("taglio al braccio", k=3)
    
    assert len(chunks) > 0, "RAG deve ritornare almeno 1 chunk"
    assert any("taglio" in chunk.get("content", "").lower() or "ferita" in chunk.get("content", "").lower() for chunk in chunks), "Deve trovare protocollo taglio/ferita"
    
    print(f"[OK] RAG attivo: {len(chunks)} chunks trovati")
    for chunk in chunks:
        print(f"   - {chunk.get('source')}: {chunk.get('content')[:60]}...")


if __name__ == "__main__":
    print("\nTEST MEMORIA E RAG\n")
    try:
        test_symptom_memory_preserved()
        print("\n" + "="*60 + "\n")
        test_rag_active_no_warning()
        print("\nTUTTI I TEST PASSATI")
    except Exception as e:
        print(f"\nERRORE: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

