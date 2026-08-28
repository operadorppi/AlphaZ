# CHANGELOG — 28 de Agosto de 2026 (Fases 7-17)

## Resumo das Fases Implementadas

| Fase | Nome | Arquivos | Status |
|------|------|----------|--------|
| 7 | Anti-Leakage | `tests/test_no_future_leakage.py` | ✅ |
| 8 | Feature Registry | `features/feature_registry.py` | ✅ |
| 9 | ML Profissional | `ml/model_metadata.py`, `ml/model_validation.py`, `ml/model_registry.py` | ✅ |
| 11 | Calibração | `core/calibration.py` | ✅ |
| 12 | Risk Engine | `core/risk_engine.py` | ✅ |
| 13 | Decision Journal | `core/decision_journal.py` | ✅ |
| 17 | CI/CD | `run_all_tests.py`, `run_ci.bat` | ✅ |

---

## 1. ARQUIVOS NOVOS

### `tests/test_no_future_leakage.py`
**Fase 7 — Anti-Leakage**

22 testes verificando 11 cenários de leakage:

```python
# Cenário 1: Book features
TestBookFeaturesNoFuture
├── test_spread_uses_only_current_levels
├── test_microprice_uses_only_current_volumes
└── test_hhi_uses_only_current_snapshot

# Cenário 2: OFI
TestOFINoFuture
├── test_ofi_events_are_incremental
└── test_ofi_no_lookahead

# Cenário 3: Janela trades
TestJanelaNoFuture
├── test_aggregate_imbalance_uses_only_past
└── test_price_efficiency_no_lookahead

# Cenário 4: Labels
TestLabelNoContamination
└── test_label_after_features

# Cenário 5: Normalização
TestNormalizationNoFuture
├── test_ewma_update_no_lookahead
└── test_percentil_tracker_uses_only_past

# Cenário 6: VWAP
TestVWAPNoFuture
├── test_vwap_accumulates_only_past
└── test_vwap_snapshot_no_mutation

# Cenário 7: Ajuste
TestAjusteNoContamination
├── test_ajuste_is_static
└── test_dist_ajuste_uses_static_value

# Cenário 8: Regime
TestRegimeNoFuture
└── test_regime_uses_historical_window

# Cenário 9: Session boundary
TestSessionBoundary
├── test_OFI_resets_between_sessions
├── test_book_level_resets_between_sessions
└── test_vwap_resets_between_sessions

# Cenário 10: Cross-asset
TestCrossAssetNoFuture
└── test_cross_asset_lag_is_positive

# Cenário 11: Replay
TestReplayNoLookahead
├── test_replay_processar_evento
└── test_replay_no_cross_temporal

# Integração
TestIntegrationLeakage
└── test_full_pipeline_no_lookahead
```

---

### `features/feature_registry.py`
**Fase 8 — Feature Registry**

70 features documentadas com metadados completos:

```python
@dataclass
class FeatureDefinition:
    name: str                    # 'ofi'
    version: str                 # '1.0'
    description: str             # 'Order Flow Imbalance'
    dtype: str                   # 'float64'
    unit: str                    # 'contracts'
    causal: bool                 # True
    lookback_ms: int             # 0
    source: str                  # 'book'
    module: str                  # 'features.book_features'
    dependencies: List[str]      # []
    nan_strategy: str            # 'zero'
    warmup_periods: int          # 1
    regime_aware: bool           # False
    regimes: List[str]           # []

class FeatureRegistry:
    register(feature)
    get(name)
    list_all()
    list_by_source(source)
    list_causal_only()
    validate_dataset(columns)
    to_json(path)
    to_markdown()
```

---

### `ml/model_metadata.py`
**Fase 9 — ML Profissional**

```python
@dataclass
class DatasetInfo:
    path, hash_sha256, n_rows, n_cols, n_features
    n_labels_pos, n_labels_neg, n_labels_neutro
    date_start, date_end, ativo

class FeatureSet:
    names, version, n_features, description

class LabelConfig:
    method, tp_pts, sl_pts, max_holding_s
    purge_s, embargo_s, version

class TrainConfig:
    algorithm, n_estimators, max_depth, learning_rate
    subsample, colsample_bytree, class_weight
    cv_strategy, n_folds, purge_days, embargo_days

class ModelMetrics:
    accuracy, auc_roc, brier_score, ece
    precision, recall, f1, profit_factor
    expectancy, sharpe, max_drawdown
    fold_metrics

class ModelMetadata:
    model_id, model_name, version, description
    algorithm, framework_version
    dataset, features, labels, train_config
    train_start, train_end, train_date
    metrics, model_path, model_hash
    feature_importance, author, tags
    save(path), load(path)
```

