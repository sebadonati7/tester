# ProtocolDrivenExecutor — Documentazione

## Descrizione

`ProtocolDrivenExecutor` esegue il percorso BLACK (Salute Mentale) di SIRAYA
basandosi su protocolli clinici validati caricati da Supabase.

Sostituisce il vecchio FSM Path B (INTAKE → CONSENT → DEMOGRAPHICS → RISK_ASSESSMENT)
con un approccio protocol-driven dove l'LLM segue un protocollo specifico per concern type.

---

## Protocolli Supportati

| Concern Type | Protocollo | Descrizione |
|---|---|---|
| `suicidal_ideation` | **ASQ** (Ask Suicide Screening Questions) | Screening ideazione suicidaria |
| `depression` | **PHQ-9** (Patient Health Questionnaire) | Screening depressione |
| `eating_disorder` | **DCA** (Disturbi Comportamento Alimentare) | Screening DCA |
| `substance_abuse` | **SERT** | Screening dipendenze |
| `general_mental_health` | Protocollo generico | Fallback per altri casi |

---

## Architettura

```python
ProtocolDrivenExecutor
    ├── identify_concern_type(user_input, chief_complaint) → str
    │   └── Keyword matching (priorità: suicidal_ideation > altri)
    │
    ├── load_protocol_from_supabase(concern_type) → Dict
    │   ├── PRIMARY: Supabase table "mental_health_protocols"
    │   ├── FALLBACK 1: RAG service (protocol_chunks)
    │   └── FALLBACK 2: Embedded protocols (minimal, sempre disponibili)
    │
    ├── execute_protocol(protocol_data, conversation_history) → Dict
    │   ├── Determina chunk corrente (da len(user_turns))
    │   ├── LLM genera domanda dal chunk
    │   └── Returns: {next_question, progress, completed, risk_indicators}
    │
    └── stratify_risk(protocol_data, responses, concern_type) → Dict
        ├── suicidal_ideation: keyword-based (piano → IMMEDIATE, attivo → HIGH)
        ├── depression: PHQ-9 score stimato (≥20 HIGH, 10-19 MOD, <10 LOW)
        ├── eating_disorder: → Centro DCA
        └── substance_abuse: → SERT
```

---

## Schema Supabase

```sql
CREATE TABLE mental_health_protocols (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    protocol_type VARCHAR(50) NOT NULL,
    -- Valori: suicidal_ideation | depression | eating_disorder | substance_abuse
    chunk_order INT NOT NULL,
    chunk_content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    -- Es: {"phase": "screening", "protocol": "ASQ", "items": [1, 2]}
    risk_stratification JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index per query veloci
CREATE INDEX idx_mhp_protocol_type ON mental_health_protocols(protocol_type);
CREATE INDEX idx_mhp_chunk_order ON mental_health_protocols(protocol_type, chunk_order);
```

---

## Upload Protocolli

Usa lo script `scripts/upload_protocols_to_supabase.py`:

```bash
# Dry-run (verifica senza upload)
python scripts/upload_protocols_to_supabase.py --dry-run

# Upload tutti i protocolli
python scripts/upload_protocols_to_supabase.py

# Upload protocollo specifico
python scripts/upload_protocols_to_supabase.py --protocol suicidal_ideation
```

**Variabili d'ambiente richieste**:
```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your-service-role-key
```

**Directory protocolli custom**: `knowledge_base/mental_health_protocols/`
- Crea file JSON per ciascun protocollo (es. `suicidal_ideation.json`)
- I file custom sovrascrivono i protocolli embedded

---

## Risk Stratification

### Suicidal Ideation (ASQ)

| Livello | Indicatori | Facility | Azione |
|---|---|---|---|
| `immediate` | Piano concreto + mezzi disponibili | SPDC | 118 |
| `high` | Ideazione attiva senza piano | Crisis Center | 118 se necessario |
| `moderate` | Pensieri passivi di morte | CSM | consulenza |
| `low` | Nessuna ideazione attiva | MMG | follow-up |

### Depression (PHQ-9)

| Livello | Score Stimato | Facility |
|---|---|---|
| `high` | ≥ 20 | CSM urgente |
| `moderate` | 10-19 | CSM |
| `low` | < 10 | MMG + follow-up |

### Eating Disorder (DCA)

Sempre → Centro DCA (valutazione specialistica)

### Substance Abuse (SERT)

Sempre → SerD/SERT (Servizio per le Dipendenze)

---

## Esempio di Utilizzo

```python
from siraya.controllers.protocol_driven_executor import ProtocolDrivenExecutor

executor = ProtocolDrivenExecutor(rag_service, llm_service, supabase_client)

# 1. Identifica concern type
concern = executor.identify_concern_type("voglio morire", "disagio psicologico")
# → "suicidal_ideation"

# 2. Carica protocollo
protocol = executor.load_protocol_from_supabase(concern)
# → {"protocol_name": "ASQ...", "chunks": [...], "risk_stratification_levels": {...}}

# 3. Esegui protocollo (loop)
conversation = []
while True:
    result = executor.execute_protocol(protocol, conversation)
    if result["completed"]:
        break
    # Mostra domanda all'utente
    print(result["next_question"])
    # Raccogli risposta
    answer = input()
    conversation.append({"role": "user", "content": answer})
    conversation.append({"role": "assistant", "content": result["next_question"]})

# 4. Stratifica rischio
risk = executor.stratify_risk(protocol, conversation, concern)
# → {"risk_level": "high", "recommended_facility": "Crisis Center", ...}
```

---

## Configurazione LLM Prompt (execute_protocol)

```
Sei un operatore medico che segue il protocollo clinico validato:
{current_chunk_content}

Conversazione finora:
{conversation_history}

Genera la PROSSIMA domanda in base al protocollo.
- Tono: empatico, non giudicante, professionale
- Se protocollo concluso: {"completed": true}
- Altrimenti: {"question": "domanda?", "completed": false}
```

---

## Fallback Strategy

Se Supabase non disponibile o tabella `mental_health_protocols` non esiste:

1. **RAG Fallback**: cerca chunk rilevanti in `protocol_chunks` via `retrieve_context()`
2. **Embedded Fallback**: usa protocolli minimali hardcoded nel codice
   - Sempre disponibili, coprono tutti i concern types
   - 4-5 domande per tipo, deterministiche

Questo garantisce che Path BLACK funzioni anche senza connessione database.
