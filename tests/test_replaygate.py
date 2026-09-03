"""FASE 9 P1 — testes do Replay Gate e das políticas por ambiente."""

import pytest

from mlgate import (  # noqa: E402
    DECISION_SOURCE_BLOCKED,
    DECISION_SOURCE_HEURISTIC_FALLBACK,
    DECISION_SOURCE_ML,
    DEVELOPMENT_POLICY,
    MlAvailability,
    MlGatePolicy,
    MLDecisionLog,
    PRODUCTION_POLICY,
    Decision,
    evaluate_gate,
)
from replaygate import (  # noqa: E402
    DEVELOPMENT_ENV_POLICY,
    ENVIRONMENT_POLICIES,
    PAPER_ENV_POLICY,
    PRODUCTION_ENV_POLICY,
    Environment,
    EnvironmentPolicy,
    GateDecision,
    GateDecisionLog,
    ReplayStatus,
    environment_policy,
    evaluate_replay_gate,
)

ML_UP = MlAvailability.up()
ML_DOWN = MlAvailability.down("timeout no servico de scoring")
REPLAY_OK = ReplayStatus.validated_()
REPLAY_NO = ReplayStatus.pending("replay pendente da v2.3 da estrategia")


class TestReplayStatus:
    def test_pending_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            ReplayStatus(validated=False)

    def test_pending_empty_reason_rejected(self):
        with pytest.raises(ValueError, match="reason"):
            ReplayStatus(validated=False, reason="   ")

    def test_validated_without_reason_ok(self):
        assert REPLAY_OK.validated is True

    def test_non_bool_rejected(self):
        with pytest.raises(TypeError, match="validated"):
            ReplayStatus(validated=1)


class TestEnvironmentPresets:
    def test_every_environment_has_explicit_policy(self):
        assert set(ENVIRONMENT_POLICIES.keys()) == set(Environment)
        for env, pol in ENVIRONMENT_POLICIES.items():
            assert pol.environment is env
            assert isinstance(pol.ml, MlGatePolicy)
            assert isinstance(pol.require_replay_validated, bool)
            assert pol.label

    def test_production_requires_validated_replay(self):
        """FASE 9 P1: política de produção exige replay validado."""
        assert PRODUCTION_ENV_POLICY.require_replay_validated is True
        assert PRODUCTION_ENV_POLICY.ml_required is True
        assert PRODUCTION_ENV_POLICY.fallback_enabled is False

    def test_development_does_not_block_on_replay(self):
        assert DEVELOPMENT_ENV_POLICY.require_replay_validated is False
        assert DEVELOPMENT_ENV_POLICY.ml_required is False
        assert DEVELOPMENT_ENV_POLICY.fallback_enabled is True

    def test_paper_policy_explicit(self):
        assert PAPER_ENV_POLICY.environment is Environment.PAPER
        assert PAPER_ENV_POLICY.require_replay_validated is False
        assert PAPER_ENV_POLICY.ml_required is True

    def test_environment_policy_lookup(self):
        assert environment_policy(Environment.PRODUCTION) is PRODUCTION_ENV_POLICY
        assert environment_policy(Environment.PAPER) is PAPER_ENV_POLICY
        assert environment_policy(Environment.DEVELOPMENT) is DEVELOPMENT_ENV_POLICY


class TestPolicyConstruction:
    def test_invalid_environment_rejected(self):
        with pytest.raises(TypeError, match="environment"):
            EnvironmentPolicy("PRODUCTION", MlGatePolicy(True, False), True)

    def test_invalid_ml_policy_rejected(self):
        with pytest.raises(TypeError, match="ml"):
            EnvironmentPolicy(Environment.PAPER, "ml", True)

    def test_non_bool_replay_flag_rejected(self):
        with pytest.raises(TypeError, match="require_replay_validated"):
            EnvironmentPolicy(
                Environment.PRODUCTION,
                MlGatePolicy(ml_required=True, fallback_enabled=False),
                require_replay_validated=1,
            )

    def test_default_label_from_environment(self):
        pol = EnvironmentPolicy(
            Environment.PAPER,
            MlGatePolicy(ml_required=True, fallback_enabled=False),
            True,
        )
        assert pol.label == "paper"

    def test_custom_paper_strict_policy(self):
        """Ambiente paper pode exigir replay se o time decidir (explícito)."""
        strict = EnvironmentPolicy(
            Environment.PAPER,
            MlGatePolicy(ml_required=True, fallback_enabled=False),
            require_replay_validated=True,
            label="paper-strict",
        )
        assert strict.require_replay_validated is True


