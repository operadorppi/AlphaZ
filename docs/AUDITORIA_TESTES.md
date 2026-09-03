# Auditoria de Testes — Relatório

> Data: 2026-08-30
> Status: **15 TESTES FALTANDO IDENTIFICADOS**

---

## Resumo Executivo

| Métrica | Valor |
|---------|-------|
| Total de testes | 229 |
| Arquivos de teste | 21 |
| Testes de leakage | 10 |
| Features testadas | 9/11 (82%) |
| Testes de dashboard | 1 |
| Testes de integração | 5 |
| Testes problemáticos | 1 |
| **Testes faltantes** | **15** |

---

## Cobertura de Edge Cases

| Edge Case | Status |
|-----------|--------|
| timestamp_zero | ✅ Coberto |
| timestamp_negativo | ❌ NÃO coberto |
| preco_zero | ❌ NÃO coberto |
| preco_negativo | ❌ NÃO coberto |
| volume_zero | ✅ Coberto |
| book_vazio | ✅ Coberto |
| fila_saturada | ❌ NÃO coberto |
| memoria_insuficiente | ❌ NÃO coberto |
| network_timeout | ❌ NÃO coberto |
| disk_full | ❌ NÃO coberto |

---

## Features NÃO Testadas

| Feature | Impacto |
|---------|---------|
| `atr_14` | ATR não validado |
| `volume_relativo` | Volume relativo não validado |

---

## Testes Problemáticos

| Arquivo | Problema |
|---------|----------|
| `testes_causalidade_v3.py` | Só tem `pass`, não valida nada |

---

## 15 Testes que DEVEM Existir

### Leakage e Integridade
1. **`test_leakage_preco_saida_no_dataset`** — Verificar se `preco_saida` não está no dataset de treino
2. **`test_leakage_duracao_label_no_dataset`** — Verificar se `duracao_label_ms` não está no dataset de treino
3. **`test_vwap_causal_no_lookahead`** — Verificar se VWAP não usa dados futuros
4. **`test_feature_parity_batch_live`** — Verificar se features são iguais no batch e live

### Consistência
5. **`test_atr_consistente_batch_live`** — Verificar se ATR é igual no batch e live
6. **`test_regime_reset_diario`** — Verificar se regime reseta entre dias
7. **`test_timestamp_timezone`** — Verificar conversão correta de timezone
8. **`test_deduplication_trades`** — Verificar se trades duplicados são removidos

### Sincronização
9. **`test_book_timestamp_sync`** — Verificar se book e T&T têm timestamps sincronizados
10. **`test_dashboard_parity_ml`** — Verificar se dashboard mostra mesmos valores que ML

### Edge Cases
11. **`test_book_split_edge_cases`** — Testar book_split=0, negativo, muito grande
12. **`test_queue_no_loss_on_overflow`** — Verificar se fila não perde dados ao saturar
13. **`test_file_rotation_no_data_loss`** — Verificar se rotação de arquivo não perde dados
14. **`test_contracts_rollover`** — Verificar reset ao mudar de contrato
15. **`test_session_boundary`** — Verificar comportamento na virada de sessão

---

## Recomendações

### Prioridade Alta
1. **Criar 15 testes faltantes** listados acima
2. **Remover teste problemático** (`testes_causalidade_v3.py`)
3. **Adicionar testes para ATR e volume_relativo**

### Prioridade Média
4. **Criar testes de edge cases** (timestamp_negativo, preco_negativo, etc.)
5. **Criar testes de integridade temporal** (timezone, sincronização)
6. **Criar testes de paridade** (batch vs live, ML vs dashboard)

### Prioridade Baixa
7. **Documentar cobertura de testes**
8. **Criar pipeline de CI com execução automática**

---

## Conclusão

**Status: COBERTURA INSUFICIENTE** ⚠️

- **229 testes existentes** — bom volume
- **15 testes faltantes críticos** — precisam ser criados
- **82% de cobertura de features** — 2 features sem teste
- **1 teste problemático** — precisa ser removido

**O sistema precisa de 15 novos testes antes de ir para produção.**
