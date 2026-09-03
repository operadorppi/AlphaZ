# Relatório de Rastreamento End-to-End — Variáveis e Features

> Data: 2026-08-29
> Versão: 1.0
> Escopo: RAW → Processamento → Features → Dataset ML → Treinamento → Inferência → Dashboard

---

## 1. RESUMO EXECUTIVO

### Problemas Críticos Encontrados

| # | Problema | Severidade | Impacto |
|---|----------|------------|---------|
| C1 | `preco_saida` vazando no dataset de treino | **CRÍTICA** | Vazamento de dados — modelo vê resultado antes de prever |
| C2 | `duracao_label_ms` vazando no dataset de treino | **CRÍTICA** | Vazamento de dados — duração do trade usada como feature |
| C3 | Discrepância de nomenclatura: `dist_vwap_pts` (registry) vs `dist_vwap_pts` (scorer usa `dist_vwap_pts` + `dist_vwap_ticks`) | **MÉDIA** | Features duplicadas/com nome diferente entre live e batch |
| C4 | Feature `regime_*` calculada no batch mas não calculada no live | **ALTA** | Inconsistência treino-produção |
| C5 | Feature `vwap_inclinacao_*` calculada no batch mas não no live | **MÉDIA** | Diferença treino-produção |
| C6 | `posicao_range_dia` (contexto_preco) vs `posicao_relativa` (institutional_context) — nomes diferentes | **BAIXA** | Confusão mas funcionalidade similar |
| C7 | Dashboards mostra `confianca_ewma` vs ML usa `confianca` do Signal | **BAIXA** | Pequena inconsistência de display |
| C8 | Feature `vp_vp_total` no ML vs `vp_vp_total` no feature_registry — mesma feature, nome ok | **NENHUM** | Confirmado: consistente |

### Features Calculadas mas Nunca Utilizadas (Live)

| Feature | Onde é calculada | Onde deveria ser usada | Status |
|---------|------------------|------------------------|--------|
| `regime_*` (7 features) | `features_contexto_avancado.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `vwap_inclinacao_1m/5m` | `features_contexto_avancado.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `atr_14` / `atr_14_norm` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `vol_expansao/compressao` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `range_vs_media/mediana/percentil` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `retorno_norm_vol` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `aceleracao_retorno` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `regime_vol_ratio` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `regime_range_ratio` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `regime_pos_vs_poc` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `regime_persistencia` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |
| `regime_vol_accel` | `build_dataset_v950.py` | `ml/scorer.py` | ❌ NÃO injetada no live |

### Features no Dataset ML mas Não Calculadas no Live

Estas features existem no parquet de treino mas **não são recalculadas** pelo `ScorerML` ao vivo:

| Feature | Origem | Presença no Live |
|---------|--------|-----------------|
| `regime_realiz_vol` | v950 batch | ❌ |
| `regime_realiz_vol_bps` | v950 batch | ❌ |
| `regime_vol_zscore` | v950 batch | ❌ |
| `regime_aggr_persistencia` | v950 batch | ❌ |
| `regime_cvd_aceleracao` | v950 batch | ❌ |
| `regime_range_dia_norm` | v950 batch | ❌ |
| `vwap_inclinacao_1m` | v950 batch | ❌ |
| `vwap_inclinacao_5m` | v950 batch | ❌ |
| `atr_14` | v950 batch | ❌ |
| `atr_14_norm` | v950 batch | ❌ |
| `vol_expansao` | v950 batch | ❌ |
| `vol_compressao` | v950 batch | ❌ |
| `vol_acelerando` | v950 batch | ❌ |
| `vol_desacelerando` | v950 batch | ❌ |
| `vol_aceleracao_mag` | v950 batch | ❌ |
| `range_vs_media` | v950 batch | ❌ |
| `range_vs_mediana` | v950 batch | ❌ |
| `range_percentil` | v950 batch | ❌ |
| `retorno_norm_vol` | v950 batch | ❌ |
| `aceleracao_retorno` | v950 batch | ❌ |
| `aceleracao_retorno_norm` | v950 batch | ❌ |
| `regime_vol_ratio` | v950 batch | ❌ |
| `regime_range_ratio` | ❌ | v950 batch |
| `regime_pos_vs_vwap` | v950 batch | ❌ |
| `regime_pos_vs_ajuste` | v950 batch | ❌ |
| `regime_persistencia` | v950 batch | ❌ |
| `regime_vol_accel` | v950 batch | ❌ |

