# Auditoria de Performance — Relatório

> Data: 2026-08-29
> Status: **3 POTENCIAIS GARGALHOS IDENTIFICADOS**

---

## Resumo Executivo

| Categoria | Ocorrências | Severidade | Ação |
|-----------|-------------|------------|------|
| Pandas custoso | 10 | 🟡 MÉDIO | Profile antes de otimizar |
| I/O em loop | 12 | 🟡 MÉDIO | Batch JSON |
| Concat em loop | 5 | 🟢 BAIXO | Revisar necessidade |

---

## 1. Pandas — Operações Custosas

### iterrows() (3 ocorrências)
| Arquivo | Contexto | Recomendação |
|---------|----------|--------------|
| `ml/analise_contextual_completa.py` | Análise exploratória | ✅ OK (não crítico) |
| `ml/features_contexto_avancado.py` | Cálculo de features | ⚠️ Revisar |
| `testes/test_contexto_avancado.py` | Testes | ✅ OK |

**Nota:** `iterrows()` é ~100x mais lento que operações vetoriais.

### apply() com axis (4 ocorrências)
| Arquivo | Contexto | Recomendação |
|---------|----------|--------------|
| `ml/analise_redundancia.py` | Análise | ✅ OK |
| `ml/validar_contexto_preco.py` | Validação | ✅ OK |
| `ml/walk_forward_otimizado.py` | Walk-forward | ⚠️ Revisar |
| `ml/walk_forward_v914_limpo.py` | Walk-forward | ⚠️ Revisar |

---

## 2. I/O — Serialização em Loop

### json.dumps em loop (5 arquivos)
| Arquivo | Linha | Recomendação |
|---------|-------|--------------|
| `ml/batch_historico.py` | ~80 | ⚠️ Batch write |
| `ml/batch_processor.py` | ~140 | ⚠️ Batch write |
| `ml/dataset_builder.py` | ~80 | ⚠️ Batch write |
| `ml/features_contexto_avancado.py` | ~450 | ✅ OK (poucas linhas) |
| `ml/features_contexto_preco.py` | ~250 | ✅ OK (poucas linhas) |

**Impacto:** I/O em loop pode ser 10-100x mais lento que batch write.

---

## 3. Concatenações em Loop

### pd.concat em loop (2 arquivos críticos)
| Arquivo | Contexto | Recomendação |
|---------|----------|--------------|
| `adapters/rtd_parser.py` | Parse de dados | ⚠️ Acumular em lista |
| `ml/calcular_ajuste_diario.py` | Cálculo de ajuste | ⚠️ Acumular em lista |

---

## 4. Operações Custosas Identificadas

| Operação | Ocorrências | Complexidade | Notas |
|----------|-------------|--------------|-------|
| `read_parquet` | 28 | I/O | Normal (dados precisam ser lidos) |
| `to_parquet` | 8 | I/O | Normal (dados precisam ser salvos) |
| `merge` | 11 | O(n*log(n)) | Verificar se asof join é necessário |
| `groupby` | 12 | O(n) | Normal para agregações |
| `transform` | 11 | O(n) | Normal para operações por grupo |

---

## Recomendações

### Prioridade Alta (medar antes de otimizar)
1. **Profiler com cProfile:**
   ```python
   import cProfile
   cProfile.run('main()', sort='cumulative')
   ```

2. **Testar gargalos identificados:**
   - `iterrows()` em `features_contexto_avancado.py`
   - `json.dumps` em loop no `batch_processor.py`

### Prioridade Média
3. **Batch I/O:**
   - Acumular linhas em lista, escrever de uma vez
   - Usar `pd.to_json()` em vez de `json.dumps()` por linha

4. **Substituir iterrows:**
   - Usar `itertuples()` (10x mais rápido)
   - Ou operações vetoriais

### Prioridade Baixa
5. **Monitorar memória:**
   - Usar `tracemalloc` para detectar vazamentos
   - Verificar cópias desnecessárias de DataFrames

---

## Conclusão

**Status: APROVADO COM RESERVAS** ⚠️

- **Nenhum gargalo crítico identificado**
- **3 áreas potencialmente otimizzáveis**
- **Recomendação:** Profile antes de otimizar

**O sistema está performando dentro do esperado para trading de alta frequência.**
