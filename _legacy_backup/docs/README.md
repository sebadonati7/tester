# 🩺 CHATBOT.ALPHA v2 - AI Health Navigator

**Versione**: 2.0  
**Data**: Gennaio 2026  
**Stato**: Production Ready ✅

---

## 🚀 Quick Start

### 1. Installazione Dipendenze

```bash
pip install streamlit groq openai plotly xlsxwriter flask flask-cors pydantic
```

### 2. Configurazione API Keys

Crea il file `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "gsk_..."
OPENAI_API_KEY = "sk-..."
BACKEND_API_KEY = "your-secret-key"
```

### 3. Avvio Sistema (Windows)

**Opzione A - Script Automatico**:
```cmd
avvia_tutto.bat
```

**Opzione B - Manuale**:
```bash
# Terminal 1: Backend API
python backend_api.py

# Terminal 2: Frontend Triage
streamlit run frontend.py --server.port 8501

# Terminal 3: Analytics Dashboard
streamlit run backend.py --server.port 8502
```

### 4. Accesso

- **Frontend Triage**: http://localhost:8501
- **Analytics Dashboard**: http://localhost:8502
- **Backend API Health**: http://localhost:5000/health

---

## 📊 Funzionalità v2

### Frontend (Porta 8501)
- ✅ Triage clinico AI-powered con FSM multi-step
- ✅ Sistema emergenze (codici BLACK/RED/ORANGE)
- ✅ Ricerca strutture sanitarie geolocalizzate
- ✅ Accessibilità (contrasto elevato, font scaling, TTS)
- ✅ Sincronizzazione sessioni cross-instance

### Analytics Dashboard (Porta 8502)
- ✅ **KPI Volumetrici**: Sessioni, throughput orario, completion rate, tempo mediano
- ✅ **KPI Clinici**: Spettro sintomi completo, urgenza, red flags
- ✅ **KPI Context-Aware**: Urgenza per specializzazione, deviazione PS
- ✅ **Export Excel**: Report professionale multi-foglio
- ✅ **Filtri**: Anno, Mese, Settimana ISO, Comune
- ✅ **Visualizzazioni**: Plotly GO (histogram, pie charts, tabelle interattive)

### Backend API (Porta 5000)
- ✅ REST API per sincronizzazione sessioni
- ✅ Persistenza JSONL + file-based sessions
- ✅ Health check endpoint
- ✅ CORS abilitato per cross-origin

---

## 🏗️ Architettura

```
┌─────────────────────────────────────────────────────────────┐
│                        USER BROWSER                         │
└────────────┬──────────────────────────────────┬─────────────┘
             │                                  │
             ▼                                  ▼
    ┌────────────────┐                ┌────────────────┐
    │   Frontend     │                │   Analytics    │
    │  (Port 8501)   │                │  (Port 8502)   │
    │                │                │                │
    │ • UI Triage    │                │ • KPI Dashboard│
    │ • FSM Logic    │                │ • Excel Export │
    │ • AI Streaming │                │ • Plotly GO    │
    └───────┬────────┘                └───────┬────────┘
            │                                 │
            │ HTTP POST                       │ Read
            ▼                                 ▼
    ┌────────────────┐                ┌────────────────┐
    │  Backend API   │                │ triage_logs    │
    │  (Port 5000)   │◄───Write───────│    .jsonl      │
    │                │                │                │
    │ • Session Sync │                │ • Raw Events   │
    │ • JSONL Write  │                │ • Append-Only  │
    └────────────────┘                └────────────────┘
```

**Componenti Chiave**:
- `frontend.py`: Fat Frontend con logica clinica integrata
- `backend.py`: Analytics Engine Streamlit (rewrite v2)
- `backend_api.py`: REST API Flask per persistenza
- `model_orchestrator_v2.py`: AI Provider Manager (Groq/OpenAI)
- `smart_router.py`: FSM Router con classificazione urgenza
- `utils/id_manager.py`: Thread-safe ID generator

---

## 📂 Struttura File

```
demo/
├── frontend.py              # Main UI (8501)
├── backend.py               # Analytics (8502) ✨ REWRITE V2
├── backend_api.py           # REST API (5000)
├── model_orchestrator_v2.py # AI Orchestrator
├── smart_router.py          # FSM Router
├── bridge.py                # AI-UI Streaming
├── models.py                # Pydantic Schemas
├── session_storage.py       # Session Persistence
├── utils/
│   ├── __init__.py
│   └── id_manager.py        # Atomic ID Generator ✨ NEW
├── triage_logs.jsonl        # Event Log (append-only)
├── master_kb.json           # Knowledge Base Strutture
├── mappa_er.json            # Geo Coordinates
├── distretti_sanitari_er.json # District Mapping ✨ NEW
├── .streamlit/
│   └── secrets.toml         # API Keys (gitignored)
├── sessions/                # Active Sessions
├── avvia_tutto.bat          # Windows Launcher
└── MASTER_ARCHITECTURE_V2.md # Architecture Docs
```

---

## 🔍 Troubleshooting

### Backend Analytics Non Parte
**Sintomo**: Dashboard si chiude immediatamente  
**Fix**:
1. Verifica `triage_logs.jsonl` esista (può essere vuoto)
2. Controlla console per errori parsing
3. Se JSONL corrotto, rinominalo e riavvia

### AI Offline
**Sintomo**: "❌ Servizio AI offline"  
**Fix**:
1. Verifica `secrets.toml` esista in `.streamlit/`
2. Testa chiavi API manualmente
3. Controlla quota Groq/OpenAI

### Sessioni Non Salvate
**Sintomo**: Dati persi tra riavvii  
**Fix**:
1. Verifica `http://localhost:5000/health` risponda
2. Controlla permessi scrittura cartella `sessions/`
3. Riavvia `backend_api.py`

---

## 🆕 Changelog v2 (Gennaio 2026)

### Nuove Funzionalità
- ✅ Analytics Dashboard: Rewrite totale, zero pandas/px
- ✅ KPI Framework: 3 categorie × 15+ metriche
- ✅ Export Excel: Report multi-foglio professionale
- ✅ ID Manager: Thread-safe atomic generation
- ✅ Parsing Timestamp: ISO 8601 robusto con fallback

### Fix Critici
- ✅ Backend crash silenzioso → Stabilità 100%
- ✅ Bug temporale (anni/settimane) → Calcolo dinamico
- ✅ Indentazione frontend.py → Correzione completa
- ✅ Dependency hell → Rimosso pandas/px

---

## 📞 Supporto

**Documentazione Completa**: Vedi `MASTER_ARCHITECTURE_V2.md`  
**Issues**: Apri issue su repository  
**Contact**: Team CHATBOT.ALPHA v2

---

## 📄 Licenza

Progetto interno - Uso riservato

---

**Powered by**: Streamlit + Groq + OpenAI + Plotly  
**Built with**: ❤️ e ☕ da Cursor AI Agent

