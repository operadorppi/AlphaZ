@echo off
:: Script para rodar no seu Windows (SSD)
:: Ele baixa as alteracoes feitas no Cloud Shell automaticamente

title AlphaZ - Sincronizacao SSD

:loop
cls
echo ======================================================
echo   ALPHAZ SYNC - ATUALIZANDO SSD (GitHub -> Local)
echo ======================================================
echo Ultima verificacao: %time%
echo.
git pull --rebase origin main
echo.
echo Aguardando 30 segundos...
timeout /t 30 > nul
goto loop
