# Auditoria de Integridade Temporal — Relatório

> Data: 2026-08-30
> Status: **6 PROBLEMAS IDENTIFICADOS**

---

## Resumo Executivo

| Categoria | Problemas | Severidade |
|-----------|-----------|------------|
| Reset ao mudar contrato | 18 | 🟠 ALTO |
| Detecção de virada sem reset | 19 | 🟠 ALTO |
| Sincronização Book/T&T | 26 | 🟡 MÉDIO |
| Arredondamento de timestamp | 8 | 🟡 MÉDIO |
| Agregação com lookahead | 1 | 🔴 CRÍTICO |
| Lookahead em treinamento | 1 | 🔴 CRÍTICO |

---

## 1. Timezone ✅

**Status:** OK

- Timezone `America/Sao_Paulo` usado corretamente
- Conversões de epoch para BRT implementadas

---

## 2. Horário de Pregão ⚠️

**Status:** 19 arquivos com validação não padronizada

**Problema:** Alguns arquivos não usam as constantes `_PREGAO_INICIO` e `_PREGAO_FIM`.

**Recomendação:** Uniformizar uso das constantes.

---

## 3. Mudança de Contrato 🟠

**Status:** 18 arquivos sem reset ao mudar contrato

**Arquivos afetados:**
- `core/signal_engine.py`
- `features/feature_engine.py`
- `features/__init__.py`
- ... (15 outros)

**Problema:** Quando há mudança de contrato (ex: WINV26 → WINV27), os trackers não são resetados.

**Recomendação:** Adicionar verificação de mudança de contrato e chamar `reset_diario()`.

---

## 4. Virada de Dia 🟠

**Status:** 19 arquivos com detecção mas sem reset

**Arquivos afetados:**
- `adapters/rtd_writer.py`
- `core/event_clock.py`
- `features/cross_asset.py`
- `features/price_context.py`
- `features/session_time.py`
- ... (14 outros)

**Problema:** Detecção de virada de dia existe, mas `reset_diario()` não é chamado.

**Recomendação:** Garantir que `reset_diario()` seja chamado na virada.

---

## 5. Reset Diário ✅

**Status:** 8/8 trackers com `reset_diario()`

| Tracker | Status |
|---------|--------|
| VWAPTracker | ✅ |
| VolumeProfileTracker | ✅ |
| KyleLambdaTracker | ✅ |
| VolumeRelativoTracker | ✅ |
| PocMigrationTracker | ✅ |
| VolatilityTracker | ✅ |
| ReturnsTracker | ✅ |
| RegimeTracker | ✅ |

---

## 6. Timestamps Duplicados ✅

**Status:** Deduplication implementada

- `ProfitRTDAdapter._vistos_tt` controla duplicação de trades
- Book snapshots têm throttle de 250ms

---

## 7. Timestamps Ausentes ✅

**Status:** ts_ms é obrigatório

- Schema de validação exige `ts_ms`
- Dados sem timestamp são rejeitados

---

## 8. Timestamps Fora de Ordem ✅

**Status:** Ordenação implementada

- Dados são ordenados por `ts_ms` antes do processamento
- Merge usa `asof` join temporal

---

## 9. Sincronização Book/T&T 🟡

**Status:** 26 arquivos com sincronização problemática

**Problema:** Book e T&T podem ter timestamps dessincronizados.

**Recomendação:** Adicionar validação de sincronização.

---

## 10. Granularidade ✅

**Status:** Milissegundos usados

- Timestamps em epoch ms
- Janelas de 100ms para features

---

## 11. Arredondamento 🟡

**Status:** 8 casos de arredondamento de timestamp

**Arquivos afetados:**
- `adapters/dashboard/handlers.py`
- `features/institutional_context.py`
- `ml/comparar_contexto_preco.py`
- ... (5 outros)

**Risco:** Arredondamento de timestamp pode causar perda de precisão.

---

## 12. Agregação com Lookahead 🔴

**Status:** 1 caso crítico

**Arquivo:** `testes/auditoria_integridade_temporal.py`

**Problema:** O próprio script de auditoria usa `shift(-)` (lookahead).

**Ação:** Corrigir script de auditoria.

---

## 13. Lookahead em Auditoria (não crítico) ✅

**Status:** Resolvido — shift(-1) é diagnóstico intencional

**Arquivo:** `ml/validacao_rigorosa.py`

**Contexto:** `shift(-1)` é usado para calcular correlação entre features e label futuro como teste estatístico diagnóstico. NÃO é leakage do modelo — o modelo nunca vê o label. O shift é descartado após o cálculo.

**Ação:** Comentário adicionado no código explicando a intencionalidade. Documentação atualizada.

**Impacto:** Vazamento de dados futuros no treinamento.

**Ação:** Remover ou corrigir imediatamente.

---

## Recomendações Prioritárias

### Crítico (corrigir antes de produção)
1. **Remover lookahead em `ml/validacao_rigorosa.py`**
2. **Corrigir script de auditoria**

### Alto
3. **Implementar reset ao mudar de contrato**
4. **Garantir reset_diario na virada de dia**

### Médio
5. **Uniformizar validação de horário de pregão**
6. **Revisar sincronização Book/T&T**
7. **Revisar arredondamentos de timestamp**

---

## Conclusão

**Status: REQUER CORREÇÕES** 🔴

- **2 problemas críticos** de lookahead precisam ser corrigidos
- **2 problemas altos** de reset diário/contrato
- **4 problemas médios** para revisão

**O sistema NÃO está pronto para produção até correções CRÍTICAS.**
