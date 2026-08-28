@echo off
echo ============================================================
echo   MOTOR RT ALPHAZ - Watchdog
echo   Mantenha esta janela aberta!
echo   Feche esta janela = para o motor
echo ============================================================
echo.
cd /d "%~dp0.."
echo [%date% %time%] Iniciando watchdog...
python watchdog.py WINV26 WDOU26
echo.
echo [%date% %time%] Motor encerrou.
echo.
rem (sem pause - Task Scheduler nao espera input)
