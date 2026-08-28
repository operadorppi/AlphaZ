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

:: Comando robusto: Busca o que esta no GitHub e forca o SSD a ficar igual.
:: Isso resolve o erro de "unborn branch" e limpa alteracoes locais no SSD.
git fetch origin main && git reset --hard origin/main

echo.
echo Aguardando 30 segundos...
timeout /t 30 > nul
goto loop