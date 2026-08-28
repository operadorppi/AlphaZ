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

:: Verifica se o remote origin existe
git remote get-url origin >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERRO: O remote 'origin' nao foi encontrado neste SSD.
    echo.
    echo Para corrigir, execute no terminal:
    echo git remote add origin https://github.com/operadorppi/alphaz.git
    goto wait
)

:: Comando robusto: Busca o que esta no GitHub e forca o SSD a ficar igual.
:: Isso resolve o erro de "unborn branch" e limpa alteracoes locais no SSD.
git fetch origin main && git reset --hard origin/main
git fetch origin main && git reset --hard origin/main || echo [!] Falha na conexao ou permissao.

:wait
echo.
echo Aguardando 30 segundos...
timeout /t 30 > nul
goto loop