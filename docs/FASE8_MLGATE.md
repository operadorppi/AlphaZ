# FASE 8 — P1 · Gate de ML Indisponível — Política Configurável

## 1. Problema corrigido

O comportamento anterior permitia **continuar operando com o ML
indisponível**, sem registrar que o ML não participou da decisão —
ou seja, a ausência do ML ficava *silenciosa*.

## 2. Novas regras

| Regra | Comportamento |
|---|---|
| Política configurável | `ml_required = True/False` (campo de `MlGatePolicy`) |
| **Produção** (ML na estratégia validada) | `PRODUCTION_POLICY = MlGatePolicy(ml_required=True, fallback_enabled=False)` |
| ML obrigatório e indisponível | `allowed = False`; o fallback **não é nem consultado**; o registro carrega o motivo da queda |
| Fallback habilitado e ML indisponível | `allowed = decisão heurística` e `decision_source = HEURISTIC_FALLBACK` — **sempre** registrado, com o motivo da indisponibilidade |
| Nunca esconder | Invariante de não-silêncio: é **impossível** registrar `decision_source = ML` quando o ML estava indisponível (validado em `MLDecisionLog.record` e reauditável via `assert_no_hidden_ml_absence`) |

O fallback **não foi removido**: ele passa a existir apenas sob
`ml_required=False` **e** `fallback_enabled=True`, e cada uso é
auditável.

## 3. Tabela-verdade

```text
ML disponível?   ml_required   fallback_enabled   resultado
--------------------------------------------------------------
SIM              qualquer      qualquer           source=ML, allowed (decisão do ML)
NAO              TRUE          qualquer           allowed=FALSE (BLOQUEADO;
                                                       fallback não é consultado)
NAO              FALSE         TRUE               allowed=heurística;
                                                       source=HEURISTIC_FALLBACK
NAO              FALSE         FALSE              allowed=FALSE (BLOQUEADO)
```

`ml_required=True` tem **precedência**: mesmo com `fallback_enabled=True`,
o bloco é absoluto quando o ML cai.

## 4. API

```python
from mlgate import (
    MlAvailability, MlGatePolicy, evaluate_gate,
    MLDecisionLog, PRODUCTION_POLICY, DEVELOPMENT_POLICY,
)

ml = MlAvailability.down("timeout no serviço de scoring")  # motivo obrigatório
policy = PRODUCTION_POLICY                                 # ml_required=True em produção

d = evaluate_gate(ml, policy, heuristic_decision=lambda: True)
assert d.allowed is False          # ML fora + obrigatório → bloqueio
assert d.decision_source == "BLOCKED"

# Desenvolvimento: contingência explícita
d2 = evaluate_gate(ml, DEVELOPMENT_POLICY, heuristic_decision=lambda: True)
assert d2.decision_source == "HEURISTIC_FALLBACK"

# Auditoria: impossível esconder a ausência do ML
log = MLDecisionLog()
log.record(d)
log.record(d2)
log.assert_no_hidden_ml_absence()
```

## 5. Campos de auditoria (`Decision`)

| Campo | Descrição |
|---|---|
| `allowed` | A decisão pode prosseguir? |
| `decision_source` | `ML` · `HEURISTIC_FALLBACK` · `BLOCKED` |
| `ml_available` | Estado do ML no momento |
| `ml_unavailable_reason` | Motivo explícito (obrigatório quando ML caiu) |
| `heuristic_decision` | Decisão da heurística (só no fallback; `None` caso contrário) |
| `policy_label` | Política aplicada (auditoria/telemetria) |
| `note` | Narrativa legível da decisão |

## 6. Testes (`tests/test_mlgate.py` — 23 testes)

| Grupo | O que cobre |
|---|---|
| `TestPolicyConstruction` | presets produção/desenvolvimento, rejeição de não-bool, label inválido |
| `TestMlAvailability` | motivo obrigatório na queda; rejeição de motivo vazio |
| `TestTruthTable` | **tabela-verdade completa**: 4 células + precedência de `ml_required` + heurística só é consultada no caso 3 (contador de chamadas) + `ValueError` quando o fallback está ON mas a heurística não foi fornecida |
| `TestNoSilenceInvariants` | fallback nunca declara `ML`; decisão bloqueada sempre carrega motivo; `decision_source` sempre pertence ao conjunto conhecido |
| `TestAuditLog` | log rejeita `decision_source=ML` com ML fora ("proibido esconder"), rejeita heurística com ML disponível, exige motivo no fallback, consultas e `fallback_ratio` |

Execução:

```bash
pytest -v
```
