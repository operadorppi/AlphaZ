# Pipeline de Dados

> v11.0 — CaptureDaemon na captura (29/08/2026)

## Pipeline Tempo Real

```
ProfitChart RTD (COM)
    │
    ▼
adapters/profit_rtd.py          ← Conexão COM, eventos normalizados
    │
    ├──→ core/capture_daemon.py  ← THREAD IMORTAL: grava JSONL
    │       └──→ adapters/file_storage.py  ← raw_negocios_ms_*.jsonl
    │                                       ← raw_book_ms_*.jsonl
    │
    ├──→ adapters/rtd_writer.py  ← THREADS SEPARADAS: grava Parquet
    │       ├── thread_escritora()      ← BOOK (Parquet/hora/ativo)
    │       └── thread_escritora_tt()   ← T&T (Parquet/hora/ativo)
    │
    ▼
core/market_state.py            ← Estado: historico, book, stats, OHLC
    │
    ▼
core/signal_engine.py           ← Features + Score
    │
    ▼
core/position_manager.py        ← Trading decisions
```

**Garantias:**
- CaptureDaemon (JSONL) sobrevive crash do trading loop
- RtdWriter (Parquet) roda em processos separados (multiprocessing)
- Se o motor morre, ambos continuam gravando

## Pipeline Offline
captura_eventos_ms -> batch_processor -> dataset_100ms -> labeler_vectorizado -> labels -> dataset_builder -> parquet

## Labeler
TP WIN: 100 pts, TP WDO: 1 pt, SL: 50 pts, Janela: 30s, Purge: 0s

## Pipeline Diario (scripts/pipeline_diario.py)
1. Relatorio qualidade 2. Features batch 3. Labels 4. Dataset 5. Gate qualidade 6. Retreino
Flags: --dry-run, --skip-batch, --dia, --save-dir
