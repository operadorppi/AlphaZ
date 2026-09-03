# FASE 19 — P2: WATCHDOG E OBSERVABILIDADE

**Data:** 2026-08-30
**Status:** ✅ Concluída

## Objetivo

Criar sistema de métricas mínimas para monitoramento do trading system, organizado em 5 categorias principais.

---

## Arquitetura

### Módulo: `core/observability.py`

```
core/observability.py
├── MetricsCollector (classe principal)
│   ├── Contadores (counters)
│   ├── Gauges (valores atuais)
│   ├── Timers (latências)
│   ├── Histogramas (distribuições)
│   └── Metadados (modelo, schema)
├── get_metrics() (acesso global)
└── reset_metrics() (limpa estado)
```

---

## Categorias de Métricas

### 1. CAPTURA (10 métricas)

| Métrica | Descrição | Tipo |
|---------|-----------|------|
| `events_received` | Eventos recebidos do RTD | Counter |
| `events_processed` | Eventos processados com sucesso | Counter |
| `events_dropped` | Eventos descartados (motivo) | Counter |
| `events_duplicate` | Eventos duplicados detectados | Counter |
| `events_out_of_order` | Eventos fora de ordem | Counter |
| `book_snapshots` | Snapshots de book recebidos | Counter |
| `trades` | Trades processados | Counter |
| `tnt_rlp` | Eventos T&T RLP | Counter |
| `gaps` | Gaps detectados no fluxo | Counter |
| `timestamp_anomalies` | Anomalias de timestamp | Counter |

### 2. LATÊNCIA (4 métricas)

| Métrica | Descrição | Tipo |
|---------|-----------|------|
| `event_to_processing_ms` | Tempo do evento até processamento | Timer |
| `feature_latency_ms` | Tempo de computação de features | Timer |
| `ml_latency_ms` | Tempo de inferência ML | Timer |
| `decision_latency_ms` | Tempo de tomada de decisão | Timer |

### 3. DADOS (5 métricas)

| Métrica | Descrição | Tipo |
|---------|-----------|------|
| `book_snapshots` | Volume de snapshots de book | Counter |
| `trades` | Volume de trades | Counter |
| `tnt_rlp` | Eventos T&T RLP | Counter |
| `gaps` | Lacunas no fluxo de dados | Counter |
| `timestamp_anomalies` | Problemas de sincronia temporal | Counter |

### 4. ML (5 métricas)

| Métrica | Descrição | Tipo |
|---------|-----------|------|
| `model_loaded` | Modelo carregado com sucesso | Counter |
| `model_version` | Versão do modelo (metadata) | Info |
| `feature_schema` | Schema de features registrado | Info |
| `inference_errors` | Erros na inferência | Counter |
| `fallback_count` | Uso de fallback heurístico | Counter |

### 5. RISCO (4 métricas)

| Métrica | Descrição | Tipo |
|---------|-----------|------|
| `risk_blocks` | Blocos gerais de risco | Counter |
| `stale_blocks` | Blocos por dados obsoletos | Counter |
| `drawdown_blocks` | Blocos por drawdown excessivo | Counter |
| `exposure_blocks` | Blocos por exposição máxima | Counter |

---

## API de Uso

### Coletor Padrão (Global)

```python
from core.observability import get_metrics, reset_metrics

metrics = get_metrics()

# Captura
metrics.capture_event_received("WINV26")
metrics.capture_event_processed("WINV26")
metrics.capture_event_dropped(motivo="timeout")

# Latência
metrics.latency_start("event_to_processing")
# ... processar evento ...
metrics.latency_stop("event_to_processing")

# ML
metrics.ml_model_loaded(versao="v5", modelo_path="/path/modelo.pkl")
metrics.ml_inference_error(erro="timeout")
metrics.ml_fallback_used(motivo="ml_down")

# Risco
metrics.risk_block(motivo="spread_excessive", simbolo="WINV26")
metrics.risk_stale_block("WINV26")

# Snapshot
snapshot = metrics.snapshot()
```

### Coletor Isolado (Testes)

```python
from core.observability import MetricsCollector

collector = MetricsCollector(namespace="test")
collector.increment("capture.events_received")
assert collector.get_counter("capture.events_received") == 1
```

---

## Métricas Detalhadas

### Snapshot Format

