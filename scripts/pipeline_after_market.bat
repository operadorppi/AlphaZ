@echo off
setlocal
echo ============================================================
echo   PIPELINE AFTER-MARKET - %date% %time%
echo   4 ativos: WIN, WDO, IND, DOL
echo ============================================================
cd /d C:\Freebuff

if "%DIA%"=="" set DIA=%1
set LOG=D:\MarketData\mimo\pipeline_after_market.log
if not exist "D:\MarketData\mimo" mkdir "D:\MarketData\mimo"
echo [%date% %time%] Inicio do pipeline after-market >> "%LOG%"

echo.
echo Executando pipeline_diario.py (ontem, dia util)...
echo Log completo: %LOG%
echo.

python scripts\pipeline_diario.py >> "%LOG%" 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Pipeline falhou - veja %LOG%
    echo Ultimas linhas do log:
    powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 25"
    echo.
    echo ============================================================
    echo   PIPELINE FALHOU - %date% %time%
    echo ============================================================
    pause
    exit /b 1
)

echo [%date% %time%] Pipeline concluido >> "%LOG%"
echo.
echo ============================================================
echo   PIPELINE CONCLUIDO - %date% %time%
echo   Log: %LOG%
echo ============================================================
endlocal