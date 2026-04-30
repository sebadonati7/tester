# SIRAYA Architecture V5.0

## Overview

SIRAYA è un bot di triage medico che opera su WhatsApp/Web con 4 percorsi principali:

```
User Input
    │
    ▼
RouteArbitrator
    │
    ├─── A (RED/ORANGE) ─── Emergency Path
    ├─── B (BLACK) ──────── Mental Health → ProtocolDrivenExecutor
    ├─── C (GREEN) ──────── Standard Triage → FSM + QuestionGenerator
    └─── INFO ───────────── Informational → InfoResponseGenerator
```

---

## Component Diagram

```
siraya/
├── controllers/
│   ├── triage_controller_v3.py     # Main orchestrator
│   ├── route_arbitrator.py          # Routing decision (LLMJudge + SmartRouter)
│   ├── smart_router.py              # Keyword-based router + escalation check
│   ├── data_acquisition_manager.py  # Anti-hallucination data acquisition
│   ├── protocol_driven_executor.py  # Path BLACK: protocol-driven flow
│   └── info_response_generator.py   # Path INFO: conversational responses
├── services/
│   ├── llm_service.py               # LLM client (Groq + Gemini)
│   ├── rag_service.py               # RAG via Supabase protocol_chunks
│   ├── data_loader.py               # Healthcare facility data
│   └── db_service.py                # Session persistence
├── core/
│   └── state_manager.py             # Session state (Streamlit)
└── config/
    ├── settings.py                  # API keys, paths
    └── emergency_keywords.json      # Classification keywords
```

---

## Path A — Emergency (RED/ORANGE)

**Trigger**: Emergency keywords o LLMJudge `is_emergency=True`

**Flow**:
```
INTAKE → LOCALIZATION → FAST_TRIAGE (3 domande) → OUTCOME (SBAR)
```

**Features**:
- Fast triage con domande cliniche mirate
- Outcome: Pronto Soccorso più vicino + 118 warning
- Escalation C→A: `SmartRouter.check_escalation()` (threshold=1)

---

## Path B — Mental Health (BLACK)

**Trigger**: Mental health keywords o LLMJudge `urgency_hint="mental_health"`

**Flow** (nuovo — protocol-driven):
```
identify_concern_type()
    │
    ▼
load_protocol_from_supabase() ──── Supabase: mental_health_protocols
    │                              └── Fallback: embedded protocols
    ▼
execute_protocol() loop
    │ (LLM genera domanda dal chunk)
    │
    ▼ (quando completed=True)
stratify_risk()
    │
    ▼
_generate_mental_health_outcome()
```

**Concern Types**:
- `suicidal_ideation` → ASQ protocol → SPDC/Crisis Center/CSM
- `depression` → PHQ-9 → CSM (score-based)
- `eating_disorder` → DCA → Centro DCA
- `substance_abuse` → SERT → SerD/SERT

---

## Path C — Standard Triage (GREEN)

**Trigger**: Default (nessun keyword specifico)

**Flow**:
```
INTAKE → CHIEF_COMPLAINT → LOCALIZATION → PAIN_SCALE → DEMOGRAPHICS
       → CLINICAL_TRIAGE (5 domande) → OUTCOME (SBAR)
```

**Features**:
- `UnifiedSlotFiller`: LLM per sintomi, regex per dati strutturati
- `LLMJudge`: classificazione semantica input
- `QuestionGenerator`: domande cliniche per dimensione (da `CLINICAL_DIMENSIONS`)
- `OutcomeGenerator`: SBAR via LLM JSON schema

---

## Path INFO — Informational

**Trigger**: Info keywords o LLMJudge `is_info_request=True`

**Flow** (nuovo — conversazionale):
```
extract_query_intent()
    │
    ▼
retrieve_context_for_info() ──── Supabase: protocol_chunks
    │                            (filtra per intent + location)
    ▼
Se risultati: _generate_from_rag_results() → LLM risposta adattata
Se vuoti:     _generate_refinement_or_alternative() → NO fallback generico
```

**Intent Categories**:
- `OPERATING_HOURS`: orari → risposta concisa
- `FACILITY_LOCATION`: indirizzo → pratico con indicazioni
- `SERVICE_INFO`: cos'è il servizio → strutturato
- `COST_INFO`: costi/esenzioni → chiaro
- `PROCEDURE_INFO`: come accedere → step-by-step
- `GENERAL_HEALTH`: info salute → con disclaimer
- `PRESCRIPTION_INFO`: ricette → pratico
- `OTHER`: altro → conversazionale

---

## Data Flow — Anti-Hallucination

```
UserInput
    │
    ▼
DataAcquisitionManager.extract_and_validate()
    │
    ├── user-explicit data ──→ confirmed → collected (immediato)
    └── LLM-inferred data ──→ pending_validation → conferma pre-SBAR
                                    │
                                    ▼
                         generate_validation_message()
                                    │
                                    ▼ (risposta utente)
                         process_validation_response()
                                    │
                         ├── confermato → collected
                         └── rifiutato → scartato
```

---

## Routing Logic

```
RouteArbitrator.route(user_input, current_data, current_branch, current_phase)
    │
    ├── current_branch is None (primo turno):
    │   ├── LLMJudge available:
    │   │   ├── is_emergency → A
    │   │   ├── mental_health → B
    │   │   ├── is_info_request → INFO
    │   │   └── specific/generic → SmartRouter check → C/A/B
    │   └── No LLMJudge → SmartRouter.route()
    │
    ├── current_branch in (A, B, INFO): → continuity (unchanged)
    │
    └── current_branch == C:
        └── SmartRouter.check_escalation() (threshold=1)
            ├── True → A (escalation)
            └── False → C (maintained)
```

---

## State Keys

| Key | Description |
|---|---|
| `TRIAGE_BRANCH` | Current branch (A/B/C/INFO) |
| `TRIAGE_PATH` | Same as TRIAGE_BRANCH (legacy sync) |
| `CURRENT_PHASE` | FSM phase string |
| `COLLECTED_DATA` | All collected session data |
| `MESSAGES` | Full conversation history |
| `SBAR_REPORT_DATA` | Final SBAR text for PDF export |
| `SESSION_ID` | Unique session identifier |

**Special collected_data keys**:
| Key | Description |
|---|---|
| `_protocol_conversation` | Path B: protocol Q&A history |
| `_conversation_history` | Path INFO: conversation history |
| `_pending_validation` | Data awaiting user confirmation |
| `_validation_required` | Flag for pre-SBAR validation |
| `concern_type` | Path B: identified mental health concern |
| `_clinical_answers` | Path C: saved clinical Q&A pairs |
