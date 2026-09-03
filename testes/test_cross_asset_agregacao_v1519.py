# -*- coding: utf-8 -*-
"""
testes/test_cross_asset_agregacao_v1519.py — P1-A25: semântica FORMAL da
correlação cross-asset.

Antes: bins por segundo (`t // 1000`) guardavam só o ÚLTIMO valor do segundo
— 100 eventos no mesmo segundo viravam "o último" e a dinâmica intrasegundo
era perdida, com a semântica implícita e não documentada.

Agora:
  - bucket_ms=100 (grid do master clock);
  - agregador explícito: 'mean' (default) | 'sum' | 'last';
  - cenário discriminador: 12 buckets com 2 eventos cada (primeiro alterna
    ±1, segundo sempre +1). 'mean' usa os dois eventos → corr = 1.0;
    'last' só vê o segundo evento (+1 constante) → sem variância → corr = 0.
"""

import pytest

from features.cross_asset import CrossAssetEngine

WIN = 'WINV26'
WDO = 'WDOV26'


def _eng(agregador='mean'):
    return CrossAssetEngine(ativo_principal=WIN, ativo_contexto=WDO,
                            agregador=agregador)


def _alimentar_discriminador(eng, n_buckets=12):
    """Bucket i (100ms): 2 eventos — primeiro signo alternado, segundo +1."""
    for i in range(n_buckets):
        primeiro = 1.0 if i % 2 == 0 else -1.0
        for ativo in (WIN, WDO):
            eng.registrar(ativo, i * 100, 100.0 + i, primeiro)
            eng.registrar(ativo, i * 100 + 30, 100.0 + i, 1.0)
    # ref após o último evento (as-of)
    return (n_buckets - 1) * 100 + 30


class TestDefaultsDocumentados:
    def test_defaults_do_engine(self):
        """Default = bucket 100ms + média (semântica explícita, não implícita)."""
        eng = _eng()
        assert eng.bucket_ms == 100
        assert eng.agregador == 'mean'

    def test_agregador_invalido_erro_explicito(self):
        with pytest.raises(ValueError):
            CrossAssetEngine(ativo_principal=WIN, ativo_contexto=WDO,
                             agregador='median')


class TestBucketizar:
    def test_modos_mean_sum_last(self):
        eng = _eng()
        eng._ref_ts = 10_000
        eventos = [
            (150, 0.0, 1.0, 0.0),
            (160, 0.0, 1.0, 0.0),
            (170, 0.0, -1.0, 0.0),
        ]
        b = 150 // eng.bucket_ms
        assert eng._bucketizar(eventos, 'aggr', 0) == {b: 1.0 / 3.0}
        eng_last = _eng('last')
        eng_last._ref_ts = 10_000
        assert eng_last._bucketizar(eventos, 'aggr', 0) == {b: -1.0}
        eng_sum = _eng('sum')
        eng_sum._ref_ts = 10_000
        assert eng_sum._bucketizar(eventos, 'aggr', 0) == {b: 1.0}

    def test_bucket_sem_evento_e_gap_nao_zero(self):
        eng = _eng()
        eng._ref_ts = 10_000
        eventos = [(100, 0.0, 1.0, 0.0), (300, 0.0, 1.0, 0.0)]
        bins = eng._bucketizar(eventos, 'aggr', 0)
        assert sorted(bins) == [100 // 100, 300 // 100]  # bucket 200 ausente


class TestCorrelacaoSemantica:
    def test_mean_preserva_dinamica_intrasegundo(self):
        """Default 'mean': os DOIS eventos do bucket contam → corr = 1.0."""
        eng = _eng('mean')
        ref = _alimentar_discriminador(eng)
        assert eng.calcular(ref)['corr_aggr'] == 1.0

    def test_last_perde_dinamica(self):
        """'last': só o 2º evento (+1 constante) → sem variância → corr = 0."""
        eng = _eng('last')
        ref = _alimentar_discriminador(eng)
        assert eng.calcular(ref)['corr_aggr'] == 0.0

    def test_mean_difere_de_last(self):
        """Prova de que o default usa informação que o antigo descartava."""
        eng_m = _eng('mean')
        eng_l = _eng('last')
        ref_m = _alimentar_discriminador(eng_m)
        ref_l = _alimentar_discriminador(eng_l)
        assert eng_m.calcular(ref_m)['corr_aggr'] != eng_l.calcular(ref_l)['corr_aggr']

    def test_sum_tambem_preserva(self):
        eng = _eng('sum')
        ref = _alimentar_discriminador(eng)
        # reps 2.0/0.0 alternando e idênticos entre lados → corr = 1.0
        assert eng.calcular(ref)['corr_aggr'] == 1.0

    def test_bucket_100ms_nao_agrupa_por_segundo(self):
        """Eventos em segundos diferentes mas no mesmo bucket de 100ms NÃO
        se misturam — o grid é o do master clock (100ms)."""
        eng = _eng('mean')
        # 12 buckets distintos com 200ms de espaçamento (b = t//100 difere)
        for i in range(12):
            t = 1000 + i * 200
            eng.registrar(WIN, t, 100.0, 1.0)
            eng.registrar(WDO, t, 100.0, 1.0)
        f = eng.calcular(1000 + 11 * 200)
        # buckets com reps constantes +1 → sem variância → 0.0 (não é 12s de
        # bins: com bucket 1s os 12 segundos dariam igualmente 0 por constância)
        assert f['corr_aggr'] == 0.0