---

### `ml/model_validation.py`
**Fase 9 — ML Profissional**

```python
class ModelValidator:
    validate(metadata, train_pred, train_out, test_pred, test_out)
    _check_metrics()        # accuracy, AUC, ECE, Brier
    _check_sanity()         # > random, AUC > 0.5, feature count
    _check_overfitting()    # gap treino/teste
    _check_feature_importance()  # top 3 features
    _check_dataset()        # hash, balanceamento
    _generate_recommendations()
    to_markdown(report)
```

---

### `ml/model_registry.py`
**Fase 9 — ML Profissional**

```python
class ModelRegistry:
    register(metadata, model_path, validation_report)
    get(model_id)
    list_all()
    list_by_algorithm(algorithm)
    list_by_tag(tag)
    get_production()
    promote(model_id, reason)
    compare(model_id_1, model_id_2)
    get_history(limit)
    count()
    export_summary()
```

---

### `core/calibration.py`
**Fase 11 — Calibração**

```python
class ProbabilityCalibrator:
    update(predicted_prob, actual_outcome, regime)
    get_threshold(regime)
    get_metrics()
    plot_calibration_curve()

class ThresholdOptimizer:
    optimize(predictions, outcomes, regime)
    optimize_by_regime(predictions, outcomes, regimes)

class ModelDecisionSeparator:
    separate(ml_prob, regime, confianca, score_heuristico)
    save(path), load(path)
```

---

### `core/risk_engine.py`
**Fase 12 — Risk Engine**

14 proteções implementadas:

```python
class RiskEngine:
    avaliar(signal, resultados_recentes) -> RiskDecision
    registrar_resultado(pnl, acertou)
    atualizar_mercado(**kwargs)
    ativar_kill_switch()
    desativar_kill_switch()
    reset_diario()
    get_estado()

    # 14 proteções
    _check_kill_switch()        # 13
    _check_circuit_breaker()    # 14
    _check_daily_loss()         # 1
    _check_max_trades()         # 4
    _check_consecutive_loss()   # 6
    _check_cooldown()           # 5
    _check_session()            # 12
    _check_stale_data()         # 7
    _check_spread()             # 8
    _check_volatility()         # 9
    _check_model_availability() # 10
    _check_confidence()         # 11
    _check_exposure()           # 2
    _check_position()           # 3
```

---

### `core/decision_journal.py`
**Fase 13 — Decision Journal**

```python
@dataclass
class DecisionEntry:
    id, ts_ms, ativo, acao, lado, preco
    score, confianca, ml_prob, sinal
    regime, regime_info
    risk_decision, risk_motivo
    tp, sl, rr_ratio, size
    motivos, features_relevantes
    preco_ref, spread, ofi, microprice
    dist_vwap, dist_abertura
    modelo, model_version
    latencia_ms, session_ts

class DecisionJournal:
    registrar(entry)
    buscar(ts_ms, id)
    listar(ativo, acao, limite)
    ultima_decisao(ativo)
    resumo(ativo)
    count()
    exportar_json(path)
```

---

### `run_all_tests.py` + `run_ci.bat`
**Fase 17 — CI/CD**

```python
# 9 steps do pipeline
step_syntax_check()      # 22 arquivos compilam
step_lint()              # 21 checks básicos
step_type_check()        # 14 imports funcionam
step_unit_tests()        # 22 testes leakage
step_leakage_tests()     # 22 testes separados
step_determinism_tests() # 4 testes de repetibilidade
step_registry_validation()  # 4 checks do registry
step_journal_validation()   # 4 checks do journal
step_artifact_validation()  # 5 arquivos críticos
```

---

## 2. ARQUIVOS MODIFICADOS

### `core/signal_engine.py`
**Mudanças:**
- Import: `from features.feature_registry import REGISTRY`
- Import: `from core.calibration import create_calibration_system`
- `__init__`: Adicionado `self.calibration = create_calibration_system(self.config)`
- `calcular()`: Adicionado `self._registry_validado` para validar features na primeira chamada
- `calcular()`: RangeTracker agora expõe `range_estado` no dict `f`
- `caliar()`: Contexto institucional (VWAP, abertura, máxima, mínima, ajuste) via `InstitutionalContext`
- `avaliar()`: ML probability agora usa `ModelDecisionSeparator` para calibrar
- `avaliar()`: Se ML e heurística discordam, penalidade de 0.7x no score
- `get_features()`: Adicionado range + contexto institucional em features que não têm `calcular()`
- `get_sinais()`: Usa `asdict()` + campo `sinal` derivado de `lado`
- Adicionado `import math`

