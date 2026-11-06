@echo off
rem MINING TRANSIT 360 - construction du classeur MINING_TRANSIT_360.xlsm (Windows + Excel)
rem Active temporairement l'acces au modele objet VBA, construit, compile, installe et teste.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build-MT360.ps1" -EnableVBOM %*
echo.
echo Journal : ..\dist\build_log.txt
pause
