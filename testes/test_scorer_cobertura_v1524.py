# -*- coding: utf-8 -*-
"""
testes/test_scorer_cobertura_v1524.py — Contrato de cobertura de features
(P0-A29).

ANTES: no fallback sem feature_manifest.json o scorer montava o vetor com
`row.get(c, 0.0)` — feature AUSENTE virava 0.0 em silencio (informacao falsa
pro modelo: ausente != zero). O extract() do manifest tambem zerava feature
presente com valor nao numerico e opcional ausente sem default.

AGORA (v15.24), tres estados separados:
  - obrigatoria ausente            -> fail-safe (0.5 neutro + contador + log)
  - presente mas nao numerica      -> fail-safe (idem) — nunca adivinhar
  - opcional ausente COM default   -> default documentado (ok)
  - opcional ausente SEM default   -> problema (nunca zero fake)
  - presente e zero                -> ZERO LEGITIMO (ok)
"""

import json
import os
import pickle
import tempfile

import numpy as np
import pytest

from ml.feature_manifest import FeatureManifest

WIN = 'WINV26'


class _DummyModel:
    """Modelo dummy serializavel (padrao dos testes de integracao)."""
    def predict_proba(self, X):
        return np.array([[0.3, 0.7]] * len(X))


def _fazer_modelo(tmpdir, features, com_manifest=None):
    """Grava pkl (+ manifest opcional) e retorna o caminho do pkl."""
    blob = {'modelo': _DummyModel(), 'features': features}
    pkl = os.path.join(tmpdir, 'modelo.pkl')
    with open(pkl, 'wb') as f:
        pickle.dump(blob, f)
    if com_manifest is not None:
        with open(os.path.join(tmpdir, 'feature_manifest.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(com_manifest, f)
    return pkl


def _feat(nome, required=True, default=None):
    return {'nome': nome, 'tipo': 'float', 'required': required,
            'default': default}


def _snap(**extra):
    base = {'ativo': WIN, 'ts_ms': 1_770_000_000_000, 'preco_ultimo': 100000.0,
            'aggr_imb': 1.0}
    base.update(extra)
    return base


# ======================================================================
#  1. Unit: FeatureManifest.montar_vetor (contrato puro)
# ======================================================================

class TestMontarVetor:
    def test_obrigatoria_ausente_e_problema(self):
        m = FeatureManifest([_feat('a'), _feat('b')])
        vals, problemas = m.montar_vetor({'a': 1.0})
        assert vals is None
        assert problemas == ['AUSENTE:b']

    def test_presente_nao_numerica_e_problema(self):
        m = FeatureManifest([_feat('a')])
        # string 'abc' presente: nunca virar 0.0
        vals, problemas = m.montar_vetor({'a': 'abc'})
        assert vals is None
        assert problemas == ['INVALIDA:a']
        # None presente tambem e invalido
        vals, problemas = m.montar_vetor({'a': None})
        assert problemas == ['INVALIDA:a']

    def test_opcional_ausente_com_default_ok(self):
        m = FeatureManifest([_feat('a'), _feat('b', required=False,
                                               default=1.5)])
        vals, problemas = m.montar_vetor({'a': 2.0})
        assert problemas == []
        assert vals == [2.0, 1.5]

    def test_opcional_ausente_sem_default_e_problema(self):
        m = FeatureManifest([_feat('a'),
                             _feat('b', required=False, default=None)])
        vals, problemas = m.montar_vetor({'a': 2.0})
        assert vals is None
        assert problemas == ['SEM_DEFAULT:b']

    def test_zero_legitimo_e_zero(self):
        m = FeatureManifest([_feat('a')])
        vals, problemas = m.montar_vetor({'a': 0.0})
        assert problemas == []
        assert vals == [0.0]

    def test_ordem_exata_preservada(self):
        m = FeatureManifest([_feat('z'), _feat('a'), _feat('m')])
        vals, problemas = m.montar_vetor({'a': 1.0, 'z': 3.0, 'm': 2.0})
        assert problemas == []
        assert vals == [3.0, 1.0, 2.0]

    def test_numpy_scalar_aceito(self):
        m = FeatureManifest([_feat('a')])
        vals, problemas = m.montar_vetor({'a': np.float64(2.5)})
        assert problemas == []
        assert vals == [2.5]


# ======================================================================
#  2. Integracao: ScorerML sem manifest (fallback do .pkl) — P0-A29
# ======================================================================

class TestScorerFallbackSemManifest:
    def test_feature_ausente_vira_fail_safe_nao_zero(self):
        """ANTES: ausente virava 0.0 e o modelo rodava com informacao falsa.
        AGORA: 0.5 neutro + contador + motivo no ultimo_error."""
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb',
                                    'feature_inexistente'])
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap())
            assert p == 0.5
            assert scorer.fallos == 1
            assert 'AUSENTE:feature_inexistente' in scorer.ultimo_error

    def test_feature_presente_invalida_vira_fail_safe(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb',
                                    'feature_inv'])
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap(feature_inv='abc'))
            assert p == 0.5
            assert scorer.fallos == 1
            assert 'INVALIDA:feature_inv' in scorer.ultimo_error

    def test_zero_legitimo_roda_o_modelo(self):
        """Features presentes com valor zero sao ZERO legitimo — o modelo
        roda (dummy -> 0.7), sem contagem de fallos."""
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb'])
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap(preco_ultimo=0.0, aggr_imb=0.0))
            assert p == pytest.approx(0.7)
            assert scorer.fallos == 0
            assert scorer.ultimo_error is None

    def test_cobertura_completa_roda_o_modelo(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb'])
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap())
            assert p == pytest.approx(0.7)
            assert scorer.fallos == 0


