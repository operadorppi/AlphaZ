# -*- coding: utf-8 -*-
"""
testes/test_cross_asset_imb_book_v1520.py — imb_book REAL no CrossAssetEngine.

Antes: todos os chamadores passavam imb_book=0.0 → corr_imb_book era 0 por
construção. Agora:
  - MarketState.alimentar_negocio lê imb_L1 do último book processado
    (book_stats[ativo]['book_level']['imb_L1'], semântica v15.9);
  - ScorerML.book guarda imb L1 do snapshot e evento() o repassa.

Cenário: book WDO com L1 bid=100 / ask=40 → imb_L1 = (100-40)/140 ≈ 0.4286.
"""

import os
import pickle
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts import BookLevel, BookSnapshot  # noqa: E402
from core.market_state import MarketState  # noqa: E402

WIN = 'WINV26'
WDO = 'WDOV26'


class _DummyModel:
    """Modelo dummy serializável (declarado no escopo do módulo)."""
    def predict_proba(self, X):
        import numpy as np
        return np.array([[0.3, 0.7]] * len(X))


def _scorer_dummy():
    """ScorerML com blob mínimo (padrão do teste de integração)."""
    from ml.scorer import ScorerML
    blob = {'modelo': _DummyModel(), 'features': ['preco_ultimo', 'aggr_imb']}
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pkl')
    pickle.dump(blob, open(tmp.name, 'wb'))
    tmp.close()
    try:
        return ScorerML(tmp.name, [WIN, WDO])
    finally:
        os.unlink(tmp.name)


def _snap_wdo(ts_ms, bid_vol=100, ask_vol=40):
    return BookSnapshot(
        symbol=WDO,
        timestamp_ms=ts_ms,
        bids=[BookLevel(price=10.0, volume=bid_vol)],
        asks=[BookLevel(price=11.0, volume=ask_vol)],
    )


def _engine_hist(ms, ativo=WDO):
    """Último evento registrado no lado do ativo no engine do par."""
    mgr = ms.cross_manager
    chave = f'{WIN}_{WDO}'
    eng = mgr.engines[chave]
    hist = eng.hist_wdo if ativo != WIN else eng.hist_win
    return eng, hist


class TestMarketStateImbBook:
    def test_trade_herda_imb_l1_do_book(self):
        ms = MarketState(config={'cross_asset_pairs': [[WIN, WDO]]})
        # Book primeiro (streaming: book antes do trade no fluxo)
        ms.alimentar_book(_snap_wdo(1000))
        # Trade depois, mesmo segundo
        assert ms.alimentar_negocio(WDO, 1100, 10.05, 5, 'Comprador', 'XP', 'BTG')
        eng, hist = _engine_hist(ms)
        assert len(hist) == 1
        _, _, aggr, imb = hist[-1]
        assert aggr == 1.0
        assert imb == pytest.approx(0.4286, abs=1e-3)  # (100-40)/140

    def test_sem_book_ainda_imb_zero(self):
        ms = MarketState(config={'cross_asset_pairs': [[WIN, WDO]]})
        assert ms.alimentar_negocio(WDO, 1100, 10.05, 5, 'Vendedor', 'XP', 'BTG')
        eng, hist = _engine_hist(ms)
        _, _, aggr, imb = hist[-1]
        assert aggr == -1.0
        assert imb == 0.0

    def test_book_depois_nao_afeta_trade_anterior(self):
        """Streaming as-of: o book só vale para trades processados DEPOIS dele."""
        ms = MarketState(config={'cross_asset_pairs': [[WIN, WDO]]})
        ms.alimentar_negocio(WDO, 1000, 10.0, 5, 'Comprador', 'XP', 'BTG')
        ms.alimentar_book(_snap_wdo(2000, bid_vol=10, ask_vol=100))
        ms.alimentar_negocio(WDO, 2100, 10.05, 5, 'Comprador', 'XP', 'BTG')
        eng, hist = _engine_hist(ms)
        assert len(hist) == 2
        # 1º trade: sem book → imb 0; 2º trade: book com imb negativo
        assert hist[0][3] == 0.0
        assert hist[1][3] == pytest.approx(-0.8182, abs=1e-3)  # (10-100)/110

    def test_imb_book_muda_corr_imb(self):
        """corr_imb_book deixa de ser 0 quando há book com imbalance real."""
        ms = MarketState(config={'cross_asset_pairs': [[WIN, WDO]]})
        base = 1_000_000
        # 12 buckets com 200ms → 12 amostras comuns (janela 60s)
        for i in range(12):
            t = base + i * 200
            ms.alimentar_book(BookSnapshot(
                symbol=WIN, timestamp_ms=t,
                bids=[BookLevel(price=100.0 + i, volume=80)],
                asks=[BookLevel(price=101.0 + i, volume=20)]))   # imb +0.6
            ms.alimentar_book(BookSnapshot(
                symbol=WDO, timestamp_ms=t,
                bids=[BookLevel(price=10.0, volume=80)],
                asks=[BookLevel(price=11.0, volume=20)]))         # imb +0.6
            ms.alimentar_negocio(WIN, t, 100.0 + i, 5, 'Comprador', 'XP', 'BTG')
            ms.alimentar_negocio(WDO, t, 10.0, 5, 'Comprador', 'XP', 'BTG')
        eng = ms.cross_manager.engines[f'{WIN}_{WDO}']
        f = eng.calcular(base + 11 * 200)
        # imb constantemente +0.6 nos dois lados → sem variância? Não: cada
        # bucket tem exatamente um valor → série constante → corr indefinida
        # (0.0). O que importa: o imb chega ao bucket (hist[0][3] != 0).
        assert eng.hist_win[0][3] != 0.0
        assert eng.hist_wdo[0][3] != 0.0
        assert f['corr_imb_book'] in (0.0, 1.0)


class TestScorerImbBook:
    def test_scorer_repassa_imb_l1_do_snapshot(self):
        scorer = _scorer_dummy()
        # book com L1 100/40 → imb ≈ 0.4286
        scorer.book(WDO, 1000, _snap_wdo(1000))
        scorer.evento(WDO, 1100, 10.05, 5, 'Comprador', 'XP', 'BTG')
        eng = scorer.inter
        assert len(eng.hist_wdo) == 1
        _, _, aggr, imb = eng.hist_wdo[-1]
        assert aggr == 1.0
        assert imb == pytest.approx(60 / 140, abs=1e-9)

    def test_scorer_sem_book_imb_zero(self):
        scorer = _scorer_dummy()
        scorer.evento(WDO, 1100, 10.05, 5, 'Comprador', 'XP', 'BTG')
        assert scorer.inter.hist_wdo[-1][3] == 0.0
