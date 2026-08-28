@echo off
echo ============================================================
echo   MOTOR RT ALPHAZ — CI/CD Pipeline
echo   Pressione Ctrl+C para cancelar
echo ============================================================
echo.
set PYTHONIOENCODING=utf-8
python run_all_tests.py %*
echo.
echo ============================================================
echo   Pipeline concluido
echo ============================================================
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   ❌ ALGUNS TESTES FALHARAM
    echo   Corrija os erros antes de commitar
    echo.
) else (
    echo.
    echo   ✅ TODOS OS TESTES PASSARAM
    echo   Seguro para commitar
    echo.
)
pause
