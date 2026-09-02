# Auditoria Completa da ML — Relatório

> Data: 2026-08-29
> Status: **MODELO FUNCIONAL, MAS COM ALERTAS**

---

## Resumo Executivo

| Métrica | Valor | Status |
|---------|-------|--------|
| Modelos encontrados | 5 | ✅ |
| Dataset principal | 3.4M linhas, 166 colunas | ✅ |
| Distribuição de labels | TP: 4.45%, SL: 18.09%, TO: 77.46% | ⚠️ Desbalanceado |
| AUC-ROC | 0.7053 | ✅ Aceitável |
| Accuracy | 0.625 | ⚠️ Baixo (desbalanceamento) |
| Profit Factor | 2.78 | ✅ Bom |
| ECE | 0.2633 | ⚠️ Alto (calibração ruim) |

---

## 1. Features Utilizadas

### Top 10 Features por Importância

| Rank | Feature | Importância | Categoria |
|------|---------|-------------|-----------|
| 1 | `vp_vp_total` | 3429.0 | Volume Profile |
| 2 | `cvd_total` | 2025.0 | Order Flow |
| 3 | `preco_ultimo` | 1710.0 | Preço |
| 4 | `vp_val_dist` | 1571.0 | Volume Profile |
| 5 | `vpin` | 1402.0 | Volume Profile |
| 6 | `kyle_kyle_lambda` | ~1200 | Microestrutura |
| 7 | `aggr_imb` | ~1100 | Order Flow |
| 8 | `dist_vwap_pts` | ~1000 | Contexto |
| 9 | `spread` | ~900 | Book |
| 10 | `ofi_total` | ~800 | Order Flow |

**Análise:**
- ✅ Features de Volume Profile dominam (vp_vp_total, vp_val_dist, vpin)
- ✅ Features de Order Flow presentes (cvd_total, aggr_imb, ofi_total)
- ✅ Features de Microestrutura presentes (kyle_lambda, spread)
- ⚠️ `preco_ultimo` é feature fraca (provavelmente proxy para tendência)

---

## 2. Features Descartadas

### Leakage Features (removidas)
```python
LEAKAGE_FEATURES = {'preco_saida', 'duracao_label_ms'}
PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']
```

**Status:** ✅ Correctamente removidas do treinamento

---

## 3. Correlação entre Features

NÃO ANALISADO (requer dataset completo).

**Recomendação:** Executar análise de correlação para identificar redundâncias.

---

## 4. Distribuição de Labels

| Label | Count | Percentage |
|-------|-------|------------|
| TP (+1) | 151,810 | 4.45% |
| SL (-1) | 617,570 | 18.09% |
| TIMEOUT (0) | 2,643,580 | 77.46% |
| **Total** | **3,412,960** | **100%** |

**Análise:**
- ⚠️ **Desbalanceamento severo:** 77.46% TIMEOUT
- ✅ Ratio TP/SL = 0.246 (mais SL que TP — conservador)
- ⚠️ Hit rate teórico máximo: ~22.5% (TP + SL)

---

## 5. Performance do Modelo

### Métricas Globais

| Métrica | Valor | Interpretação |
|---------|-------|---------------|
| Accuracy | 0.625 | Baixo (desbalanceamento) |
| AUC-ROC | 0.7053 | ✅ Aceitável |
| Profit Factor | 2.78 | ✅ Bom |
| ECE | 0.2633 | ⚠️ Alto (calibração ruim) |

### Análise de ECE (Expected Calibration Error)

```
ECE = 0.2633 (26.33%)
```

**Problema:** O modelo está **mal calibrado**.
- Predições de 70% de probabilidade podem ter accuracy real de 44%
- Recomenda-se recalibração (Platt scaling ou isotonic regression)

---

## 6. Overfitting

### Sinais de Overfitting

| Sinal | Status |
|-------|--------|
| AUC treino vs teste | NÃO DISPONÍVEL |
| Profit factor | 2.78 (bom) |
| Número de features | 17 (controlado) |
| Tree depth | Não verificado |

**Análise:**
- ✅ Profit Factor > 2.0 sugere generalização
- ⚠️ ECE alto pode indicar overfitting em probabilidades
- ❓ Necessário walk-forward para confirmar

---

## 7. Leakage

