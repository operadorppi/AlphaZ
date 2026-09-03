# FASE 9 — P1 · Replay Gate — Política por Ambiente

## 1. Problema corrigido

A produção não tinha regra explícita sobre o **replay obrigatório**
(pré-validação da estratégia contra dados históricos de replay). Sem
ela, a estratégia poderia operar em ambiente real **sem a prova de
replay** — e essa ausência poderia passar despercebida (silêncio).

## 2. Novas regras

- Cada ambiente possui política **explícita** (`EnvironmentPolicy`):
  as regras de ML da FASE 8 + `require_replay_validated`.
- **Produção**: `require_replay_validated = True`. Se o replay não
  estiver validado para a versão atual da estratégia, a operação é
  **BLOQUEADA** — antes mesmo do gate de ML ser consultado.
- Desenvolvimento: replay é **informativo** (nunca bloqueia).
- Paper: bloqueio configurável; preset padrão `False` (explícito e
  ajustável por ambiente).
- Invariante de não-silêncio estendida: em `PRODUCTION`, um registro
  `decision_source=ML` com `replay_validated=False` é **rejeitado**
  pelo log (`GateDecisionLog`).

## 3. Políticas por ambiente (presets)

| Ambiente | `ml_required` | `fallback_enabled` | `require_replay_validated` |
|---|---|---|---|
| `DEVELOPMENT` | `False` | `True` | `False` (informativo) |
| `PAPER` | `True` | `False` | `False` (ajustável; preset `False`) |
| `PRODUCTION` | `True` | `False` | **`True` (obrigatório)** |

Os presets da FASE 8 (`PRODUCTION_POLICY`, `DEVELOPMENT_POLICY`,
`mlgate.evaluate_gate`) **não foram alterados**: o ambiente de
desenvolvimento existente continua funcionando, e a suíte da FASE 8
(70 testes) passa inalterada.

## 4. Tabela-verdade do gate completo (`evaluate_replay_gate`)

```text
1. require_replay_validated=True e replay NAO validado
     → allowed=FALSE, source=BLOCKED   (replay_reason obrigatória)
     O gate de ML NAO é consultado.

2. Caso contrário, aplica-se o gate de ML da FASE 8:
     ML disponível            → source=ML
     ML fora + ml_required    → BLOCKED (motivo do ML)
     ML fora + fallback ON    → HEURISTIC_FALLBACK (motivo do ML)
   O estado do replay é SEMPRE auditado no registro (GateDecision).
```

Ordem de precedência: **replay bloqueante > regra de ML > fallback**.

## 5. API

```python
from mlgate import MlAvailability
from replaygate import (
    Environment, GateDecisionLog, ReplayStatus,
    environment_policy, evaluate_replay_gate,
)

replay = ReplayStatus.pending("replay pendente da v2.3 da estrategia")
prod   = environment_policy(Environment.PRODUCTION)

d = evaluate_replay_gate(MlAvailability.up(), replay, prod)
assert d.allowed is False and d.decision_source == "BLOCKED"

log = GateDecisionLog()
log.record(d)
log.assert_no_hidden_replay_absence()   # FASE 9
log.assert_no_hidden_ml_absence()       # FASE 8 (herdada)
```

### Campos de auditoria (`GateDecision`)

| Campo | Descrição |
|---|---|
| `allowed` / `decision_source` | como na FASE 8 |
| `replay_validated` | estado do replay obrigatório |
| `replay_reason` | motivo explícito quando não validado |
| `environment` | `DEVELOPMENT` / `PAPER` / `PRODUCTION` |
| `ml_decision` (property) | projeção para `mlgate.Decision` (compatibilidade FASE 8) |

## 6. Compatibilidade verificada

- Inventário de referências feito **antes** de alterar (ver relatório
  da fase): `mlgate` é o único módulo modificado (acréscimo de
  `validate()` + `BLOCKED` aceito como "não-ML" — correção de uma
  invariante que era mais restritiva do que a semântica da FASE 8:
  uma decisão `BLOCKED` por replay em produção não pode ser tratada
  como "decisão que deveria ter sido do ML").
- `tests/test_replaygate.py::TestBackwardCompatibility` trava que os
  presets da FASE 8 e o alinhamento com `PRODUCTION_ENV_POLICY.ml`
  se mantêm.
- 70 testes da FASE 7/8 continuam passando sem modificação.

## 7. Testes (`tests/test_replaygate.py` — 42 testes)

| Grupo | O que cobre |
|---|---|
| `TestReplayStatus` | motivo obrigatório na não-validação; rejeição de não-bool |
| `TestEnvironmentPresets` | **cada ambiente possui política explícita**; produção exige replay; dev/paper configurados; lookup `environment_policy` |
| `TestPolicyConstruction` | validações (ambiente, tipo de ML, não-bool, label) + política paper estrita |
| `TestTruthTable` | tabela por ambiente: produção bloqueia com replay pendente (mesmo com ML OK); replay precede ML (heurística não é consultada); dev opera sem replay (informativo); paper padrão e estrito; matriz parametrizada 9 células |
| `TestNoSilenceInvariants` | `GateDecision` sempre carrega estado do replay; `decision_source` nunca é `ML` com ML fora |
| `TestGateDecisionLog` | rejeita `ML`+`replay_validated=False` em PRODUCTION; aceita entradas legítimas; dev com replay pendente é permitido; invariantes FASE 8 ainda valem |
| `TestBackwardCompatibility` | presets da FASE 8 intactos; alinhamento `PRODUCTION_ENV_POLICY.ml`; projeção `ml_decision` |

Execução:

```bash
pytest -v
```
