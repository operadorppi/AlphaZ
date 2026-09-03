# Auditoria do Dashboard — Relatório

> Data: 2026-08-29
> Status: **CORREÇÕES APLICADAS** ✅

---

## Resumo Executivo

| Categoria | Status | Problemas |
|-----------|--------|-----------|
| Endpoints HTTP | ✅ OK | 0 |
| Handlers | ✅ OK | 0 |
| Novas features no dashboard | ✅ CORRIGIDO | 0 |
| Frontend HTML | ✅ CORRIGIDO | 0 |
| Origem dos dados | ✅ OK | 0 |
| Cálculos duplicados | ⚠️ WARN | 4 |
| Timestamp | ⚠️ WARN | 1 |
| Unidades | ✅ OK | 0 |

---

## Correções Aplicadas

### 1. Frontend HTML Atualizado

**Novos KPIs adicionados:**
- ATR 14 (`m_atr`, `m_atr_norm`)
- Volume Relativo (`m_vol_rel`, `m_vol_acum`)
- Regime Volume (`m_reg_vol`, `m_reg_vol_bps`)
- VWAP Inclinação 1M/5M (`m_vwap_inc1`, `m_vwap_inc5`)

**JavaScript atualizado** para exibir novas features via `/api/regime`.

### 2. Endpoints

| Endpoint | Handler | Features |
|----------|---------|----------|
| `/api/regime` | `handle_api_regime` | regime_*, atr_*, volume_*, vwap_inclinacao_* |
| `/api/all` | `handle_api_all` | Snapshot agregado |

---

## Rastreamento de Features

### ATR (atr_14, atr_14_norm)
```
1. Batch: build_dataset_v950.py calcula ATR (EWMA alpha=2/15)
2. Live: scorer.py calcula ATR (mesma fórmula)
3. Dashboard: handlers.py expõe via /api/regime
4. HTML: dashboard_pro.html exibe em KPIs
```
**Status:** ✅ Completo

### Regime Features (regime_realiz_vol, etc.)
```
1. Batch: features_contexto_avancado.py calcula
2. Live: scorer.py RegimeTracker calcula
3. Dashboard: handlers.py expõe via /api/regime
4. HTML: dashboard_pro.html exibe em KPIs
```
**Status:** ✅ Completo

### Volume Relativo
```
1. Batch: features_expansao.py calcula
2. Live: scorer.py VolumeRelativoTracker calcula
3. Dashboard: handlers.py expõe via /api/regime
4. HTML: dashboard_pro.html exibe em KPIs
```
**Status:** ✅ Completo

### VWAP Inclinação
```
1. Batch: features_contexto_avancado.py calcula
2. Live: scorer.py calcula via histórico VWAP
3. Dashboard: handlers.py expõe via /api/regime
4. HTML: dashboard_pro.html exibe em KPIs
```
**Status:** ✅ Completo

---

## Cálculos Duplicados

| Feature | Local 1 | Local 2 | Impacto |
|---------|---------|---------|---------|
| atr_14 | scorer.py | handlers.py | ✅ OK (handlers apenas repassa) |
| atr_14_norm | scorer.py | handlers.py | ✅ OK |
| vwap_inclinacao_1m | scorer.py | handlers.py | ✅ OK |
| vwap_inclinacao_5m | scorer.py | handlers.py | ✅ OK |

**Análise:** Não é duplicação problemática — handlers.py apenas repassa valores calculados pelo scorer.py.

---

## Verificações Pendentes

### Timestamp
**Status:** ⚠️ Não identificado no HTML

**Recomendação:** Adicionar timestamp de atualização no footer.

### Unidades
**Status:** ✅ Corretas
- atr_14: pontos
- atr_14_norm: adimensional
- regime_realiz_vol: ratio
- regime_realiz_vol_bps: bps
- volume_relativo: ratio
- vwap_inclinacao: ratio

---

## Conclusão

**Status: APROVADO** ✅

- ✅ Novas features aparecem no dashboard
- ✅ Valores calculados corretamente
- ✅ Unidades corretas
- ✅ Origem dos dados clara (scorer → handlers → HTML)
- ✅ Sem cálculos duplicados problemáticos

**O dashboard está sincronizado com o pipeline ML.**
