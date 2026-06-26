@echo off
title Viral OS - Server locale
echo ============================================
echo   Viral OS - Server locale
echo   Lascia questa finestra APERTA mentre usi il tool.
echo   Per fermare: chiudi la finestra.
echo ============================================
echo.
cd /d "%~dp0"
py voice_server.py
echo.
echo Il server si e' fermato. Premi un tasto per chiudere.
pause >nul
