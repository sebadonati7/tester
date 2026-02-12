@echo off
REM ============================================================================
REM  SIRAYA Health Navigator — Dev Launcher (Locale + Supabase Cloud)
REM  Avvia Streamlit puntando alla cartella siraya con i secret già configurati.
REM ============================================================================

echo.
echo  ========================================
echo   SIRAYA Health Navigator - Dev Mode
echo  ========================================
echo.

REM ── Controlla che Python sia disponibile ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRORE] Python non trovato nel PATH.
    echo Installa Python 3.10+ e riprova.
    pause
    exit /b 1
)

REM ── Attiva virtual-env se esiste ──
if exist "venv\Scripts\activate.bat" (
    echo [INFO] Attivazione virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [WARN] Nessun venv trovato. Uso Python globale.
)

REM ── Verifica dipendenze minime ──
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installazione dipendenze...
    pip install -r requirements.txt
)

REM ── Imposta variabili d'ambiente per Supabase (fallback se secrets.toml manca) ──
if not defined SUPABASE_URL (
    echo [INFO] SUPABASE_URL non definito; Streamlit usera' .streamlit/secrets.toml
)

REM ── Avvia Streamlit ──
echo.
echo [START] Avvio Streamlit su http://localhost:8501
echo.
streamlit run siraya/app.py --server.port 8501 --server.headless true --browser.gatherUsageStats false

pause

