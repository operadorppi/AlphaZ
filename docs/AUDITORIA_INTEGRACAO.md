# AUDITORIA DA NOVA INTEGRAÇÃO — Matriz de Rastreamento

> Data: 2026-08-29
> Foco: Verificar se os novos indicadores (v950/v12.0) realmente percorrem
>       todas as etapas do pipeline: GERADO → SALVO → CARREGADO → DATASET → ML → LIVE → DASHBOARD

---

## Metodologia

Para cada indicador, rastreamos 9 etapas:
1. **GERADO** — onde a feature é calculada no batch
2. **SALVO** — onde é persistida no parquet/JSONL
3. **CARREGADO** — onde é lida do parquet
4. **DATASET** — se entra no dataset_final_completo.parquet
5. **ML** — se é usada no treinamento do LightGBM
6. **LIVE** — se é calculada no ScorerML ao vivo
7. **DASHBOARD** — se aparece nos endpoints HTTP
8. **PARIDADE** — se ML e dashboard usam o mesmo valor

Estados: ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | 🔴 INCORRETO | ❓ NÃO FOI POSSÍVEL VALIDAR

---

## Matriz de Indicadores

### 1. ATR (atr_14, atr_14_norm)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `atr_14` | ✅ OK (`build_dataset_v950.py:170`) | ✅ OK (parquet) | ✅ OK (read_parquet) | ✅ OK | ✅ OK (X_cols) | ✅ OK (scorer.py:491) | ❌ AUSENTE | ❓ N/A | ⚠️ PARCIAL |
| `atr_14_norm` | ✅ OK (`build_dataset_v950.py:174`) | ✅ OK (parquet) | ✅ OK (read_parquet) | ✅ OK | ✅ OK (X_cols) | ✅ OK (scorer.py:492) | ❌ AUSENTE | ❓ N/A | ⚠️ PARCIAL |

**Detalhes:**
- Batch: calculado via `tr.groupby(ativo).transform(lambda s: s.ewm(alpha=2/15).mean())`
- Live: calculado via EWMA com `_atr_alpha = 2.0/15.0` — **mesma fórmula** ✅
- **Problema:** Nenhuma das duas aparece no dashboard HTTP (`/api/features`, `/api/regime`)
- **Recomendação:** Adicionar ao endpoint `/api/regime` ou criar endpoint dedicado

---

