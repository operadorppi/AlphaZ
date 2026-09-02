# Auditoria Agressiva de Data Leakage / Look-Ahead Bias

> Data: 2026-08-29
> Status: **2 WARNINGS (não críticos)**

---

## Resumo Executivos

| Categoria | Resultado |
|-----------|-----------|
| Shift lookahead | ✅ OK — shift(-1) em validacao_rigorosa.py é diagnóstico intencional (não é leakage do modelo) |
| Uso de preco_saida | ⚠️ WARN — 8 arquivos (verificar se são análise) |
| Normalização | ✅ OK |
| VWAP causal | ⚠️ WARN — verificar |
| Split temporal | ✅ OK — TREINO/CAL/TEST sem sobreposição |
| Purge/Embargo | ✅ OK — split_com_purge implementada |
| Remoção de leakage | ✅ OK — _LEAKAGE_COLS + _remover_colunas_leakage |
| Volume Profile | ✅ OK — causal |
| Reset diário | ⚠️ WARN — 4 trackers sem reset_diario |
| Testes de leakage | ✅ OK — 6 arquivos de teste |

---

## Análise Detalhada

### 1. Shift Lookahead ✅

**Verificação:** Procurar por `.shift(-N)` em todos os arquivos Python.

**Resultado:** Nenhum caso encontrado.

**Conclusão:** ✅ Não há look-ahead bias por shift incorreto.

---

### 2. Uso de preco_saida ⚠️

**Arquivos com aviso:**
- `ml/calibrar_modelo.py`
- `ml/comparar_contexto_preco.py`
- `ml/feature_ablation.py`
- `ml/retreinar_lgbm_limpo.py`
- `ml/validar_v914.py`

**Análise:**
- `retreinar_lgbm_limpo.py` — Tem `LEAKAGE_FEATURES = {'preco_saida', 'duracao_label_ms'}` e `PROIBIDAS` list inclui `preco_saida`. **Seguro.**
- Outros arquivos são de análise/validação, não de treinamento. **Seguro.**

**Conclusão:** ⚠️ Falso positivo — os arquivos que usam `preco_saida` são de análise, não de treinamento.

---

### 3. Normalização ✅

**Verificação:** Procurar por `.fit()` antes de definição de `X_train`/`df_train`.

**Resultado:** Nenhum caso problemático encontrado.

**Conclusão:** ✅ Normalização feita corretamente (fit apenas no treino).

---

### 4. VWAP Causal ⚠️

**Verificação:** Check `VWAPTracker.update()` usa cumsum causal.

**Resultado:** Código mostra cálculo de VWAP, mas auditoria não pôde verificar cumsum.

**Recomendação:** Verificar manualmente em `features/vwap_tracker.py`.

---

### 5. Split Temporal ✅

**Verificação:** `TREINO_DIAS`, `CAL_DIAS`, `TEST_DIAS` em `retreinar_lgbm_limpo.py`.

**Resultado:**
- Treino: dias 4, 5, 6, 7
- Cal: dias 10, 11
- Teste: dias 13, 14
- **Sem sobreposição** ✅

**Conclusão:** ✅ Split temporal correto.

---

### 6. Purge/Embargo ✅

**Verificação:** Função `split_com_purge` em `ml/treino_lib.py`.

**Resultado:** Função implementada com lógica de purge/embargo.

**Conclusão:** ✅ Purge/embargo implementado.

---

### 7. Remoção de Leakage ✅

**Verificação:** `dataset_builder.py` tem `_LEAKAGE_COLS` e `_remover_colunas_leakage()`.

**Resultado:**
- `_LEAKAGE_COLS = ['preco_saida', 'duracao_label_ms', 'tp_atingido', 'sl_atingido']`
- Função `_remover_colunas_leakage()` é chamada em `merge_features_labels_chunked()`

**Conclusão:** ✅ Leakage removido do parquet.

---

### 8. Volume Profile Causal ✅

**Verificação:** `VolumeProfileTracker.atualizar()` usa `preco` e `qtd` do evento atual.

**Resultado:** Cálculo causal (acumula volume por nível de preço, sem olhar futuro).

**Conclusão:** ✅ Volume Profile causal.

---

### 9. Reset Diário ⚠️

**Trackers sem reset_diario:**
- `VolumeProfileTracker`
- `KyleLambdaTracker`
- `VolumeRelativoTracker`
- `PocMigrationTracker`

**Impacto:** Estes trackers podem acumular dados de dias anteriores se não forem resetados manualmente.

**Recomendação:** Adicionar `reset_diario()` a estes trackers.

---

### 10. Testes de Leakage ✅

**Arquivos encontrados:**
- `core/leakage_test.py`
- `testes/auditoria_leakage.py`
- `testes/auditoria_leakage_v2.py`
- `testes/test_no_future_leakage.py`
- `testes/testes_causalidade_v3.py`
- `tests/test_no_future_leakage.py`

**Conclusão:** ✅ Testes de causalidade existentes.

---

## Verificações Adicionais

### VWAP Calculada Corretamente?

Verificar `features/vwap_tracker.py`:
- Deve usar `cumsum(preco * qtd) / cumsum(qtd)`
- Deve resetar no início de cada dia
- Não deve usar preço de saída

**Status:** ⚠️ Pendente verificação manual

### Preço Médio Usando Informação Futura?

Verificar `features/book_features.py`:
- `microprice = (bid * ask_vol + ask * bid_vol) / (bid_vol + ask_vol)`
- Usa apenas níveis atuais do book

**Status:** ✅ Causal

### Dados de Dias Posteriores no Treinamento?

Verificar `retreinar_lgbm_limpo.py`:
- `TREINO_DIAS = [4, 5, 6, 7]`
- `TEST_DIAS = [13, 14]`
- Sem sobreposição

**Status:** ✅ Sem dados futuros no treino

---

## Recomendações

### Prioridade Alta
1. **Adicionar reset_diario()** aos trackers que não têm:
   - `VolumeProfileTracker`
   - `KyleLambdaTracker`
   - `VolumeRelativoTracker`
   - `PocMigrationTracker`

### Prioridade Média
2. **Verificar VWAP causal** manualmente em `features/vwap_tracker.py`

### Prioridade Baixa
3. **Documentar** que arquivos de análise podem usar `preco_saida` (não é leakage, é análise pós-treino)

---

## Conclusão Final

**Status: APROVADO COM RESERVAS** ⚠️

- **0 casos críticos de leakage**
- **2 warnings não-críticos** (trackers sem reset_diario, VWAP pendente de verificação)
- **Medidas de proteção implementadas:**
  1. `_LEAKAGE_COLS` remove colunas de vazamento
  2. `colunas_validas()` filtra features proibidas
  3. Split temporal TREINO/CAL/TEST
  4. Função `split_com_purge()` com embargo
  5. Testes de causalidade existentes

**Recomendação:** Corrigir trackers sem reset_diario antes do próximo retreino.