class TestTruthTable:
    """Tabela-verdade do gate completo (replay + ML) por ambiente."""

    # ---- PRODUCTION -----------------------------------------------------
    def test_production_blocks_when_replay_missing(self):
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, PRODUCTION_ENV_POLICY)
        assert d.allowed is False
        assert d.decision_source == "BLOCKED"
        assert d.replay_validated is False
        assert d.replay_reason == "replay pendente da v2.3 da estrategia"
        assert d.environment is Environment.PRODUCTION

    def test_production_blocks_even_if_ml_up(self):
        """Replay pendente bloqueia mesmo com ML perfeitamente disponível."""
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, PRODUCTION_ENV_POLICY)
        assert d.allowed is False

    def test_production_allows_when_replay_validated_and_ml_up(self):
        d = evaluate_replay_gate(ML_UP, REPLAY_OK, PRODUCTION_ENV_POLICY)
        assert d.allowed is True
        assert d.decision_source == "ML"
        assert d.replay_validated is True
        assert d.used_ml is True

    def test_production_ml_down_blocks_via_ml_rule(self):
        """Replay OK, mas ML fora → bloqueado pela regra da FASE 8."""
        d = evaluate_replay_gate(ML_DOWN, REPLAY_OK, PRODUCTION_ENV_POLICY)
        assert d.allowed is False
        assert d.decision_source == "BLOCKED"
        assert d.ml_unavailable_reason == "timeout no servico de scoring"

    def test_production_replay_block_precedes_ml_gate(self):
        """Replay pendente: o gate de ML nem é consultado (heuristic não chamada)."""
        calls = []
        d = evaluate_replay_gate(
            ML_DOWN, REPLAY_NO, PRODUCTION_ENV_POLICY,
            heuristic_decision=lambda: calls.append(1) or True,
        )
        assert d.allowed is False
        assert calls == []  # heurística não foi consultada

    # ---- DEVELOPMENT ----------------------------------------------------
    def test_development_operates_without_replay(self):
        """Dev: replay é informativo — nunca bloqueia."""
        d = evaluate_replay_gate(ML_DOWN, REPLAY_NO, DEVELOPMENT_ENV_POLICY,
                                 heuristic_decision=lambda: True)
        assert d.allowed is True
        assert d.decision_source == "HEURISTIC_FALLBACK"
        assert d.replay_validated is False
        assert d.replay_reason == REPLAY_NO.reason
        assert "replay pendente" in d.note

    def test_development_ml_up_replay_pending(self):
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, DEVELOPMENT_ENV_POLICY)
        assert d.allowed is True
        assert d.decision_source == "ML"
        assert d.replay_validated is False

    # ---- PAPER ----------------------------------------------------------
    def test_paper_operates_without_replay_by_default(self):
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, PAPER_ENV_POLICY)
        assert d.allowed is True
        assert d.decision_source == "ML"

    def test_paper_strict_blocks_without_replay(self):
        strict = EnvironmentPolicy(
            Environment.PAPER,
            MlGatePolicy(ml_required=True, fallback_enabled=False, label="paper-strict"),
            require_replay_validated=True,
            label="paper-strict",
        )
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, strict)
        assert d.allowed is False
        assert d.decision_source == "BLOCKED"

    # ---- matrix resumida --------------------------------------------------
    @pytest.mark.parametrize(
        ("ml", "replay", "env", "expected_allowed", "expected_source"),
        [
            (ML_UP, REPLAY_OK, "PRODUCTION", True, "ML"),
            (ML_DOWN, REPLAY_OK, "PRODUCTION", False, "BLOCKED"),
            (ML_UP, REPLAY_NO, "PRODUCTION", False, "BLOCKED"),
            (ML_DOWN, REPLAY_NO, "PRODUCTION", False, "BLOCKED"),
            (ML_UP, REPLAY_OK, "PAPER", True, "ML"),
            (ML_DOWN, REPLAY_OK, "PAPER", False, "BLOCKED"),
            (ML_UP, REPLAY_NO, "PAPER", True, "ML"),
            (ML_UP, REPLAY_OK, "DEVELOPMENT", True, "ML"),
            (ML_DOWN, REPLAY_OK, "DEVELOPMENT", True, "HEURISTIC_FALLBACK"),
        ],
    )
    def test_full_matrix(self, ml, replay, env, expected_allowed, expected_source):
        pol = environment_policy(Environment(env))
        heur = lambda: True  # noqa: E731
        d = evaluate_replay_gate(ml, replay, pol, heuristic_decision=heur)
        assert d.allowed is expected_allowed
        assert d.decision_source == expected_source


