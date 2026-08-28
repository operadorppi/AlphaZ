@echo off
echo ============================================================
echo   PIPELINE AFTER-MARKET - %date% %time%
echo ============================================================
cd /d C:\Freebuff

echo [1/3] Rodando labeler...
PYTHONPATH=. python ml/labeler_vectorizado.py --input D:\MarketData\mimo\26\dataset_100ms_WINV26_4-17.jsonl --ativo WINV26 --tp 100 --sl 50 --max-holding 30 --purge 0 --min-vol 0 --output D:\MarketData\mimo\26\labels_WINV26_v939.jsonl
PYTHONPATH=. python ml/labeler_vectorizado.py --input D:\MarketData\mimo\26\dataset_100ms_WDOU26_4-17.jsonl --ativo WDOU26 --tp 1 --sl 0.5 --max-holding 30 --purge 0 --min-vol 0 --output D:\MarketData\mimo\26\labels_WDOU26_v939.jsonl

echo [2/3] Montando dataset...
PYTHONPATH=. python ml/dataset_builder.py --features "D:\MarketData\mimo\26\dataset_100ms_WINV26_4-17.jsonl" --labels "D:\MarketData\mimo\26\labels_WINV26_v939.jsonl" --output "D:\MarketData\mimo\26\dataset_final_completo_v939.parquet"

echo [3/3] Walk-forward...
PYTHONPATH=. python ml/walk_forward_otimizado.py

echo ============================================================
echo   PIPELINE CONCLUIDO - %date% %time%
echo ============================================================
