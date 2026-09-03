# -*- coding: utf-8 -*-
"""
testes/test_scorer_estado_v1525.py — Separacao prob x status (P1-A30).

ANTES: qualquer falha de inferencia (predict, cobertura, ECE) retornava 0.5 e
o consumidor nao conseguia distinguir "modelo neutro" de "modelo falhou".
O 0.5 de ERRO podia chegar ao gate ML como probabilidade valida (bloquear ou
liberar trade com base em silencio).

AGORA (v15.25):
  - ScorerML expoe self.status[ativo] ('OK'/'NAO_INFERIDO'/'MODEL_ERROR'/
    'ECE_ALTO') e obter_estado() -> (prob | None, status); MODEL_ERROR -> None;
  - o gate do SignalEngine trata MODEL_ERROR como "ML nao fala" (heuristica
    pura decide, motivo 'ML_ERRO' explicito) — nunca alimenta a calibration
    com 0.5 de erro; ECE_ALTO vira neutro POLITICO explicito ('ML_BLOCK').
"""

import json
import os
import pickle
import tempfile

import numpy as np
import pytest

from core.market_state import MarketState
from core.signal_engine import SignalEngine
from ml.scorer import ScorerML

WIN = 'WINV26'


class _DummyModel:
    """Modelo dummy: predict_proba controlado por parametro raise_flag."""
    def __init__(self, levantar=False):
        self._levantar = levantar

    def predict_proba(self, X):
        if self._levantar:
            raise RuntimeError('falha simulada no predict')
        return np.array([[0.3, 0.7]] * len(X))


def _fazer_modelo(tmpdir, features, levantar=False):
    pkl = os.path.join(tmpdir, 'modelo.pkl')
    with open(pkl, 'wb') as f:
        pickle.dump({'modelo': _DummyModel(levantar=levantar),
                     'features': features}, f)
    return pkl


def _snap(**extra):
    base = {'ativo': WIN, 'ts_ms': 1_770_000_000_000, 'preco_ultimo': 100000.0,
            'aggr_imb': 1.0}
    base.update(extra)
    return base


# ======================================================================
#  1. ScorerML: status da inferencia
# ======================================================================

class TestStatusScorer:
    def test_status_inicial_nao_inferido(self):
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(d, ['preco_ultimo', 'aggr_imb']),
                              [WIN])
            assert scorer.status[WIN] == 'NAO_INFERIDO'
            p, st = scorer.obter_estado(WIN)
            assert st == 'NAO_INFERIDO'
            assert p == 0.5  # neutro de conveniencia, status explicita

    def test_inferencia_valida_status_ok(self):
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(d, ['preco_ultimo', 'aggr_imb']),
                              [WIN])
            p = scorer._prever(_snap())
            assert p == pytest.approx(0.7)
            assert scorer.status[WIN] == 'OK'
            prob, st = scorer.obter_estado(WIN)
            assert st == 'OK'
            assert prob == pytest.approx(0.7)

    def test_cobertura_falha_status_model_error(self):
        """A29 + A30: feature ausente -> inferencia NAO roda -> MODEL_ERROR,
        e obter_estado devolve prob=None (nunca 0.5 de erro como valido)."""
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(
                d, ['preco_ultimo', 'aggr_imb', 'feature_inexistente']), [WIN])
            p = scorer._prever(_snap())
            assert p == 0.5  # neutro de seguranca (nao derruba o processo)
            assert scorer.status[WIN] == 'MODEL_ERROR'
            prob, st = scorer.obter_estado(WIN)
            assert prob is None
            assert st == 'MODEL_ERROR'

    def test_predict_exception_status_model_error(self):
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(d, ['preco_ultimo', 'aggr_imb'],
                                            levantar=True), [WIN])
            p = scorer._prever(_snap())
            assert p == 0.5
            assert scorer.status[WIN] == 'MODEL_ERROR'
            prob, st = scorer.obter_estado(WIN)
            assert prob is None
            assert st == 'MODEL_ERROR'

    def test_ece_alto_status_proprio(self):
        """ECE alto NAO e erro de modelo: neutro POLITICO, status proprio."""
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(d, ['preco_ultimo', 'aggr_imb']),
                              [WIN])
            scorer._ece = 0.30
            p = scorer._prever(_snap())
            assert p == 0.5
            assert scorer.status[WIN] == 'ECE_ALTO'
            prob, st = scorer.obter_estado(WIN)
            assert st == 'ECE_ALTO'
            assert prob == pytest.approx(0.5)  # neutro politico, prob valida

    def test_estado_salud_expoe_status(self):
        with tempfile.TemporaryDirectory() as d:
            scorer = ScorerML(_fazer_modelo(
                d, ['preco_ultimo', 'aggr_imb', 'feature_inexistente']), [WIN])
            scorer._prever(_snap())
            salud = scorer.estado_salud()
            assert salud['status'][WIN] == 'MODEL_ERROR'


