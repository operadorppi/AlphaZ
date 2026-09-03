# Auditoria de Redundâncias — Relatório

> Data: 2026-08-29
> Status: **24 REDUNDÂNCIAS IDENTIFICADAS**

---

## Resumo por Categoria

| Categoria | Qtd | Severidade |
|-----------|-----|------------|
| Funções duplicadas (interface) | 10 | 🟢 BAIXO |
| Módulos antigos | 8 | 🟠 ALTO |
| Código morto (variáveis) | 128 | 🟡 MÉDIO |
| Scripts obsoletos | 2 | 🟠 ALTO |
| Métricas duplicadas | 3 | 🟡 MÉDIO |

---

## 1. Funções Duplicadas (Interface) — BAIXO

**Status:** ✅ **FALSO POSITIVO**

As funções `connect`, `disconnect`, `events`, `get_health`, `start`, `stop` são **overrides de interface** — implementação necessária em cada classe que herda de `MarketDataSource`.

| Função | Localização | Status |
|--------|-------------|--------|
| `connect` | base.py, profit_rtd.py, replay.py | ✅ Interface |
| `disconnect` | base.py, profit_rtd.py, replay.py | ✅ Interface |
| `events` | base.py, profit_rtd.py, replay.py | ✅ Interface |
| `get_health` | base.py, profit_rtd.py, replay.py | ✅ Interface |
| `start` | com_watchdog.py, dashboard_server.py, capture_daemon.py | ✅ Interface |
| `stop` | com_watchdog.py, dashboard_server.py, capture_daemon.py | ✅ Interface |

**Conclusão:** Não é redundância, é polimorfismo.

---

## 2. Módulos Antigos — ALTO

### motor_web
**Status:** ❌ **REQUER AÇÃO**

Encontrado em 4 arquivos:
- `motor_rt_alphaz_v9_legacy.py` (archive)
- `test_book_writer.py`
- `test_com_watchdog.py`
- `test_tt_warmup.py`

**Ação:** Remover imports ou substituir por `adapters.profit_rtd`.

### motor_rt_alphaz
**Status:** ❌ **REQUER AÇÃO**

Encontrado em:
- `motor_rt_alphaz.py` (arquivo principal?)
- `test_b3_staleness.py`

**Ação:** Verificar se arquivo ainda é necessário.

### features_lib
**Status:** ⚠️ **SHIM DE COMPATIBILIDADE**

Encontrado em:
- `motor_rt_alphaz_v9_legacy.py` (archive)
- `__init__.py`
- `batch_processor.py`

**Ação:** `features_lib` é um shim intencional — manter mas documentar como temporário.

---

## 3. Código Morto — MÉDIO

**128 variáveis potencialmente não usadas** identificadas.

### Exemplos Críticos
| Arquivo | Linha | Variável | Impacto |
|---------|-------|----------|---------|
| `handlers.py` | 37 | `log` | Baixo (logger) |
| `profit_rtd.py` | 188 | `seen` | Médio |
| `profit_rtd.py` | 194 | `tms_tod` | Médio |

**Ação:** Revisar variáveis em `profit_rtd.py` — podem indicar lógica incompleta.

---

## 4. Scripts Obsoletos — ALTO

| Script | Status | Ação |
|--------|--------|------|
| `build_dataset_v940.py` | ❌ Obsoleto | Remover ou mover para `docs/archive/` |
| `build_dataset_v950.py` | ❌ Obsoleto | Remover ou mover para `docs/archive/` |
| `labeler.py` | ✅ Removido | — |
| `retreinar_sem_leak.py` | ✅ Removido | — |

---

## 5. Métricas Duplicadas — MÉDIO

### profit_factor
**Implementado em 12 lugares:**
- `contracts.py`
- `metrics.py`
- `replay_engine.py`
- `treino_lib.py`
- `retreinar_otimizado.py`
- `validacao_rigorosa.py`
- `walk_forward.py`
- ...

**Status:** ⚠️ **RECOMENDADO UNIFICAR**
- Criar função central em `ml/metrics.py`
- Importar nos demais módulos

### auc
**Implementado em 16 lugares:**
- Uso de `roc_auc_score` do sklearn em múltiplos arquivos
- Cálculo manual em alguns testes

**Status:** ✅ **OK** — uso de biblioteca externa é esperado

### ece
**Implementado em 4 lugares:**
- `calibrar_modelo.py`
- `retreinar_lgbm_limpo.py`
- `retreinar_otimizado.py`
- `auditoria_redundancias.py`

**Status:** ⚠️ **RECOMENDADO UNIFICAR**
- Criar função central em `ml/metrics.py`

---

## 6. Pipelines Paralelos — BAIXO

**3 scripts de treinamento encontrados:**
- `retreinar_lgbm_limpo.py` — Principal (sem leakage)
- `retreinar_otimizado.py` — Alternativo
- `treino_lib.py` — Biblioteca de funções

**Status:** ✅ **INTENCIONAL**
- `retreinar_lgbm_limpo.py` é o usado pelo pipeline diário
- Outros são alternativas/históricos

---

## Recomendações

### Prioridade Alta
1. **Remover scripts obsoletos:**
   - `build_dataset_v940.py`
   - `build_dataset_v950.py`
   - (ou mover para `docs/archive/`)

2. **Limpar imports de módulos antigos:**
   - Remover `import motor_web` dos testes
   - Verificar `motor_rt_alphaz.py`

3. **Revisar código morto em `profit_rtd.py`:**
   - Variáveis `seen`, `tms_tod`, `quantity`, `aggressor`, `buyer`

### Prioridade Média
4. **Unificar métricas:**
   - Criar `ml/metrics.py` com `calcular_profit_factor()`, `calcular_ece()`
   - Importar nos demais módulos

5. **Documentar shim `features_lib`:**
   - Adicionar comentário indicando que é temporário
   - Planejar remoção quando todos imports forem atualizados

### Prioridade Baixa
6. **Manter pipelines paralelos:**
   - Documentar qual é o principal
   - Remover comentários dos outros

---

## Conclusão

**Status: APROVADO COM RESERVAS** ⚠️

- **10 funções duplicadas** — Falso positivo (interface)
- **8 módulos antigos** — Requer ação
- **128 variáveis mortas** — Revisar as críticas
- **2 scripts obsoletos** — Remover
- **3 métricas duplicadas** — Unificar

**Principal risco:** Código morto em `profit_rtd.py` pode indicar lógica incompleta.
