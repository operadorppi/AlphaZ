"""FASE 8 P1 — testes do gate de ML (tabela-verdade, invariantes e auditoria)."""

import pytest

from mlgate import (
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

ML_UP = MlAvailability.up()
ML_DOWN = MlAvailability.down("timeout no serviço de scoring")


class TestPolicyConstruction:
    def test_production_preset(self):
        assert PRODUCTION_POLICY.ml_required is True
        assert PRODUCTION_POLICY.fallback_enabled is False
        assert PRODUCTION_POLICY.label == "production"

    def test_development_preset(self):
        assert DEVELOPMENT_POLICY.ml_required is False
        assert DEVELOPMENT_POLICY.fallback_enabled is True

    def test_non_bool_rejected(self):
        with pytest.raises(TypeError, match="ml_required"):
            MlGatePolicy(ml_required=1, fallback_enabled=True)

    def test_invalid_label_rejected(self):
        with pytest.raises(ValueError, match="label"):
            MlGatePolicy(ml_required=True, fallback_enabled=False, label="  ")


class TestMlAvailability:
    def test_down_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            MlAvailability(available=False)

    def test_up_without_reason_ok(self):
        assert MlAvailability.up().available is True

    def test_down_with_empty_reason_rejected(self):
        with pytest.raises(ValueError, match="reason"):
            MlAvailability(available=False, reason="   ")


class TestTruthTable:
    """Tabela-verdade completa do gate."""

    def test_ml_available_source_is_ml(self):
        d = evaluate_gate(ML_UP, PRODUCTION_POLICY)
        assert d.decision_source == DECISION_SOURCE_ML
        assert d.allowed is True
        assert d.used_ml is True
        assert d.heuristic_decision is None

    def test_ml_available_ignores_policy_flags(self):
        """ML disponível: a decisão é do ML independentemente da política."""
        for policy in (PRODUCTION_POLICY, DEVELOPMENT_POLICY):
            d = evaluate_gate(ML_UP, policy)
            assert d.decision_source == DECISION_SOURCE_ML
            assert d.allowed is True

    def test_ml_down_required_blocks(self):
        """ML obrigatório + indisponível → allowed=False, fallback não é consultado."""
        calls = []
        d = evaluate_gate(
            ML_DOWN,
            PRODUCTION_POLICY,  # ml_required=True, fallback_enabled=False
            heuristic_decision=lambda: calls.append(1) or True,
        )
        assert d.allowed is False
        assert d.decision_source == DECISION_SOURCE_BLOCKED
        assert d.ml_available is False
        assert d.ml_unavailable_reason == "timeout no serviço de scoring"
        assert calls == []  # heurística NÃO foi consultada

    def test_ml_down_required_with_fallback_flag_still_blocks(self):
        """ml_required=True tem precedência: mesmo com fallback_enabled, bloqueia."""
        policy = MlGatePolicy(ml_required=True, fallback_enabled=True, label="hybrid")
        calls = []
        d = evaluate_gate(ML_DOWN, policy, heuristic_decision=lambda: calls.append(1) or True)
        assert d.allowed is False
        assert d.decision_source == DECISION_SOURCE_BLOCKED
        assert d.policy_label == "hybrid"
        assert calls == []

    def test_ml_down_not_required_fallback_enabled_true(self):
        """Sem ML obrigatório + fallback ON + heurística diz SIM → allowed=True."""
        d = evaluate_gate(
            ML_DOWN,
            DEVELOPMENT_POLICY,
            heuristic_decision=lambda: True,
        )
        assert d.allowed is True
        assert d.decision_source == DECISION_SOURCE_HEURISTIC_FALLBACK
        assert d.heuristic_decision is True
        assert d.ml_unavailable_reason == ML_DOWN.reason
        assert "ML NÃO participou" in d.note  # nunca esconder a ausência

    def test_ml_down_not_required_fallback_enabled_false(self):
        """Sem ML obrigatório + fallback OFF → allowed=False."""
        policy = MlGatePolicy(ml_required=False, fallback_enabled=False, label="no-fallback")
        d = evaluate_gate(ML_DOWN, policy, heuristic_decision=lambda: True)
        assert d.allowed is False
        assert d.decision_source == DECISION_SOURCE_BLOCKED
        assert d.heuristic_decision is None

    def test_fallback_enabled_but_no_heuristic_factory_raises(self):
        with pytest.raises(ValueError, match="heuristic_decision"):
            evaluate_gate(ML_DOWN, DEVELOPMENT_POLICY, heuristic_decision=None)

    def test_heuristic_factory_is_consulted_only_in_fallback_case(self):
        """A heurística só é chamada no caso 3 (ML fora, fallback ON)."""
        calls = []
        heur = lambda: calls.append(1) or True  # noqa: E731
        evaluate_gate(ML_UP, DEVELOPMENT_POLICY, heuristic_decision=heur)          # ML ok
        assert calls == []
        evaluate_gate(ML_DOWN, PRODUCTION_POLICY, heuristic_decision=heur)         # required
        assert calls == []
        evaluate_gate(ML_DOWN, DEVELOPMENT_POLICY, heuristic_decision=heur)        # fallback
        assert calls == [1]


class TestNoSilenceInvariants:
    """Invariantes de NÃO-SILÊNCIO: impossível esconder a ausência do ML."""

    def test_fallback_decision_never_claims_ml_source(self):
        policy = DEVELOPMENT_POLICY
        for value in (True, False):
            d = evaluate_gate(ML_DOWN, policy, heuristic_decision=lambda: value)
            assert d.decision_source != DECISION_SOURCE_ML
            assert d.ml_available is False
            assert d.note

    def test_blocked_decision_always_carries_reason(self):
        blocked_policies = (
            PRODUCTION_POLICY,
            MlGatePolicy(ml_required=False, fallback_enabled=False, label="no-fallback"),
        )
        for policy in blocked_policies:
            d = evaluate_gate(ML_DOWN, policy)
            assert d.decision_source == DECISION_SOURCE_BLOCKED
            assert d.ml_unavailable_reason  # motivo explícito, nunca vazio
            assert "INDISPONÍVEL" in d.note

    def test_decision_source_always_in_known_set(self):
        for ml in (ML_UP, ML_DOWN):
            for policy in (PRODUCTION_POLICY, DEVELOPMENT_POLICY):
                d = evaluate_gate(ml, policy, heuristic_decision=lambda: True)
                assert d.decision_source in (
                    DECISION_SOURCE_ML,
                    DECISION_SOURCE_HEURISTIC_FALLBACK,
                    DECISION_SOURCE_BLOCKED,
                )


class TestAuditLog:
    def test_rejects_fake_ml_source_when_ml_down(self):
        log = MLDecisionLog()
        fake = Decision(
            allowed=True,
            decision_source=DECISION_SOURCE_ML,
            ml_available=False,
            ml_unavailable_reason="x",
            heuristic_decision=None,
            policy_label="t",
            note="tentativa de esconder",
        )
        with pytest.raises(ValueError, match="proibido esconder"):
            log.record(fake)

    def test_rejects_non_ml_source_when_ml_up(self):
        log = MLDecisionLog()
        bad = Decision(
            allowed=True,
            decision_source=DECISION_SOURCE_HEURISTIC_FALLBACK,
            ml_available=True,
            ml_unavailable_reason=None,
            heuristic_decision=True,
            policy_label="t",
            note="heurística apesar do ML estar disponível",
        )
        with pytest.raises(ValueError, match="deve ser do ML"):
            log.record(bad)

    def test_fallback_entry_requires_reason(self):
        log = MLDecisionLog()
        bad = Decision(
            allowed=True,
            decision_source=DECISION_SOURCE_HEURISTIC_FALLBACK,
            ml_available=False,
            ml_unavailable_reason=None,
            heuristic_decision=True,
            policy_label="t",
            note="sem motivo",
        )
        with pytest.raises(ValueError, match="exige ml_unavailable_reason"):
            log.record(bad)

    def test_valid_entries_and_queries(self):
        log = MLDecisionLog()
        log.record(evaluate_gate(ML_UP, PRODUCTION_POLICY))
        log.record(evaluate_gate(ML_DOWN, PRODUCTION_POLICY))
        log.record(evaluate_gate(ML_DOWN, DEVELOPMENT_POLICY, heuristic_decision=lambda: True))
        assert log.total == 3
        assert len(log.ml_decisions()) == 1
        assert len(log.blocked_decisions()) == 1
        assert len(log.fallback_decisions()) == 1
        assert log.fallback_ratio() == pytest.approx(1 / 3)
        log.assert_no_hidden_ml_absence()

    def test_empty_log(self):
        log = MLDecisionLog()
        assert log.total == 0
        assert log.fallback_ratio() == 0.0
        log.assert_no_hidden_ml_absence()
