@echo off
echo ============================================================
echo   INSTALAR AUTO-START DO MOTOR + PIPELINE
echo   Execute como Administrador
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1}"

:: Task 1: INICIAR MOTOR (8:45)
schtasks /create /tn "MotorAlphaz_Iniciar" /tr "cmd /c \"cd /d %SCRIPT_DIR% && python watchdog.py WINV26 WDOV26 INDV26 DOLV26\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:45 /ru "%USERNAME%" /rl HIGHEST /f
if %errorlevel%==0 (
    echo [OK] MotorAlphaz_Iniciar criada (8:45)
) else (
    echo [ERRO] Falha ao criar MotorAlphaz_Iniciar
)

:: Task 2: PARAR MOTOR (18:30)
schtasks /create /tn "MotorAlphaz_Parar" /tr "cmd /c \"%SCRIPT_DIR%\parar_motor.bat\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 18:30 /ru "%USERNAME%" /rl HIGHEST /f
if %errorlevel%==0 (
    echo [OK] MotorAlphaz_Parar criada (18:30)
) else (
    echo [ERRO] Falha ao criar MotorAlphaz_Parar
)

:: Task 3: PIPELINE AFTER-MARKET (18:35)
schtasks /create /tn "MotorAlphaz_Pipeline" /tr "cmd /c \"%SCRIPT_DIR%\pipeline_after_market.bat\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 18:35 /ru "%USERNAME%" /rl HIGHEST /f
if %errorlevel%==0 (
    echo [OK] MotorAlphaz_Pipeline criada (18:35)
) else (
    echo [ERRO] Falha ao criar MotorAlphaz_Pipeline
)

echo.
echo ============================================================
echo   INSTALACAO COMPLETA!
echo   08:45 - Motor liga (4 ativos: WIN, WDO, IND, DOL)
echo   18:30 - Motor para
echo   18:35 - Pipeline roda (validacao + batch + retreino)
echo ============================================================
pause