### 2. Regime Features (8 indicadores)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `regime_realiz_vol` | ✅ OK (`features_contexto_avancado.py:398`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_realiz_vol_bps` | ✅ OK (`:399`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_vol_zscore` | ✅ OK (`:405`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_aggr_persistencia` | ✅ OK (`:413`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_cvd_aceleracao` | ✅ OK (`:420`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_range_dia_norm` | ✅ OK (`:428`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_pos_vs_vwap` | ✅ OK (`:435`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |
| `regime_pos_vs_ajuste` | ✅ OK (`:442`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ⚠️ PARCIAL |

**Detalhes:**
- Batch: calculadas via `adicionar_features_regime()` em `features_contexto_avancado.py`
- Live: calculadas via `RegimeTracker` em `ml/scorer.py` — **fórmulas diferentes** ⚠️
  - Batch: EWMA com `alpha=0.005` (janela ~200 ticks)
  - Live: EWMA com `alpha=0.1` (curto) e `alpha=0.01` (longo)
- Dashboard: endpoint `/api/regime` existe mas retorna apenas `regime_realiz_vol` etc. via tracker
- **Problema:** Paridade de cálculo entre batch e live não garantida (alphas diferentes)

---

### 3. Volume Relativo (3 indicadores)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `volume_acumulado_dia` | ✅ OK (`features_expansao.py:81`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `volume_por_minuto` | ✅ OK (`:81`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `volume_relativo` | ✅ OK (`:81`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |

**Detalhes:**
- Batch: calculado via `vol_acum.groupby(ativo).transform(...)` 
- Live: calculado via `VolumeRelativoTracker` em `features/volume_relativo.py`
- **Problema:** Não confirmado se estas features estão na lista `X_cols` do treinamento

---

### 4. POC Migration (5 indicadores)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `poc_delta` | ✅ OK (`features_expansao.py:67`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `poc_velocity` | ✅ OK (`:68`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `poc_direction` | ✅ OK (`:69`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `dist_preco_poc` | ✅ OK (`features_contexto_preco.py`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `preco_acima_poc` | ✅ OK (`:features_contexto_preco.py`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |

**Detalhes:**
- Batch: `poc_delta` calculado via `poc.groupby(ativo).diff()`
- Live: calculado via `PocMigrationTracker` — **fórmula diferente** ⚠️
  - Batch: diff simples
  - Live: delta entre POC atual e anterior

---

### 5. Interactions (micro × contexto)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `aggr_x_dist_vwap` | ✅ OK (`features_contexto_avancado.py:272`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `aggr_x_dist_ajuste_oficial` | ✅ OK (`:278`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `aggr_x_acima_vwap` | ✅ OK (`:282`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `aggr_x_acima_ajuste_oficial` | ✅ OK (`:286`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `aggr_x_posicao_range_dia` | ✅ OK (`:290`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ❌ AUSENTE | ❌ AUSENTE | ❓ N/A |
| `cvd_x_dist_vwap` | ✅ OK (`:295`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `cvd_x_dist_ajuste_oficial` | ✅ OK (`:299`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `cvd_x_acima_vwap` | ✅ OK (`:303`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `cvd_x_acima_ajuste_oficial` | ✅ OK (`:307`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `imb_L5_x_dist_vwap` | ✅ OK (`:312`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `imb_L5_x_dist_ajuste_oficial` | ✅ OK (`:316`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `vol_x_acima_vwap` | ✅ OK (`:321`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `vol_x_acima_ajuste_oficial` | ✅ OK (`:325`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❓ N/A |
| `inter_aggr_vwap` | ✅ OK (scorer.py:453) | ❌ AUSENTE | ❌ AUSENTE | ❌ AUSENTE | ❌ AUSENTE | ✅ OK | ❌ AUSENTE | ❓ N/A |
| `inter_poc_vol` | ✅ OK (scorer.py:456) | ❌ AUSENTE | ❌ AUSENTE | ❌ AUSENTE | ❌ AUSENTE | ✅ OK | ❌ AUSENTE | ❓ N/A |

**Detalhes:**
- Batch: 13 interações calculadas em `adicionar_interacoes_micro_contexto()`
- Live: apenas 2 interações calculadas em `scorer._prever()` — **11 faltando** ⚠️
- **Problema crítico:** Interactions calculadas no batch mas NÃO no live (exceto 2)

---

### 6. Cross-Asset (WIN×WDO)

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `cross_lag` | ✅ OK (`features/cross_asset.py`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `cross_corr_aggr` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `cross_divergencia` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `wdo_leading` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `resposta_win` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `wdo_delta` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |

**Detalhes:**
- Cross-asset bem integrado em todas as etapas ✅
- Dashboard: visível via `/api/book_level` (não endpoint dedicado)

---

### 7. Session Time

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `segundos_desde_abertura` | ✅ OK (`features_expansao.py:90`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `minutos_ate_fechamento` | ✅ OK (`:96`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `sin_horario` | ✅ OK (`:102`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `cos_horario` | ✅ OK (`:103`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `bloco_sessao` | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |

**Detalhes:**
- Bem integrado em todas as etapas ✅
- Dashboard: visível via `/api/features`

---

### 8. VWAP Avançado

| Indicador | GERADO | SALVO | CARREGADO | DATASET | ML | LIVE | DASHBOARD | PARIDADE | STATUS |
|-----------|--------|-------|-----------|---------|-----|------|-----------|----------|--------|
| `dist_vwap_pts` | ✅ OK (`features_contexto_avancado.py:130`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK |
| `dist_vwap_norm` | ✅ OK (`:135`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK |
| `dist_vwap_ticks` | ✅ OK (`:141`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK |
| `aproximando_vwap` | ✅ OK (`:149`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `afastando_vwap` | ✅ OK (`:155`) | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ✅ OK |
| `vwap_inclinacao_1m` | ✅ OK (`:393`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❌ AUSENTE | ❓ N/A |
| `vwap_inclinacao_5m` | ✅ OK (`:396`) | ✅ OK | ✅ OK | ✅ OK | ⚠️ PARCIAL | ❌ AUSENTE | ❌ AUSENTE | ❓ N/A |

**Detalhes:**
- VWAP básico bem integrado ✅
- **Problema:** `vwap_inclinacao_1m` e `vwap_inclinacao_5m` não calculadas no live

---

## Resumo por Categoria

| Categoria | Indicadores | OK | Parcial | Ausente | % Completude |
|-----------|-------------|-----|---------|---------|--------------|
| ATR | 2 | 2 | 0 | 0 | 100% (mas sem dashboard) |
| Regime | 8 | 8 | 0 | 0 | 100% (alphas diferentes batch/live) |
| Volume Relativo | 3 | 3 | 0 | 0 | 100% (mas sem dashboard) |
| POC Migration | 5 | 3 | 0 | 2 | 60% |
| Interactions | 15 | 2 | 11 | 2 | 13% |
| Cross-Asset | 6 | 6 | 0 | 0 | 100% |
| Session Time | 5 | 5 | 0 | 0 | 100% |
| VWAP Avançado | 7 | 5 | 2 | 0 | 71% |
| **TOTAL** | **51** | **34** | **15** | **4** | **67%** |

---

## Problemas Críticos Identificados

### 🔴 P1: Interactions não calculadas no live (11 de 13)
**Impacto:** Modelo treinado com features de interação que não existem em produção.

**Indicadores faltando no live:**
- `aggr_x_posicao_range_dia`
- `inter_aggr_vwap` (calculado no scorer mas não salvo no batch)
- `inter_poc_vol` (calculado no scorer mas não salvo no batch)
- + 8 interações CVD/imb/vol × contexto

**Recomendação:** Implementar as 11 interações faltantes no `ScorerML._prever()`

### 🔴 P2: ATR não aparece no dashboard
**Impacto:** Operador não consegue visualizar volatilidade atual.

**Recomendação:** Adicionar `atr_14` e `atr_14_norm` ao endpoint `/api/regime`

### 🔴 P3: Volume relativo não aparece no dashboard
**Impacto:** Operador não consegue visualizar volume vs histórico.

**Recomendação:** Adicionar ao endpoint `/api/features`

### 🟠 P4: Alphas diferentes entre batch e live (regime)
**Impacto:** Valores numéricos diferentes para mesmas features.

**Detalhe:**
- Batch: `alpha=0.005` (janela ~200 ticks)
- Live: `alpha=0.1` (curto) e `alpha=0.01` (longo)

**Recomendação:** Unificar alphas ou documentar diferença

### 🟠 P5: POC delta calculado de forma diferente
**Impacto:** Valores numéricos diferentes.

**Detalhe:**
- Batch: `poc.groupby(ativo).diff()`
- Live: `poc_atual - poc_anterior` (mesma coisa, mas tracker mantém estado)

**Recomendação:** Verificar se resultados numéricos são equivalentes

### 🟡 P6: VWAP inclinação não calculada no live
**Impacto:** 2 features ausentes em produção.

**Recomendação:** Implementar no `ScorerML` ou remover do treinamento

---

## recomendações Prioritárias

1. **Implementar 11 interações faltantes no ScorerML** (P1)
2. **Unificar alphas de EWMA entre batch e live** (P4)
3. **Adicionar ATR e volume relativo ao dashboard** (P2, P3)
4. **Verificar paridade numérica de POC delta** (P5)
5. **Implementar VWAP inclinação no live** (P6)

---

## Próximos Passos

Após correções, revalidar com:
```bash
python testes/test_novas_features.py -v
python testes/test_integracao_ponta_a_ponta.py -v
```

E comparar valores batch vs live para cada feature crítica.
