"""Testes da agregação de exposição (E_agg, R_agg, E_net)."""

from decimal import Decimal

import pytest

from exposure import (
    Direction,
    Position,
    aggregate_exposure,
    nominal_exposure,
    risk_at_stop,
)


def pos_win() -> Position:
    # E = 10*150000*0.20 = 300000 ; R = 200*10*0.20 = 400
    return Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")


def pos_loss() -> Position:
    # E = 5*5.00*0.50 = 12.50 ; R = 0.05*5*0.50 = 0.125
    return Position("DOL", Direction.LOSS, 5, "5.00", "0.50", stop="5.05")


class TestAggregate:
    def test_totals(self):
        p1, p2 = pos_win(), pos_loss()
        agg = aggregate_exposure([p1, p2])
        assert agg.total_nominal_exposure == Decimal("300000") + Decimal("12.5")
        assert agg.total_max_risk == Decimal("400") + Decimal("0.125")
        assert agg.net_exposure == Decimal("300000") - Decimal("12.5")
        assert agg.net_by_symbol["WIN"] == Decimal("300000")
        assert agg.net_by_symbol["DOL"] == Decimal("-12.5")
        assert agg.positions_without_stop == 0

    def test_totals_equal_independent_sums(self):
        """R_agg e E_agg devem coincidir com a soma independente dos individuais."""
        p1, p2 = pos_win(), pos_loss()
        agg = aggregate_exposure([p1, p2])
        assert agg.total_max_risk == risk_at_stop(p1) + risk_at_stop(p2)
        assert agg.total_nominal_exposure == nominal_exposure(p1) + nominal_exposure(p2)

    def test_hedged_symbol_nets_to_zero_but_gross_doubles(self):
        """Hedge perfeito: líquido zera, bruto soma, pior caso soma os riscos."""
        long_ = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        short_ = Position("WIN", Direction.LOSS, 10, "150000", "0.20", stop="150200")
        agg = aggregate_exposure([long_, short_])
        assert agg.net_by_symbol["WIN"] == Decimal("0")
        assert agg.net_exposure == Decimal("0")
        assert agg.total_nominal_exposure == Decimal("600000")  # gross
        assert agg.total_max_risk == Decimal("800")            # 400 + 400

    def test_position_without_stop_excluded_from_risk(self):
        p1 = pos_win()
        p2 = Position("WIN", Direction.WIN, 3, "100", "0.10")  # sem stop
        agg = aggregate_exposure([p1, p2])
        assert agg.positions_without_stop == 1
        assert agg.total_max_risk == Decimal("400")  # somente p1
        assert agg.metrics[1].risk_at_stop is None
        # mas a exposição nominal continua contando a posição sem stop
        assert agg.total_nominal_exposure == Decimal("300000") + Decimal("30")

    def test_empty_portfolio(self):
        agg = aggregate_exposure([])
        assert agg.total_nominal_exposure == Decimal("0")
        assert agg.total_max_risk == Decimal("0")
        assert agg.net_exposure == Decimal("0")
        assert agg.net_by_symbol == {}
        assert agg.risk_to_nominal_ratio == Decimal("0")

    def test_risk_to_nominal_ratio(self):
        agg = aggregate_exposure([pos_win()])
        assert agg.risk_to_nominal_ratio == Decimal("400") / Decimal("300000")

    def test_metrics_have_all_fields(self):
        agg = aggregate_exposure([pos_win()])
        m = agg.metrics[0]
        assert m.asset == "WIN"
        assert m.nominal_exposure == Decimal("300000")
        assert m.signed_notional == Decimal("300000")
        assert m.risk_at_stop == Decimal("400")
        assert m.max_profit_at_target is None  # pos_win() não tem alvo
