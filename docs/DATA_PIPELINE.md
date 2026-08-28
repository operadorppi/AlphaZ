# Pipeline de Dados

## Pipeline Offline
captura_eventos_ms -> batch_processor -> dataset_100ms -> labeler_vectorizado -> labels -> dataset_builder -> parquet

## Labeler
TP WIN: 100 pts, TP WDO: 1 pt, SL: 50 pts, Janela: 30s, Purge: 0s

## Pipeline Diario (scripts/pipeline_diario.py)
1. Relatorio qualidade 2. Features batch 3. Labels 4. Dataset 5. Gate qualidade 6. Retreino
Flags: --dry-run, --skip-batch, --dia, --save-dir
