# FASE 14 — P0 · Causalidade End-to-End

## 1. Objetivo

**Não confiar em metadado `causal = True`.** Criar testes onde o futuro é alterado e verificar se features no tempo T permanecem idênticas.

Se features em T mudam quando eventos após T são alterados → **LEAKAGE detectado!**

## 2. Metodologia

```
Dataset A: [eventos_1 ... eventos_50] | [eventos_51 ... eventos_100]
                                    ^-- até T --^   ^-- pós-T --^

Dataset B: [eventos_1 ... eventos_50] | [eventos_diferentes_51 ... eventos_diferentes_100]
                                    ^-- até T --^   ^-- pós-T ALTERADO --^
```

- Até T: **idêntico**
- Após T: **completamente diferente** (outro seed, outros preços, outras corretoras)

Teste: calcular features em T. Se forem iguais → causal ✅. Se diferentes → leakage ❌.

## 3. Features Testadas

| Feature | Status | Observação |
|---|---|---|
| `aggr_imb` | ✅ Causal | Imbalance de agressão |
| `cvd_total` | ✅ Causal | Cumulative Volume Delta |
| `delta_preco_janela` | ✅ Causal | Variação de preço na janela |
| `vol_compra` | ✅ Causal | Volume comprador |
| `vol_venda` | ✅ Causal | Volume vendedor |
| `hhi_compra` | ✅ Causal | HHI concentração compra |
| `hhi_venda` | ✅ Causal | HHI concentração venda |

**Nenhuma feature crítica apresentou leakage.**

## 4. Resultados

```bash
pytest tests/test_causalidade_e2e.py -v
# ============================== 7 passed in 0.60s ===============================
```

## 5. Arquivo de Teste

`tests/test_causalidade_e2e.py`:
- `test_aggr_imb_no_leakage` — teste principal de imbalance
- `test_cvd_total_no_leakage` — teste de CVD
- `test_delta_preco_no_leakage` — teste de variação de preço
- `test_volume_sides_no_leakage` — teste de volumes
- `test_hhi_no_leakage` — teste de HHI
- `test_deterministic_dataset` — verifica determinismo
- `test_divergent_datasets_differ_after_split` — verifica divergência pós-split

## 6. Conclusão

✅ **Causalidade end-to-end confirmada para todas as features críticas testadas.**

O sistema de features em `C:\freebuff\features\` não apresenta vazamento de informação do futuro para o presente nos cálculos analisados.