### `core/app.py`
**Mudanças:**
- Import: `from core.decision_journal import DecisionJournal, DecisionEntry`
- Import: `from core.risk_engine import RiskEngine`
- `__init__`: Adicionado `self.journal = DecisionJournal(self.save_dir, self.session_ts)`
- `__init__`: Adicionado `self.risk_engine = RiskEngine(config=self.config)`
- `_loop()`: Book capture descomentada — `self.captura.registrar_book(...)` com conversão de `BookSnapshot`
- `_loop()`: Risk Engine agora avalia com `self.risk_engine.avaliar(sig)`
- `_loop()`: Decision Journal registra cada ABRIR/FECHAR

### `core/contracts.py`
**Mudanças:**
- `RiskDecision`: Adicionado `risk_level: str = "normal"`
- `RiskDecision`: Adicionado `risk_components: dict = None`
- `RiskDecision`: Adicionado `__post_init__` para inicializar `risk_components`

### `core/market_state.py`
**Mudanças:**
- Import: Adicionado `InstitutionalContext`
- `trackers_factory`: Adicionado `'inst_context': InstitutionalContext()`

### `features/book_features.py`
**Mudanças:**
- `_extrair_pares()`: Agora aceita numpy arrays, pandas Series (não só list/dict)

### `features/__init__.py`
**Mudanças:**
- Import: `from .institutional_context import InstitutionalContext`
- `__all__`: Adicionado `'InstitutionalContext'`

### `features/institutional_context.py` (ANTES era novo, agora é editado)
**Mudanças:**
- `__init__`: Adicionado `self._ajuste_oficial`
- `_get_state()`: Adicionado `'ajuste'`, `'dist_ajuste_anterior'`, `'bounces_ajuste'`
- `update()`: Adicionado detecção de bounce no ajuste
- `set_ajuste()`: Novo método para definir ajuste D-1
- `compute()`: Adicionado `dist_ajuste_pts`, `dist_ajuste_norm`, `zona_ajuste`, `bounces_ajuste_norm`, `reversao_perto_ajuste`
- `reset_diario()`: Reset de `bounces_ajuste`

### `config.json`
**Mudanças:**
- `horarios.abertura_fim`: `[10, 0]` → `[9, 0]`
- `horarios.fechamento`: `[16, 30]` → `[18, 30]`
- `horarios.almoco_inicio`: `[12, 0]` → `[99, 0]` (desabilitado)
- `horarios.almoco_fim`: `[13, 30]` → `[99, 0]` (desabilitado)
- `ml_modelo`: Path corrigido para `D:\MarketData\mimo\26\modelo_lgbm_v4_limpo.pkl`

### `ml/retreinar_lgbm_limpo.py`
**Mudanças:**
- Import: Adicionado `datetime`
- Import: Adicionado `sys.path.insert` para `features.feature_registry`
- Após treino: Criado `ModelMetadata` completo
- Após treino: Rodado `ModelValidator.validate()`
- Após treino: Registrado no `ModelRegistry`
- Após treino: Auto-promovido se válido
- Após treino: Salvo relatório de validação

### `dashboard_pro.html`
**Mudanças:**
- Adicionado card "CONTEXTO DE MERCADO" com VWAP, abertura, máxima, mínima, ajuste, amplitude
- Adicionado JavaScript para popular os campos

### `adapters/dashboard_api.py`
**Mudanças:**
- Adicionado endpoint `/api/decisoes` (últimas 50 decisões)
- Adicionado endpoint `/api/decisoes/{id}` (buscar por ID)

### `run_motor.py`
**Mudanças:**
- Adicionado log do Feature Registry no startup

### `ml/retreinar_lgbm_limpo.py`
**Mudanças:**
- Paths hardcoded corrigidos para `D:\MarketData\mimo\26\`

---

## 3. ARQUIVOS DE DOCUMENTAÇÃO

| Arquivo | Conteúdo |
|---------|----------|
| `CHANGELOG_2026-08-28.md` | Changelog v1 (bugs + features) |
| `CHANGELOG_2026-08-28_v2.md` | Changelog v2 (completo) |
| `CHANGELOG_2026-08-28_FASES.md` | Este arquivo (Fases 7-17) |
| `FEATURE_REGISTRY.md` | 70 features documentadas |
| `feature_registry.json` | Registry exportado |

---

*Gerado por Buffy 🤖 em 28/08/2026*
