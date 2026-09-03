"""Testes de invariantes matemáticas com amostras aleatórias (sementes fixas).

Cada invariante recalcula a fórmula **de forma independente**
(a partir de N, P, V, S, T e sgn), sem reutilizar as funções do módulo.
"""

import random
from decimal import Decimal

from exposure import (
    Direction,
    Position,
    aggregate_exposure,
    max_profit_at_target,
    nominal_exposure,
    points_to_stop,
    points_to_target,
    risk_at_stop,
    signed_notional,
)


def rnd_position(rng: random.Random) -> Position:
    q = Decimal(rng.randint(1, 60))
    price = Decimal(str(round(rng.uniform(100, 200_000), 2)))
    pv = Decimal(str(round(rng.uniform(0.05, 5.0), 2)))
    # distâncias como fração do preço → stop e alvo ficam sempre > 0
    d_stop = price * Decimal(str(round(rng.uniform(0.001, 0.05), 4)))
    d_tgt = price * Decimal(str(round(rng.uniform(0.001, 0.08), 4)))
    d = rng.choice([Direction.WIN, Direction.LOSS])
    sgn = Decimal(d.sign)
    stop = price - sgn * d_stop
    target = price + sgn * d_tgt
    return Position(f"ASSET{rng.randint(0, 9)}", d, q, price, pv, stop=stop, target=target)


class TestInvariants:
    def test_single_position_invariants_300_samples(self):
        rng = random.Random(20260830)
        for _ in range(300):
            p = rnd_position(rng)
            sgn = Decimal(p.direction.sign)
            e = nominal_exposure(p)
            r = risk_at_stop(p)
            l = max_profit_at_target(p)

            # -- positividade ------------------------------------------------
            assert e > 0
            assert r > 0
            assert l > 0

            # -- recalculado independente (fórmula expandida) ---------------
            assert points_to_stop(p) == (p.price - p.stop) * sgn
            assert points_to_target(p) == (p.target - p.price) * sgn
            assert e == p.quantity * p.price * p.point_value
            assert r == (p.price - p.stop) * sgn * p.quantity * p.point_value
            assert l == (p.target - p.price) * sgn * p.quantity * p.point_value
            assert signed_notional(p) == sgn * p.quantity * p.price * p.point_value

            # -- invariante de ordenação: R <= E  <=>  d_stop <= P ---------
            assert (r <= e) == (points_to_stop(p) <= p.price)
            assert (l >= 0) == (points_to_target(p) >= 0)

            # -- exposição != risco (em geral, valores distintos) ----------
            assert r != e or p.stop == p.price - sgn * p.price  # caso degenerado

    def test_aggregate_consistency_300_samples(self):
        rng = random.Random(777)
        positions = [rnd_position(rng) for _ in range(300)]
        agg = aggregate_exposure(positions)

        # -- somas independentes -------------------------------------------
        e_sum = Decimal(0)
        r_sum = Decimal(0)
        s_sum = Decimal(0)
        by_symbol: dict[str, Decimal] = {}
        for p in positions:
            sgn = Decimal(p.direction.sign)
            e_i = p.quantity * p.price * p.point_value
            r_i = (p.price - p.stop) * sgn * p.quantity * p.point_value
            s_i = sgn * e_i
            e_sum += e_i
            r_sum += r_i
            s_sum += s_i
            by_symbol[p.asset] = by_symbol.get(p.asset, Decimal(0)) + s_i

        assert agg.total_nominal_exposure == e_sum
        assert agg.total_max_risk == r_sum
        assert agg.net_exposure == s_sum
        assert agg.net_by_symbol == by_symbol
        assert agg.positions_without_stop == 0
        assert len(agg.metrics) == 300

        # -- relações bruto / líquido ---------------------------------------
        assert abs(agg.net_exposure) <= agg.total_nominal_exposure
        assert agg.total_max_risk > 0