### Features Silenciosamente Descartadas no Live

| Feature | Motivo do Descarte |
|---------|-------------------|
| Todas as `regime_*` (7) | Scorer não chama `adicionar_features_regime()` |
| `atr_*` (2) | Scorer não chama `adicionar_atr()` |
| `vol_expansao/compressao/...` (4) | Scorer não chama `adicionar_regime_vol()` |
| `range_vs_*` (3) | Scorer não chama `adicionar_range_stats()` |
| `retorno_norm_vol` (1) | Scorer não chama `adicionar_retorno_aceleracao()` |
| `aceleracao_retorno` (2) | Scorer não chama `adicionar_retorno_aceleracao()` |

---

## 2. RASTREAMENTO: VARIAVEL POR VARIAVEL

### 2.1 `aggr_imb` (Imbalance de Agressão)

```
RAW:    T&T lines [QUL, AGR] → profit_rtd.py → TradeEvent
↓
EXTRACAO:  JanelaFeatures.add_evento() em trade_features.py
↓
NORMALIZACAO: (vol_compra - vol_venda) / vol_total
↓
FEATURE:  FeatureEngine.processar_lote() retorna aggr_imb
↓
ARMAZENAMENTO: features_por_seg no MarketState
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim ( LightGBM)
↓
INFERENCIA:  Sim (calculada no live via FeatureEngine)
↓
DASHBOARD:   Sim (/api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.2 `cvd_total` (Cumulative Volume Delta)

```
RAW:    T&T lines → trade_features.py
↓
EXTRACAO: JanelaFeatures._cvd_total acumulado
↓
NORMALIZACAO: vc - vv (acumulado desde abertura do dia)
↓
FEATURE:  FeatureEngine.processar_lote() → f['cvd_total']
↓
ARMAZENAMENTO: features_por_seg no MarketState
↓
DATASET ML: Sim
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim
↓
DASHBOARD:   Sim (/api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.3 `vwap` (Volume-Weighted Average Price)

```
RAW:    T&T lines → trade_features.py + VWAPTracker
↓
EXTRACAO: VWAPTracker.update(ts_ms, preco, qtd)
↓
NORMALIZACAO: sum(preco*qtd) / sum(qtd) (intraday, causal)
↓
FEATURE:  VWAPTracker.snapshot() → {vwap, dist_vwap_pts, ...}
↓
ARMAZENAMENTO: Não persistido em arquivo (estado em memória)
↓
DATASET ML: Sim (v950 batch usa adicionar_vwap_causal())
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (ScorerML.vwaps[ativo])
↓
DASHBOARD:   Sim (/api/contexto → dist_vwap_pts)
```
**Status: ⚠️ PARCIALMENTE CONSISTENTE**
- No batch: VWAP é calculado via `adicionar_vwap_causal()` (cumsum de preço)
- No live: VWAP é calculado via `VWAPTracker` (cumsum de preco*qtd)
- **Divergência**: método de cálculo diferente → valores numéricos diferentes

---

### 2.4 `dist_vwap_pts` (Distância ao VWAP)

```
RAW:    Derivada do VWAP
↓
EXTRACAO: VWAPTracker.dist_vwap_pts
↓
NORMALIZACAO: preco - vwap
↓
FEATURE:  ScorerML._prever() → injeta no row
↓
ARMAZENAMENTO: Não persistido (estado em memória)
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/contexto)
```
**Status: ✅ CONSISTENTE (mas valores diferentes do batch por causa do VWAP)**

---

### 2.5 `kyle_kyle_lambda` (Kyle's Lambda)

```
RAW:    T&T lines → trade_features.py
↓
EXTRACAO: KyleLambdaTracker.atualizar(preco, qtd, agressor)
↓
NORMALIZACAO: regressão ΔP ~ λ*V_signed (janela 200ms)
↓
FEATURE:  GeradorJanelas.processar_evento() → snap['kyle']
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (presente no parquet v950)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.6 `vp_vp_total` (Volume Profile Total)

```
RAW:    T&T lines → trade_features.py
↓
EXTRACAO: VolumeProfileTracker.atualizar(preco, qtd, agressor)
↓
NORMALIZACAO: histograma de volume por preço (POC, VAH, VAL)
↓
FEATURE:  GeradorJanelas.processar_evento() → snap['vp']
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (top feature: importância 682)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.7 `spread` (Spread Bid-Ask)

```
RAW:    BOOK lines [OCP, VOC, OVD, VOV]
↓
EXTRACAO: BookLevelFeatures.calcular()
↓
NORMALIZACAO: best_ask - best_bid
↓
FEATURE:  BookLevelFeatures → res['spread']
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live via book snapshot)
↓
DASHBOARD:   Sim (via /api/book_level)
```
**Status: ✅ CONSISTENTE**

---

### 2.8 `vpin` (VPIN)

```
RAW:    T&T lines → trade_features.py
↓
EXTRACAO: VPINTracker.add_evento(qtd, agressor)
↓
NORMALIZACAO: buckets de volume, cálculo de PIN
↓
FEATURE:  JanelaFeatures.snapshot() → snap['vpin']
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (segunda feature mais importante: 510)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.9 `ofi_total` / `ofi_ewma` (Order Flow Imbalance)

```
RAW:    BOOK lines [OCP, VOC, OVD, VOV]
↓
EXTRACAO: OFITracker.atualizar(bid_levels, ask_levels)
↓
NORMALIZACAO: sum(bid_event - ask_event) por nível de preço
↓
FEATURE:  BookLevelFeatures → res['ofi'] + OFITracker.get_ofi()
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/book_level)
```
**Status: ✅ CONSISTENTE**

---

### 2.10 `regime` (Market Regime — Direção × Volatilidade)

```
RAW:    Não há raw direto — derivado de features
↓
EXTRACAO: RegimeDetector.detectar(ativo, historico)
↓
NORMALIZACAO: delta_preco > 20 e aggr_medio > 0.15 → tendencia
            range_vol_bps com percentis adaptativos → vol alta/baixa
↓
FEATURE:  SignalEngine.avaliar() → f['regime']
↓
ARMAZENAMENTO: Sim (no parquet como coluna 'regime')
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim (mas regime é feature de entrada, não label)
↓
INFERENCIA:  Sim (calculado live)
↓
DASHBOARD:   Sim (via /api/features)
```
**Status: ✅ CONSISTENTE**

---

### 2.11 `regime_realiz_vol` (Volatilidade Realizada — Novo no v950)

```
RAW:    Não existe
↓
EXTRACAO: build_dataset_v950.py → adicionar_regime_vol()
↓
NORMALIZACAO: EWMA de |retorno| (short vs long)
↓
FEATURE:  Somente no batch (v950)
↓
ARMAZENAMENTO: Sim (no parquet v950)
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  ❌ NÃO CALCULADO NO LIVE
↓
DASHBOARD:   ❌ NÃO EXIBIDO
```
**Status: ❌ INCONSISTENTE — Calculada só no batch, não no live**

---

### 2.12 `atr_14` (Average True Range)

```
RAW:    Não existe
↓
EXTRACAO: build_dataset_v950.py → adicionar_atr()
↓
NORMALIZACAO: EWMA de |Δpreco| (alpha=2/15)
↓
FEATURE:  Somente no batch (v950)
↓
ARMAZENAMENTO: Sim (no parquet v950)
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  ❌ NÃO CALCULADO NO LIVE
↓
DASHBOARD:   ❌ NÃO EXIBIDO
```
**Status: ❌ INCONSISTENTE — Calculada só no batch, não no live**

---

### 2.13 `posicao_range_dia` (Posição no Range do Dia)

```
RAW:    Derivado de maxima_dia e minima_dia
↓
EXTRACAO: features_contexto_preco.py → adicionar_contexto_preco()
↓
NORMALIZACAO: (preco - minima) / (maxima - minima)
↓
FEATURE:  Somente no batch
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim
↓
TREINAMENTO: Sim
↓
INFERENCIA:  ❌ NÃO CALCULADO NO LIVE (mas institutional_context.py calcula posicao_relativa)
↓
DASHBOARD:   ❌ NÃO EXIBIDO
```
**Status: ⚠️ CONFLITO DE NOMES — `posicao_range_dia` (batch) vs `posicao_relativa` (live)**

---

### 2.14 `dist_ajuste_oficial_pts` (Distância ao Ajuste Oficial)

```
RAW:    Tabela oficial B3 (csv/parquet)
↓
EXTRACAO: ml/features_contexto_avancado.py → adicionar_ajuste_oficial()
↓
NORMALIZACAO: preco - ajuste_anterior_oficial
↓
FEATURE:  Somente no batch
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim
↓
TREINAMENTO: Sim
↓
INFERENCIA:  ⚠️ PARCIAL — ScorerML usa ajuste_anterior_oficial do ajuste_diario.csv
↓
DASHBOARD:   Sim (via /api/contexto)
```
**Status: ⚠️ PARCIALMENTE CONSISTENTE**
- No batch: calculado via `adicionar_ajuste_oficial()`
- No live: calculado via `ScorerML._atualizar_ajuste_para_dia()` (mesma lógica, fonte diferente)

---

### 2.15 `cross_lag` (Lag Temporal WIN×WDO)

```
RAW:    T&T de WIN e WDO
↓
EXTRACAO: CrossAssetEngine.registrar()
↓
NORMALIZACAO: lag_ms = tempo entre movimento WIN e movimento WDO
↓
FEATURE:  CrossAssetManager.calcular_para_ativo()
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (presente no parquet)
↓
TREINAMENTO: Sim
↓
INFERENCIA:  Sim (calculado live via CrossAssetManager)
↓
DASHBOARD:   Sim (via /api/book_level)
```
**Status: ✅ CONSISTENTE**

---

### 2.16 `label` (Target do Modelo)

```
RAW:    Não existe — derivado do preço futuro
↓
EXTRACAO: ml/labeler_vectorizado.py → label_vectorizado()
↓
NORMALIZACAO: Triple Barrier (TP=100, SL=50, max_holding=30s)
↓
FEATURE:  Label (não feature)
↓
ARMAZENAMENTO: Sim (no parquet)
↓
DATASET ML: Sim (coluna 'label')
↓
TREINAMENTO: Sim
↓
INFERENCIA:  N/A (label não existe em produção)
↓
DASHBOARD:   N/A
```
**Status: ✅ CONSISTENTE**

---

## 3. ANÁLISE DE VAZAMENTO (LEAKAGE)

### 3.1 `preco_saida` — VAZAMENTO CRÍTICO

**Onde aparece:** `ml/retreinar_lgbm_limpo.py`

```python
# Linha ~33: BLACKLIST explícita
LEAKAGE_FEATURES = {'preco_saida', 'duracao_label_ms'}

