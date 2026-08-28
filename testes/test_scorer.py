import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
# test_scorer.py - o scorer NUNCA deve falhar em silencio (v9.19).
# Regressao para o bug P0-5: o scorer ficou 'morto' por dias porque
# qualquer excecao em predict virava return 0.5 sem nada no log.
import os
import pickle
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from scorer import ScorerML
from features_lib import GeradorJanelas
from treino_lib import flatten_snapshot


class ModeloBom:
    # Dummy modelo que prediz 0.7 para qualquer entrada.
    def predict_proba(self, X):
        n = len(X)
        return np.array([[0.3, 0.7]] * n)


class ModeloQueFalla:
    # Dummy modelo que lanca excecao em predict_proba.
    def predict_proba(self, X):
        raise RuntimeError('boom')


def _scorer_com(modelo, features):
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as f:
        pickle.dump({'modelo': modelo, 'features': features}, f)
        path = f.name
    try:
        return ScorerML(path, ['WINV26'])
    finally:
        os.unlink(path)


def _snapshot_real():
    # Gera um snapshot real via GeradorJanelas (mesmo caminho do motor).
    g = GeradorJanelas(['WINV26'], janela_ms=100, passo_ms=100)
    out = g.processar_evento('WINV26', 1000, 100.0, 10, 'Comprador', 'XP', '')
    out2 = g.processar_evento('WINV26', 1100, 100.5, 10, 'Comprador', 'XP', '')
    assert out2, 'deveria emitir snapshot'
    return out2[0][1]


class TestScorerObservavel:
    def test_predicao_normal(self):
        s = _scorer_com(ModeloBom(), ['preco_ultimo'])
        p = s._prever({'preco_ultimo': 100.0})
        assert p == 0.7
        assert s.fallos == 0

    def test_falha_registrada(self):
        s = _scorer_com(ModeloQueFalla(), ['x'])
        p = s._prever({'x': 1.0})
        assert p == 0.5
        assert s.fallos == 1
        assert s.ultimo_error is not None
        assert s.ultimo_fallo_ts is not None

    def test_estado_salud(self):
        s = _scorer_com(ModeloBom(), ['preco_ultimo'])
        s._prever({'preco_ultimo': 1.0})
        salud = s.estado_salud()
        assert salud['fallos'] == 0
        assert salud['n_features_modelo'] == 1
        assert 'prob' in salud

    def test_decisao_threshold(self):
        s = _scorer_com(ModeloBom(), ['preco_ultimo'])
        s.prob['WINV26'] = 0.7
        lado, p = s.decisao('WINV26', threshold=0.65)
        assert lado == 1
        s.prob['WINV26'] = 0.3
        lado, p = s.decisao('WINV26', threshold=0.65)
        assert lado == -1
        s.prob['WINV26'] = 0.5
        lado, p = s.decisao('WINV26', threshold=0.65)
        assert lado == 0


class TestSemLeakage:
    # Regressoes P0-5/P0-6: features do modelo nao podem ser leakage
    # e precisam existir no snapshot ao vivo (scorer morto).
    LEAKAGE = {'preco_saida', 'duracao_label_ms', 'label', 'retorno_pts',
               'tp_atingido', 'sl_atingido', 'outcome'}

    def test_features_modelo_real_sem_leakage(self):
        path = 'D:/MarketData/mimo/rf_modelo.pkl'
        if not os.path.exists(path):
            pytest.skip('rf_modelo.pkl nao presente')
        with open(path, 'rb') as f:
            blob = pickle.load(f)
        features = set(blob['X_cols'])
        assert not (features & self.LEAKAGE), (
            'features de leakage no modelo: %s' % (features & self.LEAKAGE))

    def test_features_produziveis_no_snapshot(self):
        # Se uma feature nunca aparece no snapshot (ex.: veio do parquet),
        # o scorer fica 'morto' - este teste e o detector disso.
        path = 'D:/MarketData/mimo/rf_modelo.pkl'
        if not os.path.exists(path):
            pytest.skip('rf_modelo.pkl nao presente')
        with open(path, 'rb') as f:
            blob = pickle.load(f)
        features = set(blob['X_cols'])

        g = GeradorJanelas(['WINV26'], janela_ms=100, passo_ms=100)
        keys = set()
        ts = 1_000_000
        book = {
            'bid_vol': {1: 100, 2: 80, 3: 60},
            'ask_vol': {1: 90, 2: 70, 3: 50},
            'bid_preco': {1: 100.0, 2: 99.5, 3: 99.0},
            'ask_preco': {1: 100.5, 2: 101.0, 3: 101.5},
        }
        for i in range(120):
            preco = 100.0 + (i % 7) * 0.5
            for a, s in g.processar_evento('WINV26', ts, preco, 10, 'Comprador', 'XP', ''):
                keys |= set(flatten_snapshot(s).keys())
            g.processar_book('WINV26', ts, book)
            ts += 100

        ausentes = [f for f in features if f not in keys]
        assert not ausentes, (
            'features que nunca aparecem no snapshot (scorer morto): %s' % ausentes)
