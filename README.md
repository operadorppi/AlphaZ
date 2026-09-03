# Sistema de Exposição Financeira + Gates

## Instalação

```bash
# Clonar repositório
git clone <url-do-repo>
cd exposure-financeira

# Criar ambiente virtual (recomendado)
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac

# Instalar dependências
pip install -e ".[dev]"         # dev + testes
# pip install -e .              # apenas runtime (sem dependências externas)
```

**Requisitos:** Python >= 3.10, pytest (opcional para testes).

O projeto não depende de bibliotecas externas para operação — apenas bibliotecas padrão (`decimal`, `dataclasses`, `enum`).

## Testes

```bash
# Rodar todos os testes
pytest

# Com output detalhado
pytest -v

# Com cobertura
pytest --cov=. --cov-report=term-missing

# Focando em uma fase específica
pytest tests/test_config.py -v
pytest tests/test_mlgate.py -v
pytest tests/test_replaygate.py -v
```

**Suíte atual:** 205 testes passando (FASES 7-15).

## Execução

```bash
# Exemplo básico de exposição financeira
python examples/demo.py

# Gate de ML
python examples/demo_mlgate.py

# Replay gate por ambiente
python examples/demo_replaygate.py

# Verificação de integração ponta a ponta
python check_integration.py
```

Correção do conceito de `exposure_atual`:

- **Antes (errado):** `exposure_atual = TP + SL` — soma de distâncias em
  pontos, que mede a *faixa de resultado*, não o tamanho da posição.
- **Depois (corrigido):**
  - `exposure` = **exposição nominal** `E = N · P · V`
  - `risk_at_stop` = **risco máximo no stop** `R = d_stop · N · V`
  - `exposição agregada`: `E_agg = ΣE` (bruto), `R_agg = ΣR` (pior caso),
    `E_net = Σ sgn·E` (líquido, com hedge)

Documentação completa das fórmulas: [`docs/FORMULAS.md`](docs/FORMULAS.md).

## FASE 8 P1 · Gate de ML (política configurável)

- **Problema corrigido:** comportamento antigo deixava operar com o ML
  indisponível, sem registrar que o ML não participou da decisão.
- **Política:** `ml_required = True/False` (`MlGatePolicy`).
- **Produção:** `PRODUCTION_POLICY = MlGatePolicy(ml_required=True,
  fallback_enabled=False)` → ML obrigatório e indisponível ⇒ `allowed=False`.
- **Fallback:** quando habilitado (`ml_required=False` e
  `fallback_enabled=True`), a decisão é registrada com
  `decision_source = HEURISTIC_FALLBACK` — nunca se esconde a ausência do ML.
- **Invariante de não-silêncio:** `MLDecisionLog` rejeita qualquer entrada
  que declare `decision_source=ML` com o ML indisponível.

Documentação completa: [`docs/FASE8_MLGATE.md`](docs/FASE8_MLGATE.md).

## FASE 9 P1 · Replay Gate (DEVELOPMENT / PAPER / PRODUCTION)

- **Problema corrigido:** produção não tinha regra explícita sobre o
  replay obrigatório (pré-validação em dados históricos de replay).
- **Política por ambiente:** cada ambiente tem `EnvironmentPolicy`
  explícita (ML da FASE 8 + `require_replay_validated`):

  | Ambiente | `ml_required` | `fallback_enabled` | `require_replay_validated` |
  |---|---|---|---|
  | `DEVELOPMENT` | `False` | `True` | `False` (informativo) |
  | `PAPER` | `True` | `False` | `False` (ajustável) |
  | `PRODUCTION` | `True` | `False` | **`True` (obrigatório)** |

- **Produção não opera** se o replay obrigatório não estiver validado
  (bloqueia **antes** do gate de ML).
- **Não-silêncio estendido:** `GateDecisionLog` rejeita
  `decision_source=ML` com `replay_validated=False` em `PRODUCTION`.
- **Compatibilidade:** presets da FASE 8 não alterados; suíte anterior
  intacta.

Documentação completa: [`docs/FASE9_REPLAYGATE.md`](docs/FASE9_REPLAYGATE.md).

```python
from mlgate import MlAvailability
from replaygate import Environment, ReplayStatus, environment_policy, evaluate_replay_gate

d = evaluate_replay_gate(
    MlAvailability.up(),
    ReplayStatus.pending("replay pendente da v2.3"),
    environment_policy(Environment.PRODUCTION),
)
assert d.allowed is False            # produção sem replay validado -> bloqueio
assert d.decision_source == "BLOCKED"
```

```python
from mlgate import MlAvailability, PRODUCTION_POLICY, evaluate_gate

d = evaluate_gate(
    MlAvailability.down("timeout no servico de scoring"),
    PRODUCTION_POLICY,
    heuristic_decision=lambda: True,
)
assert d.allowed is False            # ML fora + obrigatório -> bloqueio
assert d.decision_source == "BLOCKED"
```

## Estrutura

```text
exposure/                  # FASE 7 P1 - exposição financeira
  direction.py   # Direction.WIN (+1) / Direction.LOSS (-1)
  position.py    # Position (ativo, direção, N, P, V, stop, alvo) + validações
  formulas.py    # E, R, L, E_s, razões, stop por moeda/pontos
  portfolio.py   # agregação: E_agg, R_agg, E_net
mlgate/
  __init__.py    # FASE 8 P1 - MlAvailability, MlGatePolicy, evaluate_gate,
                 #            PRODUCTION_POLICY, DEVELOPMENT_POLICY, MLDecisionLog
replaygate/
  __init__.py    # FASE 9 P1 - ReplayStatus, Environment, EnvironmentPolicy,
                 #            DEVELOPMENT/PAPER/PRODUCTION presets,
                 #            evaluate_replay_gate, GateDecision, GateDecisionLog
tests/
  test_position.py     # construção e validações
  test_formulas.py     # valores conhecidos + regressão TP+SL != E
  test_aggregate.py    # somas, hedge bruto/líquido, posição sem stop
  test_invariants.py   # 300 amostras aleatórias (recálculo independente)
  test_mlgate.py       # tabela-verdade do gate + invariantes de não-silêncio
  test_replaygate.py   # replay gate + políticas por ambiente + compatibilidade
examples/
  demo.py              # exemplo executável (posição WIN)
  demo_mlgate.py       # exemplo executável (gate de ML)
  demo_replaygate.py   # exemplo executável (replay gate por ambiente)
docs/
  FORMULAS.md          # documentação completa das fórmulas
  FASE8_MLGATE.md      # documentação do gate de ML
  FASE9_REPLAYGATE.md  # documentação do replay gate
  FASE10_CONFIG.md     # documentação da configuração (fonte única, prioridade, legado)
config/                  # FASE 10 P1 - fonte única de configuração
  __init__.py           # re-export público
  errors.py             # ConfigError
  defaults.py           # ÚNICA fonte de verdade (DEFAULT_MAX_DRAWDOWN_DIA, DEFAULT_ENV_PRESETS)
  loader.py             # load_config(P1>P2>P3>P4, validação, legacy map)
tests/
  test_position.py     # construção e validações
  test_formulas.py     # valores conhecidos + regressão TP+SL != E
  test_aggregate.py    # somas, hedge bruto/líquido, posição sem stop
  test_invariants.py   # 300 amostras aleatórias (recálculo independente)
  test_mlgate.py       # tabela-verdade do gate + invariantes de não-silêncio
  test_replaygate.py   # replay gate + políticas por ambiente + compatibilidade
  test_config.py       # prioridade, legado, validação, matriz fonte-origem-consumidor
examples/
  demo.py              # exemplo executável (posição WIN)
  demo_mlgate.py       # exemplo executável (gate de ML)
  demo_replaygate.py   # exemplo executável (replay gate por ambiente)
```

## Uso rápido

```python
from decimal import Decimal
from exposure import Direction, Position, nominal_exposure, risk_at_stop, aggregate_exposure

pos = Position(
    asset="WIN",
    direction=Direction.WIN,
    quantity=Decimal(10),
    price=Decimal("150000"),
    point_value=Decimal("0.20"),
    stop=Decimal("149800"),      # stop financeiro: 200 pontos abaixo
    target=Decimal("150400"),    # alvo: 400 pontos acima
)

print(nominal_exposure(pos))  # 300000  (E = N*P*V)
print(risk_at_stop(pos))      # 400     (R = d_stop*N*V)
```

## Testes

```bash
pytest -v
```