# ======================================================================
#  2. Gate do SignalEngine: erro de modelo NAO vira 0.5 valido
# ======================================================================

class _ScorerStub:
    """Stub do scorer com prob/status controlados (o engine so le esses)."""
    def __init__(self, prob, status):
        self.prob = prob
        self.status = status


def _motor_com_sinal(scorer_stub):
    """Alimenta WIN por 2 segundos e devolve o ultimo Signal gerado."""
    ms = MarketState(config={'book_split': 30})
    se = SignalEngine(ms, config={})
    se.scorer = scorer_stub
    S = 1_787_000_000
    for i in range(3):
        ms.alimentar_negocio(ativo=WIN, ts_ms=S * 1000 + i * 200,
                             preco=170000.0 + i, qtd=1,
                             agressor='Comprador', compradora='XP',
                             vendedora='BTG')
        se.calcular(S)
    # segundo seguinte fecha o S com a feature consolidada e avalia
    ms.alimentar_negocio(ativo=WIN, ts_ms=(S + 1) * 1000, preco=170010.0,
                         qtd=1, agressor='Comprador', compradora='XP',
                         vendedora='BTG')
    se.calcular(S + 1)
    assert WIN in se.sinais, 'nenhum Signal gerado (fixture quebrada)'
    return se.sinais[WIN]


def test_model_error_cai_na_heuristica_com_motivo_explicito():
    """MODEL_ERROR + prob 0.5 (fallback): o gate NAO trata 0.5 como valido —
    sinal carrega 'ML_ERRO' e NUNCA 'ML_BLOCK/ML_DIR' (calibration nao roda)."""
    stub = _ScorerStub(prob={WIN: 0.5}, status={WIN: 'MODEL_ERROR'})
    sig = _motor_com_sinal(stub)
    assert any('ML_ERRO' in m for m in sig.motivos), sig.motivos
    assert not any('ML_BLOCK' in m or 'ML_DIR' in m for m in sig.motivos), \
        sig.motivos

    # ECE_ALTO: neutro POLITICO — nao e ML_ERRO, bloqueia com motivo proprio
    stub2 = _ScorerStub(prob={WIN: 0.5}, status={WIN: 'ECE_ALTO'})
    sig2 = _motor_com_sinal(stub2)
    assert any('ML_BLOCK' in m for m in sig2.motivos), sig2.motivos
    assert not any('ML_ERRO' in m for m in sig2.motivos), sig2.motivos


def test_status_ok_0p5_e_probabilidade_valida():
    """OK com prob 0.5 legitima: o gate roda a calibration normalmente
    (ML_BLOCK por zona de incerteza ou ML_DIR) — sem ML_ERRO."""
    stub = _ScorerStub(prob={WIN: 0.5}, status={WIN: 'OK'})
    sig = _motor_com_sinal(stub)
    assert not any('ML_ERRO' in m for m in sig.motivos), sig.motivos
    assert any(m.startswith('ML_DIR') or m.startswith('ML_BLOCK')
               for m in sig.motivos), sig.motivos
