"""FASE 9 P1 — Replay Gate + ambientes DEVELOPMENT / PAPER / PRODUCTION.

Problema corrigido
------------------
A produção não tinha uma regra explícita sobre o **replay validado**
(a pré-validação da estratégia contra dados históricos de replay).
Sem ela, a estratégia poderia operar em ambiente real sem a prova de
replay.

Novo comportamento
------------------
- Cada **ambiente** (``DEVELOPMENT``, ``PAPER``, ``PRODUCTION``) possui
  uma política **explícita** (``EnvironmentPolicy``): ML (FASE 8) +
  ``require_replay_validated``.
- **Produção**: ``require_replay_validated = True`` — se o replay
  obrigatório não estiver validado, a operação é BLOQUEADA.
- Desenvolvimento: replay é *informativo* (nunca bloqueia).
- Paper trading: bloqueio configurável, preset padrão ``False``.

Invariante de não-silêncio (mesma regra da FASE 8, agora para replay):
um registro ``decision_source=ML`` com ``replay_validated=False`` em
``PRODUCTION`` é rejeitado pelo log — o replay é parte da estratégia
validada e não pode ser ignorado em silêncio.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from mlgate import (
    DECISION_SOURCE_ML,
    Decision,
    MlAvailability,
    MlGatePolicy,
    MLDecisionLog,
    evaluate_gate,
)

DECISION_SOURCE_ML = DECISION_SOURCE_ML  # reexportado (conveniência)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReplayStatus:
    """Estado do replay obrigatório (pré-validação em dados históricos).

    Attributes
    ----------
    validated:
        ``True`` quando o replay exigido pelo ambiente foi executado e
        validado para a versão atual da estratégia.
    reason:
        Motivo da **não** validação (obrigatório quando ``validated=False``):
        ex.: ``"replay pendente da v2.3 da estratégia"``.
    """

    validated: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "validated", _as_bool(self.validated, "validated"))
        if not self.validated:
            if not (self.reason and str(self.reason).strip()):
                raise ValueError(
                    "ReplayStatus não validado exige 'reason' não vazia "
                    "(nunca se esconde por que o replay não foi validado)"
                )

    @classmethod
    def validated_(cls) -> "ReplayStatus":
        """Atalho: replay validado."""
        return cls(validated=True, reason=None)

    @classmethod
    def pending(cls, reason: str) -> "ReplayStatus":
        """Atalho: replay não validado com motivo explícito."""
        return cls(validated=False, reason=reason)


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} deve ser bool (True/False), obtido {value!r}")
    return value


from config.defaults import DEFAULT_ENV_PRESETS  # noqa: E402


# ---------------------------------------------------------------------------
# Ambientes
# ---------------------------------------------------------------------------
class Environment(Enum):
    """Ambientes de execução com política explícita."""

    DEVELOPMENT = "DEVELOPMENT"
    PAPER = "PAPER"
    PRODUCTION = "PRODUCTION"


@dataclass(frozen=True)
class EnvironmentPolicy:
    """Política **explícita** de um ambiente (FASE 8 + FASE 9).

    Attributes
    ----------
    environment:
        ``DEVELOPMENT``, ``PAPER`` ou ``PRODUCTION``.
    ml:
        Política de ML da FASE 8 (``ml_required`` / ``fallback_enabled``).
    require_replay_validated:
        ``True`` → a operação fica BLOQUEADA se o replay obrigatório não
        estiver validado. Em ``PRODUCTION`` o preset fixa ``True``.
    label:
        Identificador para auditoria/telemetria.
    """

    environment: Environment
    ml: MlGatePolicy
    require_replay_validated: bool
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise TypeError("environment deve ser Environment.DEVELOPMENT/PAPER/PRODUCTION")
        if not isinstance(self.ml, MlGatePolicy):
            raise TypeError("ml deve ser MlGatePolicy (FASE 8)")
        object.__setattr__(
            self, "require_replay_validated",
            _as_bool(self.require_replay_validated, "require_replay_validated"),
        )
        if not self.label:
            object.__setattr__(self, "label", self.environment.value.lower())

    # -- conveniências -------------------------------------------------------
    @property
    def ml_required(self) -> bool:
        return self.ml.ml_required

    @property
    def fallback_enabled(self) -> bool:
        return self.ml.fallback_enabled


# ---------------------------------------------------------------------------
# Presets por ambiente (cada ambiente possui política explícita)
# ---------------------------------------------------------------------------
DEVELOPMENT_ENV_POLICY = EnvironmentPolicy(
    environment=Environment.DEVELOPMENT,
    ml=MlGatePolicy(
        ml_required=DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.ml_required,
        fallback_enabled=DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.fallback_enabled,
        label="development",
    ),
    require_replay_validated=DEFAULT_ENV_PRESETS["DEVELOPMENT"].require_replay_validated,
    label="development",
)

PAPER_ENV_POLICY = EnvironmentPolicy(
    environment=Environment.PAPER,
    ml=MlGatePolicy(
        ml_required=DEFAULT_ENV_PRESETS["PAPER"].ml.ml_required,
        fallback_enabled=DEFAULT_ENV_PRESETS["PAPER"].ml.fallback_enabled,
        label="paper",
    ),
    require_replay_validated=DEFAULT_ENV_PRESETS["PAPER"].require_replay_validated,
    label="paper",
)

PRODUCTION_ENV_POLICY = EnvironmentPolicy(
    environment=Environment.PRODUCTION,
    ml=MlGatePolicy(
        ml_required=DEFAULT_ENV_PRESETS["PRODUCTION"].ml.ml_required,
        fallback_enabled=DEFAULT_ENV_PRESETS["PRODUCTION"].ml.fallback_enabled,
        label="production",
    ),
    require_replay_validated=DEFAULT_ENV_PRESETS["PRODUCTION"].require_replay_validated,
    label="production",
)

ENVIRONMENT_POLICIES: dict[Environment, EnvironmentPolicy] = {
    Environment.DEVELOPMENT: DEVELOPMENT_ENV_POLICY,
    Environment.PAPER: PAPER_ENV_POLICY,
    Environment.PRODUCTION: PRODUCTION_ENV_POLICY,
}


# ---------------------------------------------------------------------------
# Decisão com replay
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateDecision:
    """Resultado do gate completo (FASE 8 + FASE 9) com auditoria de replay."""

    allowed: bool
    decision_source: str
    ml_available: bool
    ml_unavailable_reason: str | None
    replay_validated: bool
    replay_reason: str | None
    heuristic_decision: bool | None
    environment: Environment
    policy_label: str
    note: str

    @property
    def used_ml(self) -> bool:
        return self.decision_source == DECISION_SOURCE_ML

    @property
    def ml_decision(self) -> Decision:
        """Projeção da decisão como ``mlgate.Decision`` (compatível com a FASE 8)."""
        return Decision(
            allowed=self.allowed,
            decision_source=self.decision_source,
            ml_available=self.ml_available,
            ml_unavailable_reason=self.ml_unavailable_reason,
            heuristic_decision=self.heuristic_decision,
            policy_label=self.policy_label,
            note=self.note,
        )


def evaluate_replay_gate(
    ml: MlAvailability,
    replay: ReplayStatus,
    policy: EnvironmentPolicy,
    heuristic_decision: Callable[[], bool] | None = None,
) -> GateDecision:
    """Avalia o gate completo: política de ML (FASE 8) + replay (FASE 9).

    Ordem de avaliação:

    1. **Replay bloqueante** (``require_replay_validated=True`` e replay não
       validado) → ``allowed=False``, ``decision_source=BLOCKED``. O gate de
       ML nem é consultado (a estratégia não está validada).
    2. Caso contrário, o gate de ML (FASE 8) decide; o replay aparece na
       auditoria, mas não bloqueia.

    Parâmetros iguais a :func:`mlgate.evaluate_gate`, mais ``replay``.
    """
    env = policy.environment
    label = policy.label

    if policy.require_replay_validated and not replay.validated:
        return GateDecision(
            allowed=False,
            decision_source="BLOCKED",
            ml_available=ml.available,
            ml_unavailable_reason=ml.reason if not ml.available else None,
            replay_validated=False,
            replay_reason=replay.reason,
            heuristic_decision=None,
            environment=env,
            policy_label=label,
            note=(
                f"REPLAY NAO VALIDADO ({replay.reason}) em {env.value} "
                f"com require_replay_validated=True [{label}]: BLOQUEADO. "
                f"O gate de ML nao foi consultado (estrategia sem prova de replay)."
            ),
        )

    base = evaluate_gate(ml, policy.ml, heuristic_decision=heuristic_decision)
    replay_tag = "" if replay.validated else (
        f" [replay pendente: {replay.reason}]"
    )
    return GateDecision(
        allowed=base.allowed,
        decision_source=base.decision_source,
        ml_available=base.ml_available,
        ml_unavailable_reason=base.ml_unavailable_reason,
        replay_validated=replay.validated,
        replay_reason=replay.reason if not replay.validated else None,
        heuristic_decision=base.heuristic_decision,
        environment=env,
        policy_label=label,
        note=base.note + replay_tag,
    )


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
class GateDecisionLog:
    """Log de auditoria estendido para o gate completo (ML + replay).

    Valida as invariantes da FASE 8 (não-silêncio do ML, aplicadas sobre a
    projeção ``ml_decision``) e acrescenta a invariante da FASE 9:

    - em ``PRODUCTION`` (``require_replay_validated=True``), uma entrada com
      ``decision_source=ML`` e ``replay_validated=False`` é **rejeitada**:
      em produção o replay é parte da estratégia validada.
    """

    def __init__(self) -> None:
        self.entries: list[GateDecision] = []

    def validate(self, decision: "GateDecision") -> None:
        """Valida as invariantes de uma entrada (sem anexar)."""
        MLDecisionLog.validate(decision.ml_decision)
        if (
            decision.environment is Environment.PRODUCTION
            and decision.decision_source == DECISION_SOURCE_ML
            and not decision.replay_validated
        ):
            raise ValueError(
                "PRODUCTION: decision_source=ML com replay_validated=False — "
                "producao nao opera sem o replay obrigatorio validado."
            )

    def record(self, decision: "GateDecision") -> None:
        """Valida e anexa (idem :meth:`mlgate.MLDecisionLog.record`)."""
        self.validate(decision)
        self.entries.append(decision)

    # -- consultas herdadas (listas de GateDecision) --------------------------
    @property
    def total(self) -> int:
        return len(self.entries)

    def ml_decisions(self) -> list["GateDecision"]:
        return [d for d in self.entries if d.decision_source == DECISION_SOURCE_ML]

    def fallback_decisions(self) -> list["GateDecision"]:
        return [d for d in self.entries if d.decision_source == "HEURISTIC_FALLBACK"]

    def blocked_decisions(self) -> list["GateDecision"]:
        return [d for d in self.entries if d.decision_source == "BLOCKED"]

    def fallback_ratio(self) -> float:
        if not self.entries:
            return 0.0
        return len(self.fallback_decisions()) / len(self.entries)

    def assert_no_hidden_ml_absence(self) -> None:
        """Reafirma o invariante da FASE 8 em todo o log."""
        for d in self.entries:
            MLDecisionLog._check_one(d.ml_decision)

    def assert_no_hidden_replay_absence(self) -> None:
        """Reafirma o invariante de replay (FASE 9) em todo o log."""
        for d in self.entries:
            if (
                d.environment is Environment.PRODUCTION
                and d.decision_source == DECISION_SOURCE_ML
                and not d.replay_validated
            ):
                raise AssertionError(
                    "Auditoria: PRODUCTION declara decisao do ML com replay nao validado."
                )


def environment_policy(environment: Environment) -> EnvironmentPolicy:
    """Retorna o preset explícito do ambiente."""
    return ENVIRONMENT_POLICIES[environment]