```json
{
  "timestamp": "2026-08-30T17:00:00",
  "counters": {
    "trading.capture.events_received": 15000,
    "trading.capture.events_processed": 14980,
    "trading.capture.events_dropped": 20,
    "trading.capture.events_duplicate": 5,
    "trading.capture.events_out_of_order": 3
  },
  "gauges": {
    "trading.latency.event_to_processing_current": 12.5
  },
  "timers": {
    "trading.event_to_processing": {
      "count": 100,
      "min": 1.2,
      "max": 45.0,
      "avg": 8.5,
      "p50": 7.2,
      "p95": 25.0,
      "p99": 42.0
    }
  },
  "model_info": {
    "loaded": true,
    "version": "v5",
    "path": "/path/modelo.pkl"
  },
  "feature_schema": {
    "aggr_imb": "float",
    "cvd_total": "float"
  }
}
```

---

## Testes Criados

### `tests/test_observability.py` (32 testes)

| Classe | Testes | Cenários |
|--------|--------|----------|
| `TestCaptureMetrics` | 9 | events_received, processed, dropped, duplicate, out_of_order, book_snapshots, trades, gaps, timestamp_anomalies |
| `TestLatencyMetrics` | 6 | event_to_processing, feature_latency, ml_latency, decision_latency, múltiplas amostras, timer não iniciado |
| `TestDataMetrics` | 1 | tnt_rlp |
| `TestMLMetrics` | 5 | model_loaded, model_unloaded, feature_schema, inference_error, fallback_used |
| `TestRiskMetrics` | 4 | risk_block, stale_block, drawdown_block, exposure_block |
| `TestSnapshotAndStats` | 7 | snapshot sections, empty, reset, histogram stats, gauge operations, counter get, default collector |

---

## Resultado dos Testes

```
============================= 289 passed in 7.32s =============================
```

| Categoria | Quantidade |
|-----------|------------|
| Testes existentes (Fases 7-16) | 205 |
| Testes FASE 17 (Replay Realista) | 17 |
| Testes FASE 18 (Estresse) | 35 |
| Testes FASE 19 (Observabilidade) | 32 |
| **Total** | **289** |

---

## Integração com Sistema

### Adapter RTD (`adapters/profit_rtd.py`)

```python
from core.observability import get_metrics

metrics = get_metrics()

# No loop de eventos
for event in self.events():
    metrics.capture_event_received(sym)
    try:
        # Processar evento
        metrics.capture_event_processed(sym)
    except Exception as e:
        metrics.capture_event_dropped(motivo=str(e))
```

### Core App (`core/app.py`)

```python
from core.observability import get_metrics

metrics = latency_start("event_to_processing")
# Processar evento
metrics.latency_stop("event_to_processing")
metrics.capture_decision_latency(sym)
```

### Feature Engine (`features/feature_engine.py`)

```python
from core.observability import get_metrics

metrics = get_metrics()
metrics.capture_feature_latency(sym)
```

### ML Scorer (`ml/scorer.py`)

```python
from core.observability import get_metrics

metrics = get_metrics()
try:
    prob = self.model.predict(features)
    metrics.capture_ml_latency(sym)
except Exception as e:
    metrics.ml_inference_error(str(e))
    metrics.ml_fallback_used("inference_error")
```

### Risk Engine (`core/risk_engine.py`)

```python
from core.observability import get_metrics

metrics = get_metrics()
if not decision.allowed:
    metrics.risk_block(decision.reason, sym)
    if "stale" in decision.reason:
        metrics.risk_stale_block(sym)
    elif "drawdown" in decision.reason:
        metrics.risk_drawdown_block(sym)
    elif "exposure" in decision.reason:
        metrics.risk_exposure_block(sym)
```

---

## Próximos Passos Recomendados

1. **Dashboard HTTP**: Expôr métricas via endpoint HTTP (`:5001/metrics`)
2. **Prometheus**: Exportar em formato Prometheus para scraping
3. **Alertas**: Configurar alertas baseados em thresholds
4. **Log Estruturado**: Integrar métricas com logs para debugging
5. **Persistência**: Salvar snapshots periodicamente em arquivo/Parquet

---

## Arquivos Criados

| Arquivo | Linhas | Descrição |
|---------|--------|-----------|
| `core/observability.py` | 348 | Módulo principal de métricas |
| `tests/test_observability.py` | 338 | Testes unitários |
| `docs/FASE19_OBSERVABILITY.md` | este arquivo | Documentação da fase |
