@echo off
SETLOCAL EnableDelayedExpansion
TITLE AI Health Navigator - Suite Integrata v2.2

:: 1. Posizionamento nella cartella del progetto
cd /d "%~dp0"

echo ======================================================
echo    AI HEALTH NAVIGATOR - SYSTEM BOOTSTRAP V2.2
echo ======================================================
echo.

:: 2. Verifica e attivazione Ambiente Virtuale
echo [1/4] Attivazione ambiente virtuale...
if not exist ".venv\Scripts\activate" (
    echo [ERRORE] Ambiente virtuale non trovato in .venv.
    pause
    exit /b
)
call .venv\Scripts\activate

:: 3. Avvio API GATEWAY (Porta 5000)
:: Essenziale per la sincronizzazione dei dati (Fat Frontend)
echo [2/4] Avvio API GATEWAY (Porta 5000)...
start "HealthNavigator - API" cmd /c "python backend_api.py"

:: 4. Avvio ANALYTICS ENGINE (Porta 8502)
:: Rimosso /min e headless per diagnosticare eventuali crash (es. Plotly mancante)
echo [3/4] Avvio ANALYTICS DASHBOARD (Porta 8502)...
:: Cambiamo /c con /k così la finestra resta aperta dopo il crash
start "HealthNavigator - Analytics" cmd /k "streamlit run backend.py --server.port 8502"
:: 5. Avvio CLINICAL FRONTEND (Porta 8501)
echo [4/4] Avvio FRONTEND TRIAGE (Porta 8501)...
start "HealthNavigator - Frontend" cmd /c "streamlit run frontend.py --server.port 8501 --browser.gatherUsageStats false"

echo.
echo ------------------------------------------------------
echo    SISTEMA IN AVVIO
echo ------------------------------------------------------
echo    - Frontend:  http://localhost:8501
echo    - Analytics: http://localhost:8502
echo    - API Sync:  http://localhost:5000
echo ------------------------------------------------------
echo.
echo NOTA: Se la finestra Analytics (8502) si chiude subito, 
echo controlla di aver installato plotly: pip install plotly
echo.
pause