# Rastreamento de Features — v9.40

## Fluxo Completo: Geração → Dataset → ML → Scorer → Dashboard

### 1. CONTEXTO DIÁRIO (PrecoContextTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| abertura | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ⚠️ API |
| fechamento_anterior | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ⚠️ API |
| ajuste_anterior | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ⚠️ API |
| maxima_dia | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ⚠️ API |
| minima_dia | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ⚠️ API |
| dist_abertura_pts | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ❌ |
| dist_ajuste_pts | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ❌ |
| posicao_range_dia | preco_context_tracker.py:36 | features_contexto_preco.py:122 | ✅ v940 | ❌ |

### 2. VWAP DIÁRIA (VWAPTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| vwap | scorer.py:27 (VWAPTracker) | calcular_vwap_diaria.py | ⚠️ v941 | ⚠️ API |
| dist_vwap_pts | scorer.py:27 | calcular_vwap_diaria.py | ⚠️ v941 | ❌ |
| acima_vwap | scorer.py:27 | calcular_vwap_diaria.py | ⚠️ v941 | ❌ |
| cruzou_vwap | scorer.py:27 | calcular_vwap_diaria.py | ⚠️ v941 | ❌ |

### 3. VOLATILIDADE (VolatilityTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| vol_100ms | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_500ms | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_1s | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_5s | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_15s | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_1min | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| vol_5min | volatility_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |

### 4. RETORNOS (ReturnsTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| retorno_100ms | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_500ms | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_1s | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_5s | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_15s | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_1min | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |
| retorno_5min | returns_tracker.py:4 | features_expansao.py:27 | ✅ v940 | ❌ |

### 5. TEMPO DE SESSÃO (SessionTimeTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| segundos_desde_abertura | session_time_tracker.py:7 | features_expansao.py:27 | ✅ v940 (**#1**) | ❌ |
| minutos_desde_abertura | session_time_tracker.py:7 | features_expansao.py:27 | ✅ v940 | ❌ |
| minutos_ate_fechamento | session_time_tracker.py:7 | features_expansao.py:27 | ✅ v940 (**#4**) | ❌ |
| sin_horario | session_time_tracker.py:7 | features_expansao.py:27 | ✅ v940 (**#6**) | ❌ |
| cos_horario | session_time_tracker.py:7 | features_expansao.py:27 | ✅ v940 (**#3**) | ❌ |
| bloco_sessao | session_time_tracker.py:7 | features_expansao.py:27 | ⚠️ v941 | ❌ |

### 6. VOLUME PROFILE / POC

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| vp_vp_total | features_lib.py:841 | features_lib.py:841 | ✅ v940 (**#5**) | ⚠️ API |
| vp_poc_dist | features_lib.py:841 | features_lib.py:841 | ✅ v940 | ⚠️ API |
| vp_vah_dist | features_lib.py:841 | features_lib.py:841 | ✅ v940 | ⚠️ API |
| vp_val_dist | features_lib.py:841 | features_lib.py:841 | ✅ v940 | ⚠️ API |
| vp_poc_acima | features_lib.py:841 | features_lib.py:841 | ✅ v940 | ❌ |

### 7. MIGRAÇÃO POC (PocMigrationTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| poc_delta | poc_migration_tracker.py | features_expansao.py | ✅ v940 | ❌ |
| poc_velocity | poc_migration_tracker.py | features_expansao.py | ✅ v940 | ❌ |
| poc_direction | poc_migration_tracker.py | features_expansao.py | ✅ v940 | ❌ |

### 8. VOLUME RELATIVO (VolumeRelativoTracker)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| volume_acumulado_dia | volume_relativo_tracker.py | features_expansao.py | ✅ v940 (**#2**) | ❌ |
| volume_por_minuto | volume_relativo_tracker.py | features_expansao.py | ✅ v940 | ❌ |
| volume_relativo | volume_relativo_tracker.py | features_expansao.py | ✅ v940 | ❌ |

### 9. RANGE E EXPECTATIVA

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| range_dia | motor_rt_alphaz.py (RangeTracker) | features_expansao.py | ✅ v940 | ❌ |
| range_dia_norm | motor_rt_alphaz.py | features_expansao.py | ✅ v940 | ❌ |
| dist_maxima_dia_norm | motor_rt_alphaz.py | features_expansao.py | ✅ v940 (**#8**) | ❌ |
| dist_minima_dia_norm | motor_rt_alphaz.py | features_expansao.py | ✅ v940 | ❌ |

### 10. NÍVEIS D-1

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| maxima_anterior | preco_context_tracker.py | features_contexto_preco.py | ✅ v940 | ❌ |
| minima_anterior | preco_context_tracker.py | features_contexto_preco.py | ✅ v940 | ❌ |
| dist_maxima_anterior_pts | preco_context_tracker.py | features_contexto_preco.py | ✅ v940 | ❌ |
| dist_minima_anterior_pts | preco_context_tracker.py | features_contexto_preco.py | ✅ v940 | ❌ |

### 11. NÍVEIS SEMANAIS

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| maxima_semana_anterior | ❌ NÃO IMPLEMENTADO | features_expansao.py | ✅ v940 | ❌ |
| minima_semana_anterior | ❌ NÃO IMPLEMENTADO | features_expansao.py | ✅ v940 | ❌ |
| fechamento_semana_anterior | ❌ NÃO IMPLEMENTADO | features_expansao.py | ✅ v940 | ❌ |

### 12-13. WIN×WDO / LIDERANÇA

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| lag_ms | motor_rt_alphaz.py (CrossAssetEngine) | ❌ BATCH | ❌ | ⚠️ API |
| corr_aggr | motor_rt_alphaz.py | ❌ BATCH | ❌ | ⚠️ API |
| divergencia | motor_rt_alphaz.py | ❌ BATCH | ❌ | ⚠️ API |
| wdo_leading | motor_rt_alphaz.py | ❌ BATCH | ❌ | ❌ |

### 14-16. COMPOSTOS (Micro × Contexto)

| Feature | Geração (live) | Batch (dataset) | ML | Dashboard |
|---------|---------------|-----------------|-----|-----------|
| aggr_imb_x_dist_ajuste_norm | ❌ LIVE | features_contexto_avancado.py | ✅ v940 | ❌ |
| aggr_imb_x_dist_maxima_dia_norm | ❌ LIVE | features_contexto_avancado.py | ✅ v940 | ❌ |
| aggr_imb_x_posicao_range_dia | ❌ LIVE | features_contexto_avancado.py | ✅ v940 | ❌ |
| cvd_norm_x_acima_abertura | ❌ LIVE | features_contexto_avancado.py | ✅ v940 | ❌ |
| cvd_norm_x_acima_ajuste | ❌ LIVE | features_contexto_avancado.py | ✅ v940 | ❌ |

## Resumo de Status

| Camada | Cobertura | Gap Principal |
|--------|-----------|---------------|
| Geração live | ~60% | Compostos (14-16) e WIN×WDO (12-13) não injetados no scorer |
| Dataset batch | ~80% | VWAP e WIN×WDO não estão no parquet |
| ML (modelo) | **~90%** | ✅ 105 de 121 features numericas no modelo |
| Dashboard | ~10% | Quase nada mostra as features de contexto |

## Pendências

| # | Item | Prioridade | Esforço |
|---|------|-----------|---------|
| 1 | Dashboard mostrar features de contexto | ALTO | 2-3h |
| 2 | VWAP no dataset (bug calcular_vwap_diaria.py) | A
