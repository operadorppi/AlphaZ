# -*- coding: utf-8 -*-
"""
testes/test_book_profundidade_v159.py — P0-A12 (v15.9).

Invariante de profundidade do BOOK: o índice do array = nível POR PREÇO.
`calcular()` trata bid_p[0]/ask_p[0] como melhor nível e L1/L3/L5 via cumsum,
portanto os níveis precisam vir ordenados best-first — independente da ordem
das linhas da janela e de buracos de volume zero.

Cenários:
  1. Nível com preço>0/volume=0 (ordem consumida) NÃO desloca a profundidade
  2. Ordem de linhas fora de ordem (janela embaralhada) é normalizada por preço
  3. Lado ask espelhado (ordenação crescente)
  4. Nível inválido (preço <= 0) nunca entra e não desloca nada
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from features.book_features import BookLevelFeatures  # noqa: E402


def _snap(bid_p, bid_v, ask_p, ask_v):
    return {'bid_preco': bid_p, 'bid_vol': bid_v,
            'ask_preco': ask_p, 'ask_vol': ask_v}


def test_nivel_volume_zero_nao_desloca_best_bid():
    """Bid: nível mais alto com volume 0 -> o melhor nível real assume índice 0."""
    blf = BookLevelFeatures()
    # melhor preço (130000.0) sem volume em repouso; níveis reais abaixo
    snap = _snap(bid_p=[130000.0, 129999.8, 129999.6],
                 bid_v=[0, 50, 30],
                 ask_p=[130000.2], ask_v=[40])
    res = blf.calcular(snap, 'WINV26', 1_787_000_000_000)

    assert res is not None
    # best_bid virou 129999.8; spread = 130000.2 - 129999.8 = 0.4
    assert res['spread'] == pytest.approx(0.4), f"spread={res['spread']} — best_bid errado"
    assert res['n_bid_levels'] == 2, f"n_bid={res['n_bid_levels']} (esperado 2)"
    # imb L1 = (50-40)/(50+40), arredondado a 4 casas pelo feature
    assert res['imbalance']['L1'] == pytest.approx((50 - 40) / 90.0, abs=1e-4)


def test_ordem_de_linhas_embaralhada_normalizada_por_preco():
    """Mesmo livro em ordem diferente de janela -> mesmas features."""
    blf = BookLevelFeatures()
    snap_desordenado = _snap(bid_p=[129999.6, 130000.0, 129999.8],
                             bid_v=[30, 0, 50],
                             ask_p=[130000.4, 130000.2], ask_v=[20, 40])
    snap_ordenado = _snap(bid_p=[130000.0, 129999.8, 129999.6],
                          bid_v=[0, 50, 30],
                          ask_p=[130000.2, 130000.4], ask_v=[40, 20])

    r1 = blf.calcular(snap_desordenado, 'WINV26', 1_787_000_000_000)
    r2 = blf.calcular(snap_ordenado, 'WINV26', 1_787_000_000_000)

    assert r1['spread'] == pytest.approx(0.4)
    assert r2['spread'] == pytest.approx(0.4)
    assert r1['microprice'] == r2['microprice']
    for k in r1['imbalance']:
        assert r1['imbalance'][k] == r2['imbalance'][k], f"imb {k} diverge"


def test_ask_ordenado_crescente():
    """Ask: melhor ask (menor preço) assume o índice 0 mesmo fora de ordem."""
    blf = BookLevelFeatures()
    snap = _snap(bid_p=[129999.8], bid_v=[50],
                 ask_p=[130000.4, 130000.2], ask_v=[20, 40])
    res = blf.calcular(snap, 'WINV26', 1_787_000_000_000)
    assert res is not None
    assert res['spread'] == pytest.approx(0.4), (
        f"spread={res['spread']} — melhor ask deveria ser 130000.2")


def test_preco_invalido_nao_desloca():
    """Nível com preço <= 0 é ignorado sem deslocar os níveis válidos."""
    blf = BookLevelFeatures()
    snap = _snap(bid_p=[0, 129999.8, -5], bid_v=[100, 50, 3],
                 ask_p=[130000.2], ask_v=[40])
    res = blf.calcular(snap, 'WINV26', 1_787_000_000_000)
    assert res is not None
    assert res['n_bid_levels'] == 1, f"n_bid={res['n_bid_levels']}"
    assert res['spread'] == pytest.approx(0.4)