class TestNoSilenceInvariants:
    def test_gate_decision_carries_replay_state_always(self):
        for ml in (ML_UP, ML_DOWN):
            for replay in (REPLAY_OK, REPLAY_NO):
                d = evaluate_replay_gate(ml, replay, PRODUCTION_ENV_POLICY,
                                         heuristic_decision=lambda: True)
                assert d.replay_validated is replay.validated
                if not replay.validated:
                    assert d.replay_reason  # motivo explícito, nunca nulo
                assert d.environment is Environment.PRODUCTION

    def test_ml_source_never_when_ml_down(self):
        d = evaluate_replay_gate(ML_DOWN, REPLAY_OK, PAPER_ENV_POLICY)
        assert d.decision_source != "ML"


class TestGateDecisionLog:
    def test_rejects_production_ml_source_with_replay_missing(self):
        log = GateDecisionLog()
        d = evaluate_replay_gate(ML_UP, REPLAY_NO, PRODUCTION_ENV_POLICY)
        # o gate devolve BLOCKED; forçamos uma entrada "falsa" para auditar
        fake = GateDecision(
            allowed=True,
            decision_source="ML",
            ml_available=True,
            ml_unavailable_reason=None,
            replay_validated=False,
            replay_reason=REPLAY_NO.reason,
            heuristic_decision=None,
            environment=Environment.PRODUCTION,
            policy_label="production",
            note="tentativa de operar produção sem replay validado",
        )
        with pytest.raises(ValueError, match="replay_validated=False"):
            log.record(fake)

    def test_valid_production_entries(self):
        log = GateDecisionLog()
        log.record(evaluate_replay_gate(ML_UP, REPLAY_OK, PRODUCTION_ENV_POLICY))
        log.record(evaluate_replay_gate(ML_DOWN, REPLAY_OK, PRODUCTION_ENV_POLICY))
        log.record(evaluate_replay_gate(ML_UP, REPLAY_NO, PRODUCTION_ENV_POLICY))
        assert log.total == 3
        assert len(log.ml_decisions()) == 1
        assert len(log.blocked_decisions()) == 2
        log.assert_no_hidden_ml_absence()
        log.assert_no_hidden_replay_absence()

    def test_development_replay_pending_is_allowed_in_log(self):
        """Dev: replay pendente não é violação (informativo)."""
        log = GateDecisionLog()
        log.record(evaluate_replay_gate(ML_UP, REPLAY_NO, DEVELOPMENT_ENV_POLICY))
        assert log.total == 1
        log.assert_no_hidden_replay_absence()  # nada de PRODUCTION → OK

    def test_fallback_entries_still_validate_ml_rules(self):
        log = GateDecisionLog()
        log.record(evaluate_replay_gate(ML_DOWN, REPLAY_NO, DEVELOPMENT_ENV_POLICY,
                                       heuristic_decision=lambda: True))
        d = log.entries[0]
        assert d.decision_source == "HEURISTIC_FALLBACK"
        assert d.ml_unavailable_reason
        log.assert_no_hidden_ml_absence()

    def test_empty_log(self):
        log = GateDecisionLog()
        assert log.total == 0
        assert log.fallback_ratio() == 0.0
        log.assert_no_hidden_ml_absence()
        log.assert_no_hidden_replay_absence()


class TestBackwardCompatibility:
    """O ambiente de desenvolvimento da FASE 8 não foi quebrado."""

    def test_mlgate_presets_unchanged(self):
        from mlgate import DEVELOPMENT_POLICY, PRODUCTION_POLICY
        assert PRODUCTION_POLICY.ml_required is True
        assert PRODUCTION_POLICY.fallback_enabled is False
        assert DEVELOPMENT_POLICY.ml_required is False
        assert DEVELOPMENT_POLICY.fallback_enabled is True

    def test_replaygate_presets_align_with_mlgate_production(self):
        """PRODUCTION_ENV_POLICY.ml é o MESMO tipo/valores da FASE 8."""
        from mlgate import PRODUCTION_POLICY
        assert PRODUCTION_ENV_POLICY.ml.ml_required == PRODUCTION_POLICY.ml_required
        assert PRODUCTION_ENV_POLICY.ml.fallback_enabled == PRODUCTION_POLICY.fallback_enabled

    def test_gate_decision_projects_to_mlgate_decision(self):
        from mlgate import MlGatePolicy
        d = evaluate_replay_gate(ML_UP, REPLAY_OK, PRODUCTION_ENV_POLICY)
        proj = d.ml_decision
        assert proj.allowed is d.allowed
        assert proj.decision_source == d.decision_source
        assert proj.policy_label == d.policy_label
