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
    echo git remote add origin https://github.com/operadorppi/Alphaz.git
    goto wait
)

:: Tenta buscar atualizacoes
echo Verificando atualizacoes no servidor...
git fetch origin main >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    :: Forca o SSD a ficar identico ao GitHub (apaga mudancas locais acidentais)
    git reset --hard origin/main
    echo [OK] Sincronizado com sucesso as %time%
) else (
    echo [!] ERRO: Nao foi possivel conectar ao GitHub. Verifique internet ou Token.
)

:wait
echo.
echo Aguardando 30 segundos...
timeout /t 30 > nul
goto loop