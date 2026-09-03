"""ÚNICA fonte de verdade dos valores padrão (FASE 10 P1).

Este módulo é o ÚNICO lugar do projeto que define valores padrão de
configuração:

- os presets de ``mlgate`` e ``replaygate`` derivam daqui (sem duplicação);
- ``config.json`` (quando fornecido) sobrepõe estes valores;
- ``max_drawdown_dia`` tem EXATAMENTE esta constante como fonte de
  verdade — nenhum outro módulo define ou duplica este padrão.

Camada de importação (DAG, sem ciclos — ver docs/FASE10_CONFIG.md)::

    config.defaults  <-  mlgate  <-  replaygate  <-  config.loader
"""

from dataclasses import dataclass
from decimal import Decimal

# ---------------------------------------------------------------------------
# Risco
# ---------------------------------------------------------------------------
#: Máximo de drawdown diário, fração do equity diário.
#: Restrição: 0 < max_drawdown_dia <= 1.
#: **Fonte de verdade única** (FASE 10 P1).
DEFAULT_MAX_DRAWDOWN_DIA: Decimal = Decimal("0.02")

MIN_MAX_DRAWDOWN_DIA: Decimal = Decimal("0")   # inferior (exclusivo)
MAX_MAX_DRAWDOWN_DIA: Decimal = Decimal("1")   # superior (inclusivo)

#: Ambiente padrão quando o config.json não informa.
DEFAULT_ENVIRONMENT: str = "DEVELOPMENT"

VALID_ENVIRONMENTS: tuple[str, ...] = ("DEVELOPMENT", "PAPER", "PRODUCTION")


# ---------------------------------------------------------------------------
# Presets por ambiente (únicos no projeto)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MlPresetDefaults:
    """Defaults da política de ML (FASE 8) por ambiente."""

    ml_required: bool
    fallback_enabled: bool
    label: str


@dataclass(frozen=True)
class EnvPresetDefaults:
    """Defaults completos de um ambiente (FASE 8 + FASE 9)."""

    ml: MlPresetDefaults
    require_replay_validated: bool
    label: str


DEFAULT_ML_PRESETS: dict[str, MlPresetDefaults] = {
    "DEVELOPMENT": MlPresetDefaults(
        ml_required=False, fallback_enabled=True, label="development",
    ),
    "PAPER": MlPresetDefaults(
        ml_required=True, fallback_enabled=False, label="paper",
    ),
    "PRODUCTION": MlPresetDefaults(
        ml_required=True, fallback_enabled=False, label="production",
    ),
}

DEFAULT_ENV_PRESETS: dict[str, EnvPresetDefaults] = {
    env: EnvPresetDefaults(
        ml=DEFAULT_ML_PRESETS[env],
        # FASE 9 P1: apenas produção exige replay validado por padrão
        require_replay_validated=(env == "PRODUCTION"),
        label=env.lower(),
    )
    for env in VALID_ENVIRONMENTS
}