# Linha ~80: colunas_validas filtra essas colunas
PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', ...
             'preco_saida', 'tp_atingido', 'sl_atingido', ...]
```

**Problema:** O parquet `dataset_final.parquet` ainda contém `preco_saida` e `duracao_label_ms` como colunas. O `retreinar_lgbm_limpo.py` filtra essas colunas explicitamente na função `colunas_validas()`.

**Status:** ✅ **CORRIGIDO** no código de treino (filtro ativo). Porém, o parquet ainda contém as colunas de vazamento.

### 3.2 `duracao_label_ms` — VAZAMENTO CRÍTICO

**Onde aparece:** Mesmo arquivo acima.

**Problema:** Duração do trade (quanto tempo levou para TP/SL) é uma feature que só existe APÓS o trade fechar. Usá-la como feature de entrada é vazamento puro.

**Status:** ✅ **CORRIGIDO** no código de treino (filtro ativo).

### 3.3 `cruzou_vwap` — POTENCIAL VAZAMENTO

**Onde aparece:** `features_contexto_avancado.py`

```python
# Cruzou VWAP: lado (acima/abaixo) mudou vs snapshot anterior
lado = (preco > vwap).astype('Int64')
lado_prev = lado.groupby(df[ativo_col]).shift(1)
df['cruzou_vwap'] = (lado != lado_prev).astype('float64')
```

**Análise:** O `cruzou_vwap` usa `shift(1)`, ou seja, compara com o trade ANTERIOR. Isso é causal (não olha o futuro). **Não é vazamento.**

**Status:** ✅ SEGURO

### 3.4 `maxima_dia` / `minima_dia` — POTENCIAL VAZAMENTO

**Onde aparece:** `features_contexto_preco.py`

```python
df['maxima_dia'] = g.transform(lambda s: s.expanding().max())
df['minima_dia'] = g.transform(lambda s: s.expanding().min())
```

**Análise:** `expanding().max()` usa APENAS dados até o timestamp atual (t). Isso é causal. **Não é vazamento.**

**Status:** ✅ SEGURO

### 3.5 `retorno_*x100ms` — POTENCIAL VAZAMENTO

**Onde aparece:** `features_expansao.py`

```python
rets = [1, 5, 10, 50, 100, 150, 300, 500]  # em linhas (100ms cada)
for n in rets:
    df[f"retorno_{n}x100ms"] = g.transform(lambda s: s.pct_change(n))
