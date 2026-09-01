@echo off
echo ============================================================
echo   PIPELINE AFTER-MARKET - %date% %time%
echo   4 ativos: WIN, WDO, IND, DOL
echo ============================================================
cd /d C:\Freebuff

set DIA=%1
if "%DIA%"=="" (
    rem Usar data de ontem
    for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
    set ANO=%datetime:~0,4%
    set MES=%datetime:~4,2%
    set DIA_NUM=%datetime:~6,2%
    rem Calcular ontem (simplificado: usar data atual)
    set DIA=%ANO%%MES%%DIA_NUM%
)

echo Dia: %DIA%
echo.

echo [1/4] Validacao RAW Hive...
python scripts/validar_raw_hive.py --raw-path D:\MarketData\Profit --dia %DIA%
if %errorlevel% neq 0 (
    echo [AVISO] Validacao encontrou problemas
)

echo.
echo [2/4] Relatorio de qualidade...
python scripts/converter_brutos_parquet.py --dia %DIA% --save-dir D:\MarketData\Profit

echo.
echo [3/4] Features 100ms (batch_processor)...
python ml/batch_processor.py --ativo WINV26,WDOV26,INDV26,DOLV26 --periodo %DIA%-%DIA%

echo.
echo [4/4] Retreino do modelo...
python ml/retreinar_lgbm_limpo.py --ativo WINV26

echo.
echo ============================================================
echo   PIPELINE CONCLUIDO - %date% %time%
echo ============================================================