# ======================================================================
#  3. Integracao: ScorerML com manifest
# ======================================================================

class TestScorerComManifest:
    def _manifest(self, features):
        return {'model_name': 'teste', 'model_version': '1',
                'train_date': '2026-09-03', 'features': features}

    def test_obrigatoria_ausente_fail_safe(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            manifest = self._manifest([_feat('preco_ultimo'),
                                       _feat('aggr_imb'),
                                       _feat('req_inexistente')])
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb',
                                    'req_inexistente'],
                                com_manifest=manifest)
            scorer = ScorerML(pkl, [WIN])
            assert scorer.manifest is not None
            p = scorer._prever(_snap())
            assert p == 0.5
            assert scorer.fallos == 1
            assert 'AUSENTE:req_inexistente' in scorer.ultimo_error

    def test_opcional_com_default_roda_modelo(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            manifest = self._manifest([_feat('preco_ultimo'),
                                       _feat('aggr_imb'),
                                       _feat('opcional', required=False,
                                             default=1.5)])
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb', 'opcional'],
                                com_manifest=manifest)
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap())
            # dummy ignora valores: so confirma que NAO caiu no fail-safe
            assert p == pytest.approx(0.7)
            assert scorer.fallos == 0

    def test_opcional_sem_default_fail_safe(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            manifest = self._manifest([_feat('preco_ultimo'),
                                       _feat('aggr_imb'),
                                       _feat('opcional', required=False,
                                             default=None)])
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb', 'opcional'],
                                com_manifest=manifest)
            scorer = ScorerML(pkl, [WIN])
            p = scorer._prever(_snap())
            assert p == 0.5
            assert 'SEM_DEFAULT:opcional' in scorer.ultimo_error

    def test_manifest_carregado_pelo_scorer(self):
        from ml.scorer import ScorerML
        with tempfile.TemporaryDirectory() as d:
            manifest = self._manifest([_feat('preco_ultimo'),
                                       _feat('aggr_imb')])
            pkl = _fazer_modelo(d, ['preco_ultimo', 'aggr_imb'],
                                com_manifest=manifest)
            scorer = ScorerML(pkl, [WIN])
            assert scorer.manifest is not None
            assert scorer.manifest.n_features == 2
