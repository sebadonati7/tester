@echo off
REM ================================================================
REM TEST MANUALE BACKEND - Verifica Avvio Senza Crash
REM ================================================================

echo.
echo ===================================================================
echo   BACKEND EMERGENCY FIX - TEST MANUALE
echo ===================================================================
echo.

REM Test 1: Compilazione Python
echo [TEST 1] Compilazione backend.py...
python -m py_compile backend.py
if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] Backend non compila correttamente
    pause
    exit /b 1
)
echo [PASS] Backend compila correttamente
echo.

REM Test 2: Verifica file triage_logs.jsonl
echo [TEST 2] Verifica file triage_logs.jsonl...
if exist triage_logs.jsonl (
    echo [OK] File esiste
    for %%A in (triage_logs.jsonl) do echo [OK] Dimensione: %%~zA bytes
) else (
    echo [WARN] File NON esiste - Backend mostrera' warning
)
echo.

REM Test 3: Verifica xlsxwriter
echo [TEST 3] Verifica xlsxwriter (opzionale)...
python -c "import xlsxwriter; print('[OK] xlsxwriter installato')" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] xlsxwriter non installato - Export Excel disabilitato
    echo [INFO] Per abilitare: pip install xlsxwriter
) else (
    echo [PASS] xlsxwriter disponibile
)
echo.

REM Test 4: Avvio Backend
echo ===================================================================
echo   AVVIO BACKEND - Porta 8502
echo ===================================================================
echo.
echo [INFO] Avvio backend.py su http://localhost:8502
echo [INFO] Premi Ctrl+C per terminare
echo.
echo [VERIFICA MANUALE RICHIESTA:]
echo   1. Dashboard si carica senza crash
echo   2. Sidebar visibile con filtri
echo   3. Nessun errore rosso in questa console
echo   4. Se dati presenti: KPI visualizzati
echo.
pause

streamlit run backend.py --server.port 8502