```

**Análise:** `pct_change(n)` usa dados passados (n linhas atrás). **Causal. Não é vazamento.**

**Status:** ✅ SEGURO

---

## 4. INCONSISTÊNCIAS DE NOMENCLATURA

### 4.1 Tabela de Nomes Divergentes

| Feature (Registry) | Feature (Batch) | Feature (Live) | Consistente? |
|--------------------|-----------------|----------------|--------------|
| `posicao_relativa` | `posicao_range_dia` | `posicao_relativa` | ⚠️ Batch usa nome diferente |
| `dist_vwap_pts` | `dist_vwap_pts` | `dist_vwap_pts` | ✅ |
| `dist_abertura_pts` | `dist_abertura_pts` | `dist_abertura_pts` | ✅ |
| `zona_vwap` | `zona_vwap` | `zona_vwap` | ✅ |
| `bounces_vwap_norm` | `bounces_vwap_norm` | `bounces_vwap_norm` | ✅ |
| `reversao_perto_vwap` | `reversao_perto_vwap` | `reversao_perto_vwap` | ✅ |

### 4.2 Features com Mesma Funcionalidade, Nomes Diferentes

| Nome no Batch | Nome no Live | Funcionalidade |
|---------------|--------------|----------------|
| `posicao_range_dia` | `posicao_relativa` | Posição no range do dia (0-1) |
| `dist_ajuste_pts` (contexto_preco) | `dist_ajuste_pts` (institutional_context) | Mesma feature, calculada em dois lugares |

---

## 5. FEATURES AUSENTES NO DASHBOARD

### 5.1 Features do Backend mas Não Exibidas

| Feature | Endpoint | Onde Deveria Aparecer |
|---------|----------|----------------------|
| `atr_14` | `/api/features` | Não exibido |
| `atr_14_norm` | `/api/features` | Não exibido |
| `regime_realiz_vol` | `/api/features` | Não exibido |
| `regime_realiz_vol_bps` | `/api/features` | Não exibido |
| `vwap_inclinacao_1m` | `/api/features` | Não exibido |
| `vwap_inclinacao_5m` | `/api/features` | Não exibido |
| `vol_expansao` | `/api/features` | Não exibido |
| `vol_compressao` | `/api/features` | Não exibido |

### 5.2 Features do Dashboard que Não Correspondem ao ML

| Feature no Dashboard | Valor Real no ML | Discrepância |
|---------------------|------------------|--------------|
| `confianca_ewma` (position_manager) | `confianca` (signal_engine) | Pequena: signal_engine usa score bruto, position_manager usa EWMA do score |
| `score` (signal_engine) | `score` (signal_engine) | ✅ Consistente |
| `ml_prob` (signal_engine) | `ml_prob` (signal_engine) | ✅ Consistente |

---

## 6. TIMESTAMPS INCOMPATÍVEIS

### 6.1 Epoch vs Time-of-Day

| Fonte | Timestamp | Formato | Uso |
|-------|-----------|---------|-----|
| RTD (T&T) | `DAT` field | HH:MM:SS.mmm | Time-of-day |
| RTD (Book) | `snapshot` | epoch ms | Epoch |
| FileStorage | `ts_ms` | epoch ms | Epoch |
| Parquet (TT) | `time_ms` | epoch ms | Epoch |
| Parquet (BOOK) | `time_ms` | epoch ms | Epoch |

**Problema:** O `profit_rtd.py` converte TOD → epoch usando um offset calculado no runtime:
```python
offset = agora_epoch - agora_tod
tms_epoch = offset + tms  # T&T tod → epoch ms
```
Isso pode causar drift se o relógio do sistema mudar (ex: DST, NTP sync).

### 6.2 Timestamp no Dataset ML

| Coluna | Formato | Uso |
|--------|---------|-----|
| `ts_ms` | epoch ms | Chave de merge |
| `_dia` | int (epoch days) | Agrupamento por dia |
| `data` | date | Split treino/teste |

**Status:** ✅ Consistente (tudo em epoch ms)

---

## 7. FEATURES SILENCIOSAMENTE DESCARTADAS

### 7.1 Lista Completa

| Feature | Motivo do Descarte | Impacto no Modelo |
|---------|-------------------|-------------------|
| `regime_realiz_vol` | Scorer não chama `adicionar_features_regime()` | Modelo perde signal de volatilidade |
| `regime_realiz_vol_bps` | Idem | Modelo perde signal de volatilidade em bps |
| `regime_vol_zscore` | Idem | Modelo perde signal de vol de vol |
| `regime_aggr_persistencia` | Idem | Modelo perde signal de persistência do fluxo |
| `regime_cvd_aceleracao` | Idem | Modelo perde signal de aceleração do CVD |
| `vwap_inclinacao_1m` | Idem | Modelo perde signal de inclinação do VWAP |
| `vwap_inclinacao_5m` | Idem | Modelo perde signal de inclinação do VWAP (5min) |
| `atr_14` | Idem | Modelo perde signal de ATR |
| `atr_14_norm` | Idem | Modelo perde signal de ATR normalizado |
| `vol_expansao` | Idem | Modelo perde signal de expansão de vol |
| `vol_compressao` | Idem | Modelo perde signal de compressão de vol |
| `vol_acelerando` | Idem | Modelo perde signal de aceleração de vol |
| `vol_desacelerando` | Idem | Modelo perde signal de desaceleração de vol |
| `range_vs_media` | Idem | Modelo perde signal de range vs média |
| `range_vs_mediana` | Idem | Modelo perde signal de range vs mediana |
| `range_percentil` | Idem | Modelo perde signal de percentil de range |
| `retorno_norm_vol` | Idem | Modelo perde signal de retorno normalizado |
| `aceleracao_retorno` | Idem | Modelo perde signal de aceleração de retorno |
| `aceleracao_retorno_norm` | Idem | Modelo perde signal de aceleração normalizada |
| `regime_vol_ratio` | Idem | Modelo perde signal de ratio vol short/long |
| `regime_range_ratio` | Idem | Modelo perde signal de ratio range |
| `regime_pos_vs_vwap` | Idem | Modelo perde signal de posição vs VWAP |
| `regime_pos_vs_ajuste` | Idem | Modelo perde signal de posição vs ajuste |
| `regime_persistencia` | Idem | Modelo perde signal de persistência do regime |
| `regime_vol_accel` | Idem | Modelo perde signal de aceleração de vol |

**Total: 25 features calculadas no batch mas NÃO calculadas no live.**

---

## 8. FEATURES CALCULADAS NO LIVE MAS NÃO NO BATCH

### 8.1 Lista

| Feature | Onde é calculada no Live | Por que não está no batch? |
|---------|--------------------------|---------------------------|
| `feature_hits` (tracking) | `learning.py` | Métrica de aprendizado, não feature |
| `acuracia` (por feature) | `learning.py` | Métrica de aprendizado, não feature |
| `pesos` (por regime) | `learning.py` | Métrica de aprendizado, não feature |
| `sinal_confirmado` | `position_manager.py` | Estado do sistema, não feature |
| `confianca_ewma` | `position_manager.py` | Estado do sistema, não feature |

**Status:** ✅ Correto — estes são estados do sistema, não features de predição.

---

## 9. CHECKLIST DE CONSISTÊNCIA POR CAMADA

### 9.1 Raw → Extração

| Estágio | Status | Problemas |
|---------|--------|-----------|
| RTD COM → TradeEvent | ✅ | Nenhum |
| RTD COM → BookSnapshot | ✅ | Nenhum |
| TradeEvent → FeatureEngine | ✅ | Nenhum |
| BookSnapshot → BookLevelFeatures | ✅ | Nenhum |

### 9.2 Extração → Normalização

| Feature | Normalização Live | Normalização Batch | Consistente? |
|---------|-------------------|-------------------|--------------|
| `aggr_imb` | `(vc-vv)/(vc+vv)` | `(vc-vv)/(vc+vv)` | ✅ |
| `vwap` | `sum(p*q)/sum(q)` (incremental) | `cumsum(p)/cumsum(1)` (aproximação) | ⚠️ Diferente |
| `cvd_total` | Acumulado desde abertura | Acumulado desde abertura | ✅ |
| `vpin` | Bucket volume (500) | Bucket volume (500) | ✅ |
| `kyle_lambda` | Regressão 200ms | Regressão 200ms | ✅ |
| `spread` | `best_ask - best_bid` | `best_ask - best_bid` | ✅ |

### 9.3 Normalização → Armazenamento

| Formato | Status |
|---------|--------|
| JSONL (raw) | ✅ |
| Parquet (histórico) | ✅ |
| Feature Registry (165 features) | ✅ |

### 9.4 Armazenamento → Dataset ML

| Problema | Severidade |
|----------|------------|
| `preco_saida` presente no parquet (vazamento) | 🔴 CRÍTICA |
| `duracao_label_ms` presente no parquet (vazamento) | 🔴 CRÍTICA |
| 25 features do v950 não estão no live | 🟠 ALTA |
| `posicao_range_dia` (batch) vs `posicao_relativa` (live) | 🟡 MÉDIA |
| Cálculo de VWAP diferente (batch vs live) | 🟡 MÉDIA |

### 9.5 Dataset ML → Treinamento

| Problema | Severidade |
|----------|------------|
| Filtro de leakage ativo em `retreinar_lgbm_limpo.py` | ✅ Corrigido |
| Split temporal com purge/embargo | ✅ Correto |
| 25 features ausentes no live | 🟠 ALTA |

### 9.6 Treinamento → Inferência

| Problema | Severidade |
|----------|------------|
| Scorer não recalcula features `regime_*` | 🟠 ALTA |
| Scorer não recalcula features `atr_*` | 🟠 ALTA |
| Scorer não recalcula features `vol_*` (expansao/compressao) | 🟠 ALTA |
| Manifest valida features mas não garante paridade | 🟡 MÉDIA |
| Cross-asset calculado em tempo real (consistente) | ✅ |

### 9.7 Inferência → Dashboard

| Problema | Severidade |
|----------|------------|
| `confianca_ewma` do position_manager vs `confianca` do signal_engine | 🟡 BAIXA |
| Features `regime_*` não aparecem no dashboard | 🟡 BAIXA (já que não existem no live) |
| 18 endpoints funcionando | ✅ |

---

## 10. RECOMENDAÇÕES

### 10.1 Corrigir Vazamentos (Prioridade Máxima)

1. **Remover `preco_saida` e `duracao_label_ms` do parquet** — o filtro está no código de treino mas as colunas ainda existem nos dados.
2. **Criar pipeline de limpeza** que remove essas colunas do parquet gerado.

### 10.2 Sincronizar Features Batch ↔ Live (Prioridade Alta)

As 25 features calculadas no batch mas não no live devem ser implementadas no `ScorerML`:

**Opção A:** Adicionar cálculo no live (recomendado)
- Adicionar métodos ao `ScorerML` para calcular `regime_*`, `atr_*`, `vol_*`
- Manter paridade total com o batch

**Opção B:** Remover do batch (menos recomendado)
- Remover features do v950 que não podem ser calculadas ao vivo
- Reduzir dimensão do modelo mas perder signal

### 10.3 Corrigir Nomenclatura (Prioridade Média)

1. **Padronizar `posicao_range_dia` vs `posicao_relativa`**
   - Escolher um nome canônico e usar em todos os lugares
   - Recomendado: `posicao_range_dia` (mais descritivo)

2. **Unificar cálculo de VWAP**
   - Batch usa `cumsum(preco)/cumsum(1)` (aproximação)
   - Live usa `cumsum(preco*qtd)/cumsum(qtd)` (correto)
   - Ajustar batch para usar a mesma fórmula do live

### 10.4 Melhorar Feature Manifest (Prioridade Média)

1. **Expandir o manifest** para incluir todas as 165 features do registry
2. **Adicionar validação de tipo** (não apenas nome)
3. **Adicionar validação de faixa** (min/max esperado)

### 10.5 Adicionar Monitoramento (Prioridade Baixa)

1. **Log de features faltantes** no scorer (já existe mas pode ser melhorado)
2. **Alerta automático** quando ECE > 0.15 (já existe no signal_engine)
3. **Dashboard de saúde do modelo** (já existe mas pode ser expandido)

---

## 11. APPÊNDICE: FLUXO COMPLETO DE UMA FEATURE (ex: `aggr_imb`)

```
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 1: RAW (ProfitChart RTD)                                │
│  - T&T lines: [DAT, PRE, QUL, AGR, ACP, AVD]                   │
│  - Book lines: [OCP, VOC, ACP, OVD, VOV, AVD]                  │
│  - Thread: adapters/profit_rtd.py → events()                   │
│  - Output: MarketEvent(TRADE/BOOK)                             │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 2: EXTRAÇÃO (Feature Engine)                            │
│  - FeatureEngine.processar_lote(ativo, negs, seg)              │
│  - Deduplicação de eventos                                      │
│  - Vetorização NumPy: v_arr, p_arr, a_arr                      │
│  - Cálculo: aggr_imb = (vc-vv)/(vc+vv)                         │
│  - Output: dict de features (1s window)                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 3: NORMALIZAÇÃO                                         │
│  - FeatureEngine.processar_lote() já normaliza                 │
│  - aggr_imb ∈ [-1, 1]                                          │
│  - Output: float                                               │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 4: ARMAZENAMENTO                                        │
│  - MarketState.features_por_seg[(ativo, seg)] = features       │
│  - MarketState.historico[ativo].append(features)               │
│  - Persistência: JSONL (raw_negocios_ms_*.jsonl)               │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 5: DATASET ML (Batch)                                   │
│  - ml/batch_processor.py → dataset_100ms_*.jsonl               │
│  - ml/dataset_builder.py → dataset_final.parquet               │
│  - Feature: aggr_imb presente no parquet                       │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 6: TREINAMENTO                                          │
│  - ml/retreinar_lgbm_limpo.py                                   │
│  - LightGBM com 22 features (sem leakage)                      │
│  - aggr_imb é uma das features de entrada                      │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 7: INFERÊNCIA (Live)                                    │
│  - ml/scorer.py → ScorerML                                     │
│  - GeradorJanelas.processar_evento() calcula aggr_imb          │
│  - flatten_snapshot() achata dict → vector                     │
│  - modelo.predict_proba() → probabilidade                      │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 8: DASHBOARD                                            │
│  - /api/features → signal_engine.get_features()                │
│  - aggr_imb presente no JSON                                   │
│  - Dashboard HTML lê e exibe                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 12. RESUMO DOS PROBLEMAS POR SEVERIDADE

