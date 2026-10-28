@echo off
rem ASTRA MT5 DEMO bridge (Windows, next to the MT5 terminal). Mirrors ASTRA's managed paper accounts onto
rem one MT5 DEMO account; see src/astra/mt5_bridge.py. Installed copy: %USERPROFILE%\.astra (outside Git).
rem The token file holds a read-only agent token (astra auth agent-create --scopes read); never commit it.
rem To stop every order at once: create an empty file named STOP in %USERPROFILE%\.astra\mt5-bridge.
setlocal
if "%ASTRA_MT5_LOGIN%"=="" (echo ASTRA_MT5_LOGIN manquant & exit /b 2)
if "%ASTRA_MT5_URL%"=="" (echo ASTRA_MT5_URL manquant & exit /b 2)
set ASTRA_MT5_TOKEN_FILE=%USERPROFILE%\.astra\mt5-bridge.token
set ASTRA_MT5_DIR=%USERPROFILE%\.astra\mt5-bridge
set PYTHONPATH=%USERPROFILE%\.astra\app\src
set PYTHONDONTWRITEBYTECODE=1
:loop
"%USERPROFILE%\.astra\venv\Scripts\python.exe" -m astra mt5 bridge run >> "%ASTRA_MT5_DIR%\bridge.log" 2>&1
rem Exit code 2 = halted on purpose (server not in SIMULATION, token refused): do not restart.
if errorlevel 2 if not errorlevel 3 exit /b 2
timeout /t 60 /nobreak > nul
goto loop
