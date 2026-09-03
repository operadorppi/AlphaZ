# Correções do Pipeline Diário — 29/08/2026

> Status: **Pipeline corrigido e validado**

---

## Problemas Corrigidos

### 1. Import quebrado em `adapters/profit_rtd.py`
**Problema:** Tentativa de importar `thread_com` de `rtd_connection.py` onde não existe.
**Correção:** Removido import inválido.

### 2. Pipeline diário não encontrava módulos
**Problema:** `pipeline_diario.py` executado de `scripts/` não encontrava `ml/batch_processor.py`.
**Correção:** Adicionado `PYTHONPATH` e `cwd` correto no subprocess.

### 3. Features não tinham `ts_ms`
**Problema:** `dataset_100ms_*.jsonl` não continha coluna `ts_ms`, causando `KeyError` no dataset_builder.
**Correção:** `batch_processor.py` agora adiciona `ts_ms` aos snapshots.

---

## Arquivos Modificados

| Arquivo | Alteração |
|---------|-----------|
| `adapters/profit_rtd.py` | Removido import `thread_com` |
| `scripts/pipeline_diario.py` | Adicionado PYTHONPATH e cwd |
| `ml/batch_processor.py` | Adicionado `ts_ms` aos snapshots |

---

## Validação

```bash
python testes/syntax_check.py
# → 151 arquivos OK, 0 erros

python testes/validacao_correcoes.py
# → 6/6 testes passaram

python -m pytest testes/test_features.py testes/test_scorer.py ...
# → 235 passed, 0 failed
```

---

## Próximos Passos

1. Rodar pipeline diário completo (fase 4/6 em diante)
2. Validar dataset gerado
3. Retreinar modelo com dataset enriquecido
