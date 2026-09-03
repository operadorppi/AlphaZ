# FASE 16 — P2 · LEGACY Code Classification & Isolation

**Date:** 2026-08-30
**Goal:** Clearly separate PRODUCTION / EXPERIMENTAL / LEGACY code, ensuring production code does not depend on experimental or legacy code.

---

## 1. Classification Criteria

| Category | Definition | Location |
|----------|------------|----------|
| **PRODUCTION** | Core code currently running in production — data ingestion, feature calculation, ML inference, risk management, trade execution | Directly importable from module directories |
| **EXPERIMENTAL** | New features under test, unstable, subject to change or deletion | `scripts/experimental/` |
| **LEGACY** | Older versions superseded by new code but retained for rollback or reference | `docs/archive/` |

### Hard Rules
- ❌ Production code (`adapters/`, `core/`, `features/`, `ml/`) must **NOT** import from `archive/` or experimental scripts
- ✅ LEGACY code may reference earlier LEGACY code (historical traceability)
- ✅ EXPERIMENTAL code may freely reference PRODUCTION and LEGACY code

---

## 2. Current Project Code Classification

### ✅ PRODUCTION (Production Code)

#### adapters/ — Data Ingestion Layer
```
adapters/profit_rtd.py      # Main RTD adapter (production)
adapters/rtd_connection.py  # COM connection
adapters/rtd_parser.py      # Parser
adapters/rtd_writer.py      # Data writer
adapters/base.py            # Abstract base class
adapters/replay.py          # Replay mode adapter
adapters/com_watchdog.py    # COM heartbeat monitor
adapters/file_storage.py    # File storage
adapters/dashboard_server.py # Dashboard HTTP service
adapters/dashboard/         # Dashboard sub-module
```

#### core/ — Core Business Logic
```
core/app.py                 # Main orchestrator (core)
core/contracts.py           # Data contracts (frozen dataclass)
core/temporal.py            # Time utility functions
core/event_ordering.py      # Event temporal ordering detection
core/market_state.py        # Market state tracking
core/signal_engine.py       # Signal engine
core/risk_engine.py         # Risk engine (14-layer protection)
core/position_manager.py    # Position manager
core/persistence.py         # Persistence
core/decision_journal.py    # Decision journal
core/metrics.py             # Metrics statistics
core/calibration.py         # Model calibration
core/regime_detector.py     # Market regime detection
core/learning.py            # Online learning
core/capture_daemon.py      # Data capture daemon
core/utils.py               # General utilities
core/leakage_test.py        # Leakage test tools
```

#### features/ — Feature Engineering
```
features/feature_engine.py     # Feature engine
features/trade_features.py     # Trade features (JanelaFeatures)
features/book_features.py      # Order book features
features/vpin.py               # VPIN indicator
features/kyle_lambda.py        # Kyle's Lambda
features/volume_profile.py     # Volume profile
features/institutional_context.py # Institutional behavior
features/ewma_zscore.py        # EWMA normalization
features/percentil.py          # Percentile tracker
features/cross_asset.py        # Cross-asset correlation
features/session_time.py       # Session time features
features/vwap_tracker.py       # VWAP tracker
features/poc_migration.py      # POC migration
features/price_context.py      # Price context
features/utils.py              # Feature utilities
features/returns.py            # Returns calculation
features/volatility.py         # Volatility tracking
features/volume_relativo.py    # Relative volume
features/feature_registry.py   # Feature registry
```

#### ml/ — Machine Learning
```
ml/batch_processor.py    # Batch feature processor
ml/dataset_builder.py    # Dataset builder
ml/scorer.py             # ML scorer
ml/calibrar_modelo.py    # Model calibration
ml/walk_forward.py       # Walk-forward validation
ml/analisar_features.py  # Feature analysis
ml/batch_historico.py    # Historical batch processing
ml/labeler_core.py       # Labeling core
ml/labeler_vectorizado.py # Vectorized labeling
ml/features_lib.py       # Feature library
ml/metrics.py            # Evaluation metrics
ml/model_registry.py     # Model registry
ml/ablation_test.py      # Ablation tests
```

