# REFACTORING NOTES — SIRAYA Controllers V5.0

## Panoramica

Questo documento descrive il refactoring dei controller SIRAYA effettuato per risolvere quattro problemi critici:

1. **Routing ambiguo** tra SmartRouter e LLMJudge
2. **Allucinazione dati** (age/location estratti senza conferma)
3. **Path BLACK non strutturato** (nessun protocollo clinico validato)
4. **Path INFO bloccato** (sempre "NON HO TROVATO RISULTATI")

---

## Problema 1: Routing Ambiguo

### Soluzione: `RouteArbitrator`

**File nuovo**: `siraya/controllers/route_arbitrator.py`

**Logica di priorità**:
- Primo turno: LLMJudge (semantico) con SmartRouter come fallback
- Turni successivi su branch C: `check_escalation` (soglia abbassata da 2 a 1)
- Branch A/B/INFO: continuità garantita

**Modifiche**:
- `smart_router.py`: soglia `check_escalation` da `>= 2` a `>= 1`
- `triage_controller_v3.py`: routing delega a `RouteArbitrator`

**Eliminato**: vecchio routing inline (if/elif urgency_override)

---

## Problema 2: Allucinazione Dati

### Soluzione: `DataAcquisitionManager`

**File nuovo**: `siraya/controllers/data_acquisition_manager.py`

**Regola fondamentale**:
```
Dato user-explicit → collected (diretto)
Dato estratto/inferito da LLM → pending_validation + conferma pre-SBAR
```

**Modifiche**:
- `UnifiedSlotFiller.extract()`: rimosso accesso diretto a `judgment.extracted_location` e `judgment.extracted_age`; ora delega a `DataAcquisitionManager`
- `OutcomeGenerator.generate()`: `location = data.get("location", None)` (rimosso fallback `"Bologna"`)
- `OutcomeGenerator.generate()`: `pain = data.get("pain_scale", None)` (rimosso fallback `5`)
- SBAR ora indica dati non confermati con "(non confermato)"

**Eliminato**: fallback silenzioso `"Bologna"` e `5` per pain scale

---

## Problema 3: Path BLACK — Protocol-Driven

### Soluzione: `ProtocolDrivenExecutor`

**File nuovo**: `siraya/controllers/protocol_driven_executor.py`

**Protocolli supportati**:
| Concern Type | Protocollo | Facility |
|---|---|---|
| `suicidal_ideation` | ASQ - Ask Suicide Screening Questions | SPDC/Crisis Center/CSM |
| `depression` | PHQ-9 | CSM (score-based) |
| `eating_disorder` | DCA | Centro DCA |
| `substance_abuse` | SERT | SerD/SERT |

**Flow**:
1. `identify_concern_type()` → keyword matching
2. `load_protocol_from_supabase()` → tabella `mental_health_protocols` (fallback embedded)
3. `execute_protocol()` → LLM genera domanda dal chunk corrente
4. `stratify_risk()` → risk level + facility recommendation

**Script**: `scripts/upload_protocols_to_supabase.py`

**Schema Supabase**:
```sql
CREATE TABLE mental_health_protocols (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    protocol_type VARCHAR(50),
    chunk_order INT,
    chunk_content TEXT,
    metadata JSONB,
    risk_stratification JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Modifiche a `triage_controller_v3.py`**:
- Eliminato: FSM Path B generico (INTAKE → CONSENT → DEMOGRAPHICS → RISK_ASSESSMENT)
- Aggiunto: `ProtocolDrivenExecutor` nella sezione `process_user_input`
- Aggiunto: `_generate_mental_health_outcome()` in `TriageControllerV3`

---

## Problema 4: Path INFO — Liberalizzazione RAG-Driven

### Soluzione: `InfoResponseGenerator`

**File nuovo**: `siraya/controllers/info_response_generator.py`

**Differenze da vecchio `_generate_info_response`**:
- Nessun formato SBAR
- Tono amichevole, conversazionale
- Intent detection (8 categorie)
- Risposta adattata al tipo di domanda
- MAI "NON HO TROVATO RISULTATI" — sempre raffinamento o alternative

**Intent Categories**:
```
OPERATING_HOURS | FACILITY_LOCATION | SERVICE_INFO | COST_INFO
PROCEDURE_INFO | GENERAL_HEALTH | PRESCRIPTION_INFO | OTHER
```

**Modifiche a `rag_service.py`**:
- Aggiunto `retrieve_context_for_info()` con filtri per intent e location
- Filtraggio per soglia rilevanza (content length proxy)

**Modifiche a `triage_controller_v3.py`**:
- Eliminato: vecchio `_generate_info_response` in `OutcomeGenerator`
- Aggiunto: `InfoResponseGenerator` nella sezione `process_user_input` (prima dell'FSM)

---

## File Eliminati (Legacy)

Nessun file eliminato — le funzioni sostituite sono state rimosse inline:
- Vecchio routing inline (`if urgency_override == "emergency": ...`)
- `_generate_info_response` in `OutcomeGenerator` sostituito da `InfoResponseGenerator`
- Vecchio FSM Path B sostituito da `ProtocolDrivenExecutor`

---

## Nuovi File

| File | Descrizione |
|---|---|
| `siraya/controllers/route_arbitrator.py` | Routing centralizzato |
| `siraya/controllers/data_acquisition_manager.py` | Acquisizione consapevole dati |
| `siraya/controllers/protocol_driven_executor.py` | Esecuzione protocolli BLACK |
| `siraya/controllers/info_response_generator.py` | Risposte INFO conversazionali |
| `siraya/config/emergency_keywords.json` | Keywords classificazione (config) |
| `scripts/upload_protocols_to_supabase.py` | Upload protocolli su Supabase |

---

## Test

| File | Coverage |
|---|---|
| `tests/test_routing_arbitration.py` | RouteArbitrator + threshold escalation |
| `tests/test_data_acquisition.py` | DataAcquisitionManager |
| `tests/test_protocol_driven.py` | ProtocolDrivenExecutor |
| `tests/test_info_generation.py` | InfoResponseGenerator |

---

## Prossimi Passi

1. Popolare `knowledge_base/mental_health_protocols/` con JSON dei protocolli reali
2. Eseguire `python scripts/upload_protocols_to_supabase.py` per caricare su Supabase
3. Implementare embedding vector search in `retrieve_context_for_info()` per score reali
4. Aggiungere fase `_handle_pending_validation()` nel FSM per gestire conferma dati pending
