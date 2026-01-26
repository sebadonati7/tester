# 🚀 SIRAYA Health Navigator - Quick Start Guide

**Versione**: v2.1 (UI/UX Professional Edition)  
**Data**: 10 Gennaio 2026

---

## ⚡ Avvio Rapido (3 Passi)

### 1. Verifica Dipendenze
```bash
cd C:\Users\Seba\Desktop\demo
pip install -r requirements.txt
```

### 2. Avvia i Servizi
```bash
# Opzione A: Avvio automatico (Windows)
avvia_tutto.bat

# Opzione B: Avvio manuale
# Terminal 1 - Frontend
streamlit run frontend.py --server.port 8501

# Terminal 2 - Analytics Dashboard
streamlit run backend.py --server.port 8502

# Terminal 3 - API (opzionale)
python backend_api.py
```

### 3. Accedi all'Applicazione
- **Frontend**: http://localhost:8501
- **Analytics**: http://localhost:8502
- **API**: http://localhost:5000

---

## 🎨 Nuove Funzionalità v2.1

### Landing Page
✅ Logo SIRAYA professionale  
✅ Terms of Use obbligatori  
✅ Access gate con checkbox accettazione

### Triage Intelligente
✅ Attivazione automatica solo su richieste mediche  
✅ Rilevamento intent con 50+ keyword italiane  
✅ Modalità triage condizionale

### UI/UX Migliorata
✅ Avatar bot personalizzato (logo SIRAYA)  
✅ Bottoni restyling professionale  
✅ Sidebar con branding aziendale  
✅ Palette colori pulita (#4A90E2 blue)

### Dashboard Analytics
✅ Grafici interattivi Plotly (zoom, pan, hover)  
✅ Filtri distretto sanitario  
✅ KPI selector personalizzabile  
✅ Export Excel con filtri applicati

---

## 📋 Test Rapido

### Test Frontend
1. Apri http://localhost:8501
2. Verifica landing page SIRAYA visibile
3. Leggi terms of use (expander)
4. Spunta checkbox e clicca "Accetta e Procedi"
5. Invia messaggio: **"Ho mal di testa e febbre"**
6. Verifica che triage mode si attivi automaticamente
7. Controlla avatar bot (logo SIRAYA)

### Test Backend
1. Apri http://localhost:8502
2. Verifica dashboard "SIRAYA Analytics" visibile
3. Testa filtri sidebar:
   - Anno: 2026
   - Distretto: Seleziona uno disponibile
4. Testa KPI selector:
   - Deseleziona "Tutti"
   - Seleziona solo "Volumetrici: Throughput Orario"
   - Verifica che solo quel grafico appaia
5. Hover su grafici per tooltip interattivi
6. Scarica report Excel (se xlsxwriter installato)

---

## 🐛 Troubleshooting

### Problema: Landing page non si carica
**Soluzione**: Verifica che `ui_components.py` e cartella `assets/` esistano

### Problema: Avatar bot mostra emoji invece del logo
**Soluzione**: Normale, fallback automatico se `assets/logo.svg` mancante

### Problema: Medical intent non rilevato
**Soluzione**: Usa keyword forti come "dolore", "febbre", "sangue" o attiva manualmente triage da sidebar

### Problema: Grafici backend non interattivi
**Soluzione**: Verifica versione Plotly: `pip install --upgrade plotly`

### Problema: Filtro distretto mostra "UNKNOWN"
**Soluzione**: Comune non mappato in `distretti_sanitari_er.json`, normale per comuni fuori ER

---

## 📁 Struttura File (Aggiornata)

```
demo/
├── frontend.py                 # ✅ MODIFICATO - Landing page + medical intent
├── backend.py                  # ✅ MODIFICATO - Grafici interattivi + filtri
├── ui_components.py            # 🆕 NUOVO - Componenti UI SIRAYA
├── assets/                     # 🆕 NUOVA CARTELLA
│   ├── logo.svg                # Logo SIRAYA
│   └── terms_of_use.md         # Condizioni d'uso
├── backend_api.py              # API Flask (non modificato)
├── model_orchestrator_v2.py    # AI orchestration (non modificato)
├── models.py                   # Pydantic models (non modificato)
├── smart_router.py             # Urgency routing (non modificato)
├── session_storage.py          # Session persistence (non modificato)
├── utils/
│   ├── id_manager.py           # ID generation (non modificato)
│   └── __init__.py
├── triage_logs.jsonl           # Log file
├── distretti_sanitari_er.json  # Distretti ER
├── master_kb.json              # Knowledge base
├── requirements.txt            # Dipendenze Python
├── avvia_tutto.bat             # Script avvio automatico
├── MASTER_ARCHITECTURE_V2.md   # Documentazione architettura
├── UI_UX_OVERHAUL_REPORT.md    # 🆕 Report overhaul completo
└── QUICK_START_SIRAYA.md       # 🆕 Questa guida
```

---

## 🎯 Prossimi Passi

1. ✅ Test completo funzionalità
2. ⚠️ **Upload su GitHub** (sebadonati7/chatbot-triage)
3. 📝 Aggiorna README.md con nuove feature
4. 🚀 Deploy su server production
5. 📊 Monitoraggio metriche utenti

---

## 📞 Supporto

**Documentazione Completa**: `UI_UX_OVERHAUL_REPORT.md`  
**Architettura Sistema**: `MASTER_ARCHITECTURE_V2.md`  
**GitHub**: sebadonati7/chatbot-triage

**Per emergenze mediche reali, chiamare il 118**

---

**Sviluppato con ❤️ da Cursor AI Agent | Gennaio 2026**