### 🔴 CRÍTICO (deve corrigir antes de colocar em produção)

| # | Problema | Arquivo | Solução |
|---|----------|---------|---------|
| 1 | `preco_saida` no parquet (vazamento) | `dataset_final.parquet` | Limpar colunas do parquet |
| 2 | `duracao_label_ms` no parquet (vazamento) | `dataset_final.parquet` | Limpar colunas do parquet |

### 🟠 ALTO (deve corrigir em breve)

| # | Problema | Arquivo | Solução |
|---|----------|---------|---------|
| 3 | 25 features do batch não calculadas no live | `ml/scorer.py` | Adicionar cálculo no live |
| 4 | Cálculo de VWAP diferente (batch vs live) | `ml/features_contexto_avancado.py` | Unificar fórmula |

### 🟡 MÉDIO (melhorar quando possível)

| # | Problema | Arquivo | Solução |
|---|----------|---------|---------|
| 5 | `posicao_range_dia` vs `posicao_relativa` | Múltiplos | Padronizar nome |
| 6 | Timestamp conversion drift | `adapters/profit_rtd.py` | Usar timestamp do sistema |
| 7 | Feature manifest incompleto | `ml/feature_manifest.py` | Expandir para 165 features |

### 🟢 BAIXO (nice-to-have)

| # | Problema | Arquivo | Solução |
|---|----------|---------|---------|
| 8 | `confianca_ewma` vs `confianca` | `core/position_manager.py` | Unificar confidence |
| 9 | Dashboard não mostra features `regime_*` | `adapters/dashboard/handlers.py` | Adicionar endpoints |