#### config/ — Configuration Management
```
config/__init__.py       # Unified configuration loader
config/config.json       # Configuration file
```

#### tests/ — Test Suite
```
All test_*.py files
```

#### scripts/ — Startup & Operations Scripts
```
scripts/run_motor.py          # Main startup entry point
scripts/auto_start.bat        # Windows auto-start
scripts/iniciar_motor.bat     # Manual start
scripts/parar_motor.bat       # Stop
scripts/pipeline_after_market.bat # Post-market processing
scripts/pipeline_diario.py    # Daily pipeline
scripts/observability.py      # Observability framework
scripts/verificar_importancia.py # Feature importance check
scripts/atualizar_documentacao.py # Documentation update
```

---

### ⚠️ LEGACY (Archived Code)

Files already correctly archived in `docs/archive/`:

```
docs/archive/
├── AUDITORIA_FREEBUFF.md
├── build_dataset_v940.py
├── build_dataset_v950.py
├── CAUSALITY_AUDIT.md
├── DOCUMENTACAO_ALTERACOES.md
├── DOCUMENTACAO_RECUPERADA_2026-08-26.md
├── DOCUMENTACAO_TESTES.md
├── METODOLOGIA_BACKTESTER.md
├── motor_rt_alphaz_v9_legacy.py    ← Old motor version, shim-compatible
├── RASTREAMENTO_INTEGRACAO.md
├── RELATORIO_REVISAO.md
└── RELATORIO_VALIDACAO.md
```

**Special handling:**
- `motor_rt_alphaz.py` (root directory) — **Compatibility shim**, internally redirected to `core/app.py`, no external import dependencies. ✅ Acceptable to keep.

---

### 🔬 EXPERIMENTAL (Needs Classification)

Files located at project root with unclear classification, need reorganization:

| File | Classification | Suggested Action |
|------|---------------|------------------|
| `volatility_tracker.py` | Multi-timeframe volatility tracker | Move to `features/` or delete (feature_engine.py covers similar functionality) |
| `volume_relativo_tracker.py` | Relative volume tracker | Move to `features/` |
| `returns_tracker.py` | Multi-horizon returns tracker | Move to `features/` |
| `session_time_tracker.py` | Session time tracker | Move to `features/` |
| `preco_context_tracker.py` | Price context tracker | Move to `features/` |
| `poc_migration_tracker.py` | POC migration tracker (duplicate of `features/poc_migration.py`?) | Delete or integrate into `features/` |
| `watchdog.py` | Process guardian (possibly production) | Move to `scripts/` or mark as LEGACY |
| `auto_sync.py` | Auto-sync script | Move to `scripts/` |
| `iniciar_auto_sync.bat` | Startup batch file | Move to `scripts/` |
| `github.bat` | Git helper script | Move to `scripts/` |
| `Local)` | ⚠️ Suspicious filename (likely git artifact) | Delete |
| `motor_web.py` | ⚠️ Old web motor (superseded by core/app.py) | Archive to `docs/archive/` |
| `motor_stdout_*.log` | Runtime logs | Clean up |
| `motor_v5_test.log` | Test logs | Clean up |
| `retreinar_output.log` | Retraining log | Clean up |
| `dashboard_pro.html` | Old dashboard HTML | Archive to `docs/archive/` |
| `capture_eventos_ms.py` | MS-level event capture script | Archive to `scripts/legacy/` |
| `ajuste_diario_202608.csv` | Daily adjustment data | Move to `dados/` |
| `walk_forward_*.json` | Walk-forward results | Move to `dados/` |
| `feature_registry.json` | Feature registry data | Move to `docs/` or `config/` |
| `strutura.txt` | Project structure file | Move to `docs/` |

---

## 3. Import Dependency Check Results

### Does production code reference archive or experimental code?

Scanning `adapters/*.py`, `core/*.py`, `features/*.py`, `ml/*.py`:

```
✅ No production code imports from docs/archive/
✅ No production code imports from root-level experimental tracker files
✅ No production code imports from experimental scripts in scripts/
```

