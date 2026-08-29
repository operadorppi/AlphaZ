# Arquitetura

> v11.0 — Arquitetura em Camadas (29/08/2026)
> v10.0 — Arquitetura em Camadas (27/08/2026)

## Diagrama Geral

```
                    ┌─────────────────────────────────────┐
                    │         TASK SCHEDULER              │
                    │  08:45 iniciar · 18:35 parar        │
                    │  18:35 pipeline pós-pregão          │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       scripts/iniciar_motor.bat      │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │           watchdog.py                │
                    │  Monitora processo · reinicia se     │
                    │  morrer (max 10/hora)                │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │          run_motor.py                │
                    │  Entry point unificado (v10)         │
                    └──────────────┬──────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
  ┌───────▼───────┐     ┌──────────▼──────────┐    ┌────────▼────────┐
  │   core/        │     │   features/         │    │   adapters/     │
  │   Domínio      │     │   Microestrutura    │    │   I/O externo   │
  └────────────────┘     └─────────────────────┘    └─────────────────┘
```

## Fluxo de Tempo Real (Live)

```
ProfitChart RTD (COM)
    │
    ▼
adapters/profit_rtd.py          ← Conexão COM, PumpEvents, RefreshData
    │
    ├──→ core/capture_daemon.py      ← THREAD IMORTAL: grava JSONL em disco
    │       │                          (sobrevive crash do trading loop)
    │       └──→ adapters/file_storage.py  ← raw_negocios_ms_*.jsonl
    │
    ▼
core/market_state.py            ← Estado: historico, book, stats, OHLC
    │
    ├──→ features/*               ← Trackers: VPIN, OFI, BookLevel, VP, Kyle,
    │                                Volatility, Returns, PriceContext, Session,
    │                                PocMigration, VolumeRelativo, CrossAsset
    │
    ▼
core/signal_engine.py           ← Calcula features por segundo → Score
    │
    ├──→ core/regime_detector.py     ← Ajusta score por regime
    ├──→ core/learning.py            ← Pesos aprendidos (MFE/MAE)
    ├──→ scorer.py (ScorerML)        ← Predição ML (opcional, 40% peso)
    │
    ▼
core/position_manager.py        ← Abrir / Fechar / TP / SL / Reversão
    │
    ├──→ core/risk_manager.py        ← Circuit breaker, cooldown, horário
    ├──→ core/persistence.py         ← Grava trades + decisões + checkpoint
    │
    ▼
adapters/dashboard/             ← HTTP dashboard (api, state, handlers)
    │
    ▼
dashboard_pro.html              ← Dashboard profissional (porta 5001)
```

## Fluxo Offline (Batch / Pipeline ML)

```
D:/MarketData/mimo/26/
    │
    ├── raw_negocios_ms_*.jsonl      ← Captura bruta (adapters/file_storage.py)
    ├── raw_book_ms_*.jsonl
    │
    ▼
ml/labeler_vectorizado.py       ← Labels TP=100 / SL=50 (causal, sem leakage)
    │
    ▼
ml/build_dataset_v950.py         ← Gera dataset_final_WINV26_v950.parquet
    │                               165 colunas, 129 features, 3.4M linhas
    │                               (inclui: contexto, VWAP, POC, vol, retornos,
    │                                regime, micro×contexto, compostos)
    ▼
ml/walk_forward.py               ← Walk-forward temporal (7d treino / 1d teste)
    │                               AUC 0.779, acc 75.4%
    ▼
ml/dataset_builder.py            ← Dataset alternativo (v939, 26 features)
ml/features_expansao.py         ← Features de contexto batch (33 features)
ml/features_contexto_preco.py   ← Features de preço batch
ml/features_contexto_avancado.py ← VWAP, POC, compostos batch
```

## Ciclo Diário Automático

```
08:45  Task Scheduler → iniciar_motor.bat → watchdog.py → run_motor.py
       │
       ├── Conecta RTD (COM)
       ├── Carrega modelo ML (se config ml_modelo)
       ├── Dashboard HTTP na porta 5001
       └── Loop: PumpEvents → RefreshData → alimentar → calcular → avaliar
           │
           ├── Snapshot book a cada 250ms
           ├── Verificar TP/SL a cada 250ms
           ├── Flush trades/decisões a cada 0.5s
           └── Salvar sessão a cada 60s

18:35  Task Scheduler → taskkill /F /IM python.exe (stop motor)
18:35  Task Scheduler → pipeline_after_market.bat
       │
       ├── Labeler (labels_TP100_SL50)
       ├── Dataset builder
       ├── Walk-forward
       └── Relatório diário
```

## Camadas de Componentes (v11.0)

