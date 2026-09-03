"""FASE 8 P1 — Gate de ML indisponível (política configurável).

Problema corrigido
------------------
O comportamento anterior permitia continuar operando quando o ML estava
indisponível, **escondendo** que o ML não participou da decisão.

Novo comportamento
------------------
- Política configurável: ``ml_required = True/False``.
- **Produção** (ML faz parte da estratégia validada): ``ml_required = True``
  (preset :data:`PRODUCTION_POLICY`).
- ML obrigatório e indisponível  → ``allowed = False``.
- Fallback habilitado            → a decisão é registrada com
  ``decision_source = HEURISTIC_FALLBACK``.
- **Nunca é permitido** um registro com ``decision_source = ML`` quando o
  ML estava indisponível (invariante de não-silêncio, auditada em
  :class:`MLDecisionLog`).

Tabela-verdade (ver docs/FASE8_MLGATE.md):

=================  ===========  ==================  ==============
ML disponível?     ml_required  fallback_enabled    resultado
=================  ===========  ==================  ==============
SIM                qualquer     qualquer            allowed pelo ML;
                                                        source=ML
NAO                TRUE         qualquer            allowed=FALSE
                                                        (ML indisponível)
NAO                FALSE        TRUE                allowed=decisão heurística;
                                                        source=HEURISTIC_FALLBACK
NAO                FALSE        FALSE               allowed=FALSE
                                                        (sem ML e sem fallback)
=================  ===========  ==================  ==============
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Fontes de decisão
# ---------------------------------------------------------------------------
DECISION_SOURCE_ML = "ML"
DECISION_SOURCE_HEURISTIC_FALLBACK = "HEURISTIC_FALLBACK"
DECISION_SOURCE_BLOCKED = "BLOCKED"

DECISION_SOURCES = (
    DECISION_SOURCE_ML,
    DECISION_SOURCE_HEURISTIC_FALLBACK,
    DECISION_SOURCE_BLOCKED,
)


@dataclass(frozen=True)
class MlAvailability:
    """Estado do ML no momento da decisão.

    Attributes
    ----------
    available:
        ``True`` quando o modelo respondeu dentro da janela/SLA válida.
    reason:
        Motivo da indisponibilidade (obrigatório quando ``available=False``).
        Ex.: ``"timeout"`` , ``"modelo fora de serviço"``.
    """

    available: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.available:
            if not (self.reason and str(self.reason).strip()):
                raise ValueError(
                    "MlAvailability indisponível exige 'reason' não vazia "
                    "(nunca se esconde por que o ML não participou)"
                )

    @classmethod
    def up(cls) -> "MlAvailability":
        """Atalho: ML disponível (sem motivo)."""
        return cls(available=True, reason=None)

    @classmethod
    def down(cls, reason: str) -> "MlAvailability":
        """Atalho: ML indisponível com motivo explícito."""
        return cls(available=False, reason=reason)


@dataclass(frozen=True)
class MlGatePolicy:
    """Política configurável do gate de ML.

    Attributes
    ----------
    ml_required:
        ``True`` → ML é obrigatório (estratégia validada). Se indisponível,
        ``allowed = False`` — o fallback NÃO é consultado.
        ``False`` → operação pode continuar por fallback, se habilitado.
    fallback_enabled:
        ``True`` → a decisão heurística de contingência pode ser usada
        (e é SEMPRE registrada como ``HEURISTIC_FALLBACK``).
        ``False`` → sem ML não há fonte de decisão: ``allowed = False``.
    label:
        Identificador da política (auditoria/telemetria).
    """

    ml_required: bool
    fallback_enabled: bool
    label: str = "policy"

    def __post_init__(self) -> None:
        # bools puros (rejeita "true"/0-1 disfarçados, Decimal etc.)
        object.__setattr__(self, "ml_required", _as_bool(self.ml_required, "ml_required"))
        object.__setattr__(self, "fallback_enabled", _as_bool(self.fallback_enabled, "fallback_enabled"))
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label deve ser um texto não vazio")


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} deve ser bool (True/False), obtido {value!r}")
    return value


from config.defaults import DEFAULT_ENV_PRESETS  # noqa: E402


#: Preset de **produção**: ML faz parte da estratégia validada.
#: Quando o ML cair, a operação é BLOQUEADA — não há decisão heurística
#: "silenciosa" em produção com ``ml_required=True``.
PRODUCTION_POLICY = MlGatePolicy(
    ml_required=DEFAULT_ENV_PRESETS["PRODUCTION"].ml.ml_required,
    fallback_enabled=DEFAULT_ENV_PRESETS["PRODUCTION"].ml.fallback_enabled,
    label="production",
)

#: Preset de **desenvolvimento**: ML pode cair e a heurística mantém o
#: fluxo, mas cada decisão de contingência é registrada explicitamente.
DEVELOPMENT_POLICY = MlGatePolicy(
    ml_required=DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.ml_required,
    fallback_enabled=DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.fallback_enabled,
    label="development",
)


@dataclass(frozen=True)
class Decision:
    """Resultado do gate. Este objeto É a auditoria da decisão."""

    allowed: bool
    decision_source: str
    ml_available: bool
    ml_unavailable_reason: str | None
    heuristic_decision: bool | None  # decisão da heurística (None se não foi consultada)
    policy_label: str
    note: str

    @property
    def used_ml(self) -> bool:
        """O ML participou da decisão?"""
        return self.decision_source == DECISION_SOURCE_ML


def evaluate_gate(
    ml: MlAvailability,
    policy: MlGatePolicy,
    heuristic_decision: Callable[[], bool] | None = None,
) -> Decision:
    """Avalia se a decisão pode prosseguir conforme a política.

    Regras (tabela-verdade no docstring do módulo):

    1. ML disponível        → decisão vem do ML; ``allowed`` é a decisão do
       ML (aqui representada por ``heuristic_decision`` não entra em jogo —
       o ML decide; em integração real, a decisão do ML é passada).
    2. ML indisponível e ``ml_required=True`` → ``allowed=False``.
       O fallback NÃO é consultado; o registro carrega o motivo.
    3. ML indisponível, ``ml_required=False`` e ``fallback_enabled=True``
       → ``allowed = heurística``; ``decision_source=HEURISTIC_FALLBACK``.
    4. ML indisponível, ``ml_required=False`` e ``fallback_enabled=False``
       → ``allowed=False`` (sem fonte de decisão válida).

    Parameters
    ----------
    ml:
        Disponibilidade atual do ML (com motivo, se indisponível).
    policy:
        Política configurável (``PRODUCTION_POLICY`` em produção).
    heuristic_decision:
        Fábrica da decisão heurística. ``None`` significa "a heurística
        diria NÃO" (conservador). Só é **consultada** no caso 3.

    Raises
    ------
    ValueError
        Se o caso 3 precisa do fallback mas ``heuristic_decision`` não foi
        fornecido (falha de configuração de integração).
    """
    label = policy.label

    if ml.available:
        # O ML participa: a decisão final é do ML. Nesta abstração o gate
        # devolve allowed=True com source=ML; o valor real da decisão do ML
        # é a responsabilidade do caller (que conhece o score/sinal).
        return Decision(
            allowed=True,
            decision_source=DECISION_SOURCE_ML,
            ml_available=True,
            ml_unavailable_reason=None,
            heuristic_decision=None,
            policy_label=label,
            note="ML disponível: decisão do ML.",
        )

    # ML indisponível
    if policy.ml_required:
        return Decision(
            allowed=False,
            decision_source=DECISION_SOURCE_BLOCKED,
            ml_available=False,
            ml_unavailable_reason=ml.reason,
            heuristic_decision=None,
            policy_label=label,
            note=(
                f"ML INDISPONÍVEL ({ml.reason}) e ml_required=True "
                f"[{label}]: decisão BLOQUEADA. Fallback não é consultado."
            ),
        )

    if not policy.fallback_enabled:
        return Decision(
            allowed=False,
            decision_source=DECISION_SOURCE_BLOCKED,
            ml_available=False,
            ml_unavailable_reason=ml.reason,
            heuristic_decision=None,
            policy_label=label,
            note=(
                f"ML INDISPONÍVEL ({ml.reason}) e fallback desabilitado "
                f"[{label}]: sem fonte de decisão válida. BLOQUEADO."
            ),
        )

    # Caso 3: fallback habilitado
    if heuristic_decision is None:
        raise ValueError(
            "Fallback habilitado (ml_required=False, fallback_enabled=True) "
            "mas 'heuristic_decision' não foi fornecido. Configure a heurística "
            "ou desabilite o fallback."
        )
    h = bool(heuristic_decision())
    return Decision(
        allowed=h,
        decision_source=DECISION_SOURCE_HEURISTIC_FALLBACK,
        ml_available=False,
        ml_unavailable_reason=ml.reason,
        heuristic_decision=h,
        policy_label=label,
        note=(
            f"ML INDISPONÍVEL ({ml.reason}): decisão de CONTINGÊNCIA por "
            f"heurística (allowed={h}). Registrada como "
            f"{DECISION_SOURCE_HEURISTIC_FALLBACK} — o ML NÃO participou."
        ),
    )


@dataclass
class MLDecisionLog:
    """Log de auditoria com invariantes de **não-silêncio**.

    Toda entrada é validada na inserção:

    - se ``ml_available=True``  → ``decision_source`` deve ser ``ML``.
    - se ``ml_available=False`` → ``decision_source`` **não pode** ser ``ML``
      (não se pode afirmar que o ML decidiu quando ele não estava).
    - se ``decision_source=HEURISTIC_FALLBACK`` → ``ml_unavailable_reason``
      deve estar presente.

    Estas regras tornam impossível, por construção, "esconder que o ML não
    participou da decisão".
    """

    entries: list[Decision] = field(default_factory=list)

    def record(self, decision: Decision) -> None:
        self.validate(decision)
        self.entries.append(decision)

    # ``validate`` é @staticmethod (não usa self); ``record`` chama via classe
    # para evitar confusão de tipos ao ser reutilizado por GateDecisionLog.

    @staticmethod
    def validate(decision: Decision) -> None:
        """Valida as invariantes de não-silêncio de uma entrada (sem anexar).

        Regras:

        - ``BLOCKED`` é sempre aceito (é, por definição, "não foi ML").
        - se o ML estava **disponível** e a decisão **não** foi
          bloqueada, o ``decision_source`` deve ser ``ML`` — não se pode
          silenciosamente "degradação" para heurística com o ML de pé.
        - se o ML estava **indisponível**, ``decision_source`` **não pode**
          ser ``ML`` (não se pode afirmar que o ML decidiu quando ele não
          estava).
        - ``HEURISTIC_FALLBACK`` exige motivo explícito da queda do ML.
        """
        if decision.decision_source == DECISION_SOURCE_BLOCKED:
            return
        if decision.ml_available:
            if decision.decision_source != DECISION_SOURCE_ML:
                raise ValueError(
                    f"ML disponível mas decision_source={decision.decision_source!r}: "
                    "se o ML está disponível, a decisão declarada deve ser do ML."
                )
        else:
            if decision.decision_source == DECISION_SOURCE_ML:
                raise ValueError(
                    "decision_source=ML com ML INDISPONÍVEL: "
                    "proibido esconder a ausência do ML na decisão."
                )
            if decision.decision_source == DECISION_SOURCE_HEURISTIC_FALLBACK:
                if not decision.ml_unavailable_reason:
                    raise ValueError(
                        "HEURISTIC_FALLBACK exige ml_unavailable_reason explícita."
                    )
    # -- consultas de auditoria ---------------------------------------------
    @property
    def total(self) -> int:
        return len(self.entries)

    def ml_decisions(self) -> list[Decision]:
        return [d for d in self.entries if d.decision_source == DECISION_SOURCE_ML]

    def fallback_decisions(self) -> list[Decision]:
        return [
            d for d in self.entries
            if d.decision_source == DECISION_SOURCE_HEURISTIC_FALLBACK
        ]

    def blocked_decisions(self) -> list[Decision]:
        return [d for d in self.entries if d.decision_source == DECISION_SOURCE_BLOCKED]

    def fallback_ratio(self) -> float:
        """Fração de decisões tomadas em contingência (heurística)."""
        if not self.entries:
            return 0.0
        return len(self.fallback_decisions()) / len(self.entries)

    def assert_no_hidden_ml_absence(self) -> None:
        """Reafirma o invariante em todo o log (defesa em profundidade)."""
        for d in self.entries:
            self._check_one(d)

    @staticmethod
    def _check_one(d: Decision) -> None:
        if not d.ml_available and d.decision_source == DECISION_SOURCE_ML:
            raise AssertionError(
                "Auditoria: entrada declara decision_source=ML com ML indisponível."
            )
        if d.decision_source == DECISION_SOURCE_HEURISTIC_FALLBACK and not d.ml_unavailable_reason:
            raise AssertionError(
                "Auditoria: HEURISTIC_FALLBACK sem motivo de indisponibilidade do ML."
            )