**Conclusion: Production code is already cleanly isolated.**

### Potential Risk Points

| Check Item | Result |
|------------|--------|
| `adapters/profit_rtd.py` imports | ✅ Only depends on `adapters.*`, `core.*`, `collections`, `time`, `logging` |
| `core/app.py` imports | ✅ Only depends on `core.*`, `adapters.*`, `features.*`, `ml.*` |
| `features/*` imports | ✅ Only depends on `features.*`, `core.*` |
| `ml/*` imports | ✅ Only depends on `ml.*`, `features.*`, `core.*`, `numpy`, `pandas`, `lightgbm` |

---

## 4. Recommended Action Plan

### Immediate (Low Risk)

1. **Move root-level tracker files into `features/`**
   - `volatility_tracker.py` → `features/volatility_tracker.py` (if not covered by feature_engine)
   - `volume_relativo_tracker.py` → `features/volume_relativo.py`
   - `returns_tracker.py` → `features/returns_tracker.py`
   - `session_time_tracker.py` → `features/session_time.py`
   - `preco_context_tracker.py` → `features/price_context_tracker.py`

2. **Clean up unused files**
   - Delete `Local)` (git artifact, no purpose)
   - Archive `motor_web.py` → `docs/archive/motor_web_legacy.py`
   - Archive `dashboard_pro.html` → `docs/archive/`
   - Clean `.log` files → add to `.gitignore`

3. **Unify script locations**
   - `auto_sync.py` → `scripts/auto_sync.py`
   - `github.bat` → `scripts/github.bat`

### Medium-term (Medium Risk)

4. **Create explicit directory structure documentation**
   - Add a "Code Classification" section to `docs/ARCHITECTURE.md`
   - Every new file must declare which category it belongs to

5. **Add import lint rules**
   - In CI, add checks: production code cannot import non-module code

### Do Not Do (High Risk)

6. ~~Delete `motor_rt_alphaz.py` shim~~ — External tests may depend on it
7. ~~Delete root-level tracker files~~ — Must first confirm they are superseded by `features/` equivalents

---

## 5. Code Classification Decisions Log

| Decision | Reason | Status |
|----------|--------|--------|
| Keep `motor_rt_alphaz.py` | Backward compatibility shim, no dirty imports internally | ✅ Kept |
| Keep `watchdog.py` | Production guardian process, not experimental | ✅ Kept (needs review) |
| `poc_migration_tracker.py` pending | May duplicate `features/poc_migration.py` | ⏳ To investigate |
| Delete `Local)` file | Git issue artifact, no purpose | 🗑️ To delete |
| Archive `motor_web.py` | Fully superseded by `core/app.py` | 📦 Suggested archive |

---

## 6. Related File Index

| File | Path | Category |
|------|------|----------|
| Main app | `core/app.py` | PRODUCTION |
| RTD adapter | `adapters/profit_rtd.py` | PRODUCTION |
| Feature engine | `features/feature_engine.py` | PRODUCTION |
| ML scorer | `ml/scorer.py` | PRODUCTION |
| Old motor shim | `motor_rt_alphaz.py` | LEGACY(SHIM) |
| Old motor | `docs/archive/motor_rt_alphaz_v9_legacy.py` | LEGACY(ARCHIVE) |
| Old web motor | `motor_web.py` | LEGACY(suggested archive) |
| Volatility tracker | `volatility_tracker.py` | To classify |
| Volume tracker | `volume_relativo_tracker.py` | To classify |
| Returns tracker | `returns_tracker.py` | To classify |
| Session tracker | `session_time_tracker.py` | To classify |
| Price context tracker | `preco_context_tracker.py` | To classify |
| Watchdog | `watchdog.py` | PRODUCTION or LEGACY |

---

## Summary

✅ **Good news:** Current production code (`adapters/core/features/ml`) has **no** import references to `archive/` or experimental code — isolation is already achieved.

⚠️ **To improve:** The project root contains numerous ambiguous files (tracker scripts, old logs, batch files) that should be organized/archived to make the codebase cleaner and more maintainable.