| Camada | Diretório | Arquivos | Linhas | Responsabilidade |
|--------|-----------|----------|--------|------------------|
| **core/** | `core/` | 13 | 2.800+ | Domínio: estado, scoring, posição, risco, regime, aprendizado, persistência, métricas, **loop RTD completo**, orquestrador, **capture daemon** |
| **features/** | `features/` | 17 | 2.000+ | Microestrutura: VPIN, OFI, book, T&T, VP, Kyle, vol, retornos, contexto, sessão, **cross-asset multi-par**, padrões |
| **config/** | `config/` | 2 | 200 | Configuração: ConfigCompleto, flat/aninhado, defaults |
| **adapters/** | `adapters/` | 6 | 700+ | I/O: RTD (COM), file storage (JSONL), dashboard HTTP, COM watchdog, **dashboard/ (api+state+handlers)** |
| **ml/** | `ml/` | 29 | ~8.000 | Pipeline: labeler, dataset, treino, walk-forward, ablation, features batch |
| **scripts/** | `scripts/` | 9 | — | Automação: iniciar, parar, pipeline, relatórios |
| **testes/** | `testes/` | 16 | — | 142 testes (features, contexto, scorer, causalidade, staleness) |
| **docs/** | `docs/` | 22 | — | Documentação estruturada |
| **dados/** | `dados/` | 13 | — | Resultados, JSONs, parquets |
| **Shims** | raiz | 4 | ~60 | motor_rt_alphaz.py (25, shim), motor_web.py (1.116, orchestrator), scorer.py (314, ML), features_lib.py (23, shim), config.py (105) |

## Shims de Compatibilidade

| Arquivo | Linhas | Delega para |
|---------|--------|-------------|
| `motor_rt_alphaz.py` | 25 | `core/app.py` (App, _AnaliseShim, _sem_dados_por_ativo, parse_hms_ms) |
| `features_lib.py` | 23 | `features/` (re-exporta tudo) |
| `captura_eventos_ms.py` | 9 | `adapters/file_storage.py` |
| `config/__init__.py` | 32 | Re-exporta `config.py` raiz + `config/defaults.py` |
| `config.py` (raiz) | 109 | Só CONFIG loading (código flat removido em v10.1.1) |

## Arquivamento

O motor original (`motor_rt_alphaz.py`, 4.154 linhas) foi arquivado em:
`docs/archive/motor_rt_alphaz_v9_legacy.py`

O `run_motor.py` agora usa `core.app.App` diretamente.
A cadeia de execução não muda: Task Scheduler → bat → watchdog → run_motor → core/app.py

## Estrutura de Diretórios

```
C:/Freebuff/
├── run_motor.py                # Entry point oficial (usa core.app.App)
├── watchdog.py                 # Watchdog → run_motor.py
├── motor_rt_alphaz.py         # SHIM (25 linhas) → core/app.py
├── motor_web.py               # ORIGINAL (conexão COM, ainda ativo)
├── scorer.py                   # ScorerML (ML live)
├── features_lib.py             # SHIM → features/
├── captura_eventos_ms.py       # SHIM → adapters/file_storage.py
├── config.py                   # Config central
├── treino_lib.py               # Utilitários de treino
├── dashboard_pro.html         # Dashboard profissional
│
├── core/                       # 13 arquivos, 2.800+ linhas
│   ├── __init__.py
│   ├── app.py                  # Orquestrador + LOOP RTD COMPLETO
│   ├── capture_daemon.py       # THREAD IMORTAL: captura JSONL (v11.0)
│   ├── contracts.py            # Dataclasses (Signal, Action, etc)
│   ├── event_clock.py          # Relógio mestre + parse_hms_ms
│   ├── market_state.py         # Estado de mercado
│   ├── persistence.py          # I/O trades/decisões/checkpoints
│   ├── metrics.py              # Métricas (PF, Sharpe, DD)
│   ├── regime_detector.py      # Regime (direção × vol)
│   ├── learning.py             # Aprendizado de pesos
│   ├── risk_manager.py         # Circuit breaker, TP/SL, horário
│   ├── position_manager.py     # Abrir/fechar/saídas
│   └── signal_engine.py        # Scoring + features
│
├── features/                   # 17 arquivos, 1.876 linhas
│   ├── __init__.py             # Re-export de tudo
│   ├── utils.py                # Funções puras (ewma, hhi, entropia, fase_sessao)
│   ├── vpin.py                 # VPINTracker
│   ├── book_features.py        # BookLevelFeatures + OFITracker
│   ├── trade_features.py       # JanelaFeatures + GeradorJanelas
│   ├── volume_profile.py       # VolumeProfileTracker (POC, VAH/VAL)
│   ├── ewma_zscore.py          # EWMAZScore
│   ├── kyle_lambda.py          # KyleLambdaTracker
│   ├── patterns.py             # PadroesMemoria (spoof, stop-hunt)
│   ├── cross_asset.py          # CrossAssetEngine + CrossAssetManager (WIN↔IND, DOL↔WDO)
│   ├── percentil.py            # PercentilTracker + RangeTracker + AccumulationTracker
│   ├── volatility.py           # VolatilityTracker
│   ├── returns.py              # ReturnsTracker
│   ├── price_context.py        # PrecoContextTracker
│   ├── session_time.py         # SessionTimeTracker
│   ├── poc_migration.py        # PocMigrationTracker
│   └── volume_relativo.py      # VolumeRelativoTracker
│
├── config/                     # 2 arquivos, 200 linhas
│   ├── __init__.py             # Re-export de config.py + config/defaults.py
│   └── defaults.py             # ConfigCompleto, _aplicar_*, NESTED_TO_FLAT
│
├── adapters/                   # 6 arquivos, 700+ linhas
│   ├── __init__.py
│   ├── com_watchdog.py         # COMHeartbeatMonitor (B2)
│   ├── file_storage.py         # CapturaEventosMS / FileStorage
│   ├── profit_rtd.py           # ProfitRTD (MarketDataSource)
│   ├── rtd_connection.py       # COM interfaces, server, discover, connect
│   ├── rtd_parser.py           # parse_refresh_data, parse_dat, enforce_schema
│   ├── rtd_writer.py           # Writer threads, schemas, parquet, stats
│   └── dashboard/              # Dashboard HTTP desacoplado
│       ├── api.py              # Roteamento HTTP (tabela de rotas)
│       ├── state.py            # Estado compartilhado (filas, live stats)
│       └── handlers.py         # Handlers de cada endpoint
│
├── ml/                         # 29 arquivos
├── scripts/                    # 9 arquivos
├── testes/                     # 16 arquivos
├── dados/                      # 13 arquivos
├── docs/                       # 22 arquivos
└── docs/archive/               # Arquivos legados
    └── motor_rt_alphaz_v9_legacy.py   # Motor original (4.154 linhas)
```