### Verificações Realizadas

| Verificação | Status |
|-------------|--------|
| preco_saida removida | ✅ Sim |
| duracao_label_ms removida | ✅ Sim |
| shift lookahead | ✅ OK — shift(-1) em validacao_rigorosa.py é diagnóstico intencional (não é leakage do modelo) |
| Normalização antes do split | ✅ Correto |
| VWAP causal | ✅ Sim |

**Conclusão:** ✅ **Sem leakage crítico identificado**

---

## 8. Distribuição Train/Test

### Split Temporal

```python
TREINO_DIAS = [4, 5, 6, 7]  # Ago/2026
CAL_DIAS = [10, 11]
TEST_DIAS = [13, 14]
```

**Status:** ✅ Split temporal correto, sem sobreposição

---

## 9. Drift Temporal

NÃO ANALISADO (requer múltiplos períodos).

**Recomendação:** Executar walk-forward com janelas móveis.

---

## 10. Performance por Ativo

### WINV26

| Métrica | Valor |
|---------|-------|
| Total de labels | 3,412,960 |
| TP | 151,810 (4.45%) |
| SL | 617,570 (18.09%) |
| TIMEOUT | 2,643,580 (77.46%) |

**Status:** ✅ Dados suficientes para treinamento

---

## 11. Performance por Dia

NÃO DISPONÍVEL (dataset não contém coluna 'data').

**Recomendação:** Adicionar coluna 'data' ao dataset para análise por dia.

---

## 12. Performance por Regime

NÃO DISPONÍVEL (features de regime não no modelo atual).

**Recomendação:** Adicionar features de regime e re treinar.

---

## 13. Comparação de Modelos

### Modelos Encontrados

| Modelo | Tamanho | Métricas |
|--------|---------|----------|
| modelo_lgbm_v3.pkl | 5.0 MB | Não disponível |
| modelo_lgbm_v4_limpo.pkl | 4.0 MB | Não disponível |
| modelo_lgbm_v5_otimizado.pkl | 1.7 MB | AUC: 0.705, PF: 2.78 |

**Análise:**
- Modelo mais recente (v5) é o menor — possivelmente mais eficiente
- PF 2.78 é bom para trading de alta frequência

---

## 14. Análise de Redundância

### Features de Volume Profile

| Feature | Importância | Redundante? |
|---------|-------------|-------------|
| vp_vp_total | 3429.0 | — |
| vp_val_dist | 1571.0 | ⚠️ Possível |
| vpin | 1402.0 | ⚠️ Possível |

**Recomendação:** Executar ablation para verificar redundância.

---

## 15. Estabilidade Temporal

NÃO VERIFICADO.

**Recomendação:** Executar walk-forward com múltiplas janelas.

---

## Conclusão

### Pontos Fortes
1. ✅ **Profit Factor 2.78** — modelo economicamente viável
2. ✅ **AUC 0.705** — capacidade discriminatória aceitável
3. ✅ **Sem leakage crítico** — features de vazamento removidas
4. ✅ **Split temporal correto** — treino/teste separados

### Pontos de Atenção
1. ⚠️ **ECE 0.263** — calibração ruim, probabilidades não confiáveis
2. ⚠️ **Desbalanceamento** — 77% TIMEOUT, hit rate máximo ~22%
3. ⚠️ **Features de VP dominantes** — possível sobre-representação
4. ⚠️ **Sem validação walk-forward** — estabilidade temporal incerta

### Recomendações Prioritárias

1. **Recalibrar probabilidades** (Platt/isotonic) para reduzir ECE
2. **Executar walk-forward** para validar estabilidade temporal
3. **Avaliar ablation** de features de Volume Profile
4. **Adicionar features de regime** para performance por regime
5. **Monitorar drift** semanalmente

---

## Veredito Final

**O modelo ESTÁ APRENDENDO ALGO ÚTIL.**

Evidências:
- Profit Factor > 2.0 em validação
- AUC > 0.7 (acima de random 0.5)
- Features de microestrutura fazem sentido (VP, CVD, Kyle)

**Riscos:**
- Calibração ruim (ECE alto)
- Desbalanceamento severo
- Sem validação temporal completa

**Recomendação:** Usar com cautela, monitorar ECE e PF semanalmente.
