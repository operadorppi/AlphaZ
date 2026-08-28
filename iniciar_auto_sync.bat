@echo off
title Auto-Sync GitHub
echo ============================================
echo   Auto-Sync GitHub - AlphaZ
echo   Backup automatico a cada 5 minutos
echo   Feche esta janela para parar
echo ============================================
echo.
cd /d C:\Freebuff
python auto_sync.py
pause
