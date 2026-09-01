@echo off
echo ============================================================
echo   MOTOR RT ALPHAZ - Watchdog
echo   Mantenha esta janela aberta!
echo   Feche esta janela = para o motor
echo ============================================================
echo.
cd /d "%~dp0.."
echo [%date% %time%] Iniciando watchdog...
python watchdog.py WINV26 WDOV26 INDV26 DOLV26
echo.
echo [%date% %time%] Motor encerrou.
echo.
