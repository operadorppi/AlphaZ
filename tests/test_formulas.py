"""Testes matemáticos independentes das fórmulas (exposição x risco).

Exemplo canônico (posição WIN):
    N = 10 contratos, P = 150.000 pontos, V = R$ 0,20/ponto
    S = 149.800 (stop, 200 pts abaixo), T = 150.400 (alvo, 400 pts acima)

Resultados esperados:
    E = N*P*V            = 300.000
    R = d_stop*N*V       = 400
    L = d_target*N*V     = 800
    R/E = 200/150000, L/R = 2
"""

from decimal import Decimal

import pytest

from exposure import (
    Direction,
    Position,
    exposure_risk_ratio,
    max_profit_at_target,
    nominal_exposure,
    points_to_stop,
    points_to_target,
    risk_at_stop,
    risk_reward_ratio,
    stop_price_from_distance,
    stop_price_from_risk,
)

# Posição A: WIN (long)
A = Position(
    "WIN", Direction.WIN, 10, "150000", "0.20",
    stop="149800", target="150400",
)
# Posição B: LOSS (short) espelhada — mesmos números, direção oposta
B = Position(
    "WIN", Direction.LOSS, 10, "150000", "0.20",
    stop="150200", target="149600",
)


class TestExposureNominal:
    def test_example_value(self):
        # E = N * P * V = 10 * 150000 * 0.20
        assert nominal_exposure(A) == Decimal("300000")

    def test_is_not_tp_plus_sl(self):
        """Correção FASE 7 P1: exposição NÃO é a faixa (d_target + d_stop)."""
        faixa_pontos = points_to_target(A) + points_to_stop(A)  # 400 + 200 = 600
        assert faixa_pontos == Decimal("600")
        faixa_em_moeda = faixa_pontos * A.quantity * A.point_value  # 1200
        assert faixa_em_moeda == Decimal("1200")
        # O conceito antigo (TP+SL) conflita com a exposição correta:
        assert nominal_exposure(A) != faixa_pontos      # dimensões diferentes
        assert nominal_exposure(A) != faixa_em_moeda    # valores diferentes
        assert nominal_exposure(A) != Decimal("1200")

    def test_is_independent_of_stop_and_target(self):
        p1 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="140000")
        p2 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149999")
        assert nominal_exposure(p1) == nominal_exposure(p2) == Decimal("300000")

    def test_is_linear_in_quantity(self):
        p2x = Position("WIN", Direction.WIN, 20, "150000", "0.20")
        assert nominal_exposure(p2x) == 2 * nominal_exposure(A)


class TestRiskAtStop:
    def test_example_values(self):
        # d_stop = P - S = 200 ;  R = d_stop * N * V = 200 * 10 * 0.20
        assert points_to_stop(A) == Decimal("200")
        assert risk_at_stop(A) == Decimal("400")

    def test_win_loss_symmetry(self):
        """Mesma distância, direção oposta → mesmo risco em valor."""
        assert points_to_stop(B) == Decimal("200")
        assert risk_at_stop(B) == risk_at_stop(A) == Decimal("400")

    def test_exact_identity_without_division(self):
        """Identidade exata: R * 750 == E (pois d_stop/P = 200/150000 = 1/750)."""
        assert risk_at_stop(A) * Decimal(750) == nominal_exposure(A)

    def test_is_linear_in_quantity(self):
        p2x = Position("WIN", Direction.WIN, 20, "150000", "0.20", stop="149800")
        assert risk_at_stop(p2x) == 2 * risk_at_stop(A)

    def test_no_stop_raises(self):
        p = Position("WIN", Direction.WIN, 10, "150000", "0.20")
        with pytest.raises(ValueError, match="sem stop"):
            risk_at_stop(p)


class TestProfitAtTarget:
    def test_example_values(self):
        # d_target = T - P = 400 ;  L = 400 * 10 * 0.20
        assert points_to_target(A) == Decimal("400")
        assert max_profit_at_target(A) == Decimal("800")

    def test_win_loss_symmetry(self):
        assert max_profit_at_target(B) == max_profit_at_target(A) == Decimal("800")

    def test_risk_reward_ratio(self):
        assert risk_reward_ratio(A) == Decimal("2")  # L/R = 800/400

    def test_no_target_returns_none(self):
        p = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        assert max_profit_at_target(p) is None
        assert risk_reward_ratio(p) is None


class TestRatios:
    def test_exposure_risk_ratio(self):
        """R/E = d_stop/P = 200/150000 = 1/750; identidade exata: E/750 == R."""
        assert exposure_risk_ratio(A) is not None
        assert nominal_exposure(A) / Decimal("750") == risk_at_stop(A)

    def test_ratio_none_without_stop(self):
        p = Position("WIN", Direction.WIN, 1, "100", "0.01")
        assert exposure_risk_ratio(p) is None


class TestStopBuilders:
    def test_stop_from_distance_win(self):
        assert stop_price_from_distance(A, Decimal("200")) == Decimal("149800")

    def test_stop_from_distance_loss(self):
        assert stop_price_from_distance(B, Decimal("200")) == Decimal("150200")

    def test_stop_from_risk_roundtrip(self):
        """Stop a R$ 400 de risco deve reproduzir o stop original (200 pts)."""
        assert stop_price_from_risk(A, Decimal("400")) == Decimal("149800")
        assert stop_price_from_risk(B, Decimal("400")) == Decimal("150200")

    def test_stop_from_risk_invalid_amount(self):
        with pytest.raises(ValueError, match="risk_amount"):
            stop_price_from_risk(A, Decimal("0"))

    def test_stop_from_distance_invalid(self):
        with pytest.raises(ValueError, match="distance_points"):
            stop_price_from_distance(A, Decimal("-1"))

    def test_with_stop_risk_method(self):
        p = Position("WIN", Direction.WIN, 10, "150000", "0.20")
        p2 = p.with_stop_risk("100")
        # d = 100 / (10 * 0.20) = 50 pts → stop = 149950
        assert p2.stop == Decimal("149950")
        assert risk_at_stop(p2) == Decimal("100")

    def test_with_stop_distance_method(self):
        p = Position("DOL", Direction.LOSS, 2, "5.50", "0.10")
        p2 = p.with_stop_distance("0.05")
        assert p2.stop == Decimal("5.55")
        assert risk_at_stop(p2) == Decimal("0.01")  # 0.05 * 2 * 0.10
