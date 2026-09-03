# FASE 15 — P1 · Paridade Book Live × Batch

## 1. Problema Identificado

Verificamos a diferença temporal entre:
- **BOOK histórico** (batch): capturado via adapter RTD
- **BOOK live**: throttled a cada 250ms no adapter
- **ML features**: calculadas em janelas de 100ms

### Frequências identificadas:

| Componente | Frequência | Período | Origem |
|---|---|---|---|
| **Book Live (RTD)** | 4 Hz | 250ms | `adapters/profit_rtd.py:250` |
| **Trades (T&T)** | ~20 Hz | ~50ms | Captura contínua |
| **ML Janela** | 10 Hz | 100ms | `features/trade_features.py:janela_ms=100` |
| **Batch Processor** | 10 Hz | 100ms | `ml/batch_processor.py` |

### Discrepância:

```
Book live:    250ms entre snapshots (4 Hz)
ML features:  100ms entre janelas (10 Hz)

Razão: 2.5x
```

Isso significa que **a cada 2-3 janelas de ML, há apenas 1 snapshot de book atualizado**.

## 2. Impacto

- Features baseadas em book (`spread`, `imbalance`, `ofi`, etc.) ficam **desatualizadas**
- Até **2 janelas** podem rodar com book antigo
- Divergência potencial entre:
  - Treinamento (batch com book histórico completo)
  - Inferência (live com book throttled)

## 3. Soluções Propostas

### Opção 1: Aumentar frequência do book live para 100ms

**Vantagens:**
- Alinha book com janela ML (100ms)
- Book sempre atualizado para features

**Desvantagens:**
- Maior volume de dados (~2.5x mais snapshots)
- Mais processamento no adapter RTD

**Implementação:**
```python
# adapters/profit_rtd.py
# Alterar de:
if agora - self._last_book_yield[sym] > 0.25:  # 250ms
# Para:
if agora - self._last_book_yield[sym] > 0.10:  # 100ms
```

### Opção 2: Aumentar janela ML para 250ms

**Vantagens:**
- Alinha automaticamente com book
- Sem mudança no adapter

**Desvantagens:**
- Menor resolução temporal (perda de granularidade)
- Features mais "suavizadas"

**Implementação:**
```python
# features/trade_features.py / ml/batch_processor.py
# Alterar de:
janela_ms=100, passo_ms=100
# Para:
janela_ms=250, passo_ms=250
```

### Opção 3: Interpolar book entre snapshots

**Vantagens:**
- Mantém resolução atual (100ms)
- Book sempre "disponível" nas janelas

**Desvantagens:**
- Complexidade adicional
- Interpolação pode introduzir artefatos

## 4. Recomendação

**Opção 1 (aumentar book para 100ms)** é a mais simples e segura:
- Mínima alteração no código
- Alinha naturalmente com ML
- Sem perda de informação

## 5. Testes Criados

`tests/test_book_parity_live_batch.py`:
- `test_rt_adapter_throttle_is_250ms` — confirma throttle atual
- `test_gerador_janelas_default_params` — confirma janela ML (100ms)
- `test_batch_processor_usa_mesmos_params` — confirma batch also 100ms
- `test_book_features_calculated_on_trade_events` — book integrado
- `test_mismatch_if_book_less_frequent_than_trades` — documenta problema
- `test_ml_feature_window_alignment` — calcula ratio 2.5x
- `test_historical_book_frequency` — simula arquivo histórico
- `test_trade_frequency_is_higher` — trades vs book
- `test_feature_computation_when_book_missing` — gracefully degrade
- `test_recommendation_documented` — documentação da solução

## 6. Resultado

```bash
pytest tests/test_book_parity_live_batch.py -v
# ============================== 10 passed in 2.09s ==============================
```

**Nenhuma divergência crítica encontrada** — o sistema lida gracefulmente com book desatualizado, mas a recomendação é alinhar frequências para consistência máxima.
