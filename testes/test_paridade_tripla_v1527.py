# -*- coding: utf-8 -*-
"""
testes/test_paridade_tripla_v1527.py — CONTRATO UNICO de features e paridade
tripla (P0-A31, v15.27).

O ScorerML mantem estado temporal proprio em paralelo ao motor. O contrato
(ml/paridade_features.CONTRATO) cataloga nome-a-nome quem produz cada feature
e a classe de igualdade. Este teste roda os MESMOS eventos deterministicos
nas pemas e trava numericamente o que e definido como igual:

  A = REALTIME (FeatureEngine, 1s)   -> heuristico  (AGR_DIFERENTE por design)
  B = OFFLINE/GRID (GeradorJanelas)  -> dataset de treino (dataset_100ms)
  C = SCORER (trackers do ScorerML)  -> inferencia do modelo

Trava principal: B_offline ~ C_scorer nas features vol_*/retorno_* (mesma
definicao EWMA no grid) e identidade de fim de stream do VP (mesma classe de
tracker alimentada pelos mesmos trades). Achado corrigido junto (v15.27):
o row do scorer passou a usar o vp do snap do gerador (semantica do dataset,
lag de 1 trade no corte) em vez do self.vps no instante do trade — antes o
VP do scorer divergia do dataset em ~1 trade.
"""

import pytest

from ml.paridade_features import (CONTRATO, VP_B2C, offline_vol_ret,
                                  relatorio, rodar_paridade,
                                  vp_identidade_fim)

SEGUNDOS = 60  # cobre retorno_300x/500x; vol_1min/5min ficam SEM_COBERTURA


@pytest.fixture(scope='module')
def paridade():
    return rodar_paridade(segundos=SEGUNDOS)


@pytest.fixture(scope='module')
def linhas(paridade):
    A, B, C, off, trades, books, vp_id, vp_lag = paridade
    return relatorio(A, B, C, off, vp_id=vp_id, vp_lag=vp_lag,
                     verbose=False)


class TestContratoCatalogo:
    def test_contrato_cobre_vol_e_ret(self):
        """O catalogo nomeia vol_* e retorno_* como MESMA_DEFINICAO."""
        assert any(k.startswith('vol_') and v == 'MESMA_DEFINICAO'
                   for k, v in CONTRATO.items())
        assert any(k.startswith('retorno_') and v == 'MESMA_DEFINICAO'
                   for k, v in CONTRATO.items())

    def test_vp_mapeado_b_para_c(self):
        assert len(VP_B2C) >= 5
        assert set(VP_B2C.values()) == {
            'poc_dist', 'vah_dist', 'val_dist', 'vp_total', 'poc_acima'}


class TestParidadeNumerica:
    def test_stream_gerado(self, paridade):
        A, B, C, off, trades, books, vp_id, vp_lag = paridade
        assert len(trades) > 200
        assert len(C) == SEGUNDOS
        assert len(A) == SEGUNDOS
        assert vp_id['igual'] is True

    def test_vp_lag_1_trade_sem_desvio(self, paridade):
        """v15.28: o VP do snap (dataset) == estado causal as-of do corte
        (ultimo trade com ts ESTRITAMENTE < corte) em 100% dos cortes,
        inclusive na rajada — o lag de 1 trade NAO desvia o VP."""
        A, B, C, off, trades, books, vp_id, vp_lag = paridade
        assert vp_lag['n_cortes'] > 0
        burst_ok = False
        for f, st in vp_lag['campos'].items():
            if st['n'] == 0:
                continue
            assert st['n_diff'] == 0, \
                f'vp.{f}: {st["n_diff"]}/{st["n"]} cortes divergem do as-of'
            assert st['max_diff'] == 0
            if st['n_burst'] > 0:
                assert st['max_diff_burst'] == 0
                burst_ok = True
        assert burst_ok, 'nenhum corte na janela de rajada foi exercitado'

    def test_vp_lag_reportado_no_relatorio(self, linhas):
        lag_lines = [l for l in linhas
                     if l['classe'] == 'VP_LAG_1_TRADE']
        assert lag_lines, 'VP_LAG_1_TRADE ausente do relatorio'
        assert all(l['status'] == 'OK' for l in lag_lines), \
            [l for l in lag_lines if l['status'] != 'OK']

    def test_vol_ret_offline_x_scorer_ok(self, linhas):
        """B_offline ~ C_scorer: nenhuma feature MESMA_DEFINICAO diverge
        alem da borda de 1 linha do grid (SEM_COBERTURA permitido p/ os
        horizontes maiores que a historia do stream)."""
        relevantes = [l for l in linhas if l['classe'] == 'MESMA_DEFINICAO']
        assert relevantes, 'nenhuma linha MESMA_DEFINICAO no relatorio'
        divergentes = [l for l in relevantes if l['status'] == 'DIVERGE']
        assert not divergentes, f'features divergentes: {divergentes}'

    def test_vol_100ms_exato(self, linhas):
        """vol_100ms (1 linha) e EXATO: sem acumulo de borda de janela."""
        linha = next(l for l in linhas if l['feature'] == 'vol_100ms')
        assert linha['max_diff'] == 0

    def test_vp_identidade_fim_de_stream(self, linhas):
        linha = next(l for l in linhas if l['classe'] == 'VP_PARALELO')
        assert linha['status'] == 'OK'
        assert linha['max_diff'] == 0

    def test_vp_identidade_direta(self, paridade):
        A, B, C, off, trades, books, vp_id, vp_lag = paridade
        assert vp_id['b_volumes'] == vp_id['c_volumes']

    def test_sem_surpresas_sem_cobertura(self, linhas):
        """Horizontes > historia sao SEM_COBERTURA (nunca comparados como
        zero ou DIVERGE)."""
        sc = [l for l in linhas if l['status'] == 'SEM_COBERTURA']
        assert sc, 'esperava horizontes sem cobertura em %ds' % SEGUNDOS
        nomes = {l['feature'] for l in sc}
        assert 'vol_5min' in nomes


class TestDivergenciaPorDesignDocumentada:
    def test_agr_diferente_reportado_nao_comparado(self, linhas):
        """Nomes A(1s) x C(grid) tem agregacao diferente POR DESIGN — o
        relatorio registra presenca (PRESENTE_AMBOS/PARCIAL), nunca OK/DIVERGE
        como se fossem a mesma feature."""
        agrs = [l for l in linhas if l['classe'] == 'AGR_DIFERENTE']
        assert agrs
        for l in agrs:
            assert l['status'] in ('PRESENTE_AMBOS', 'PARCIAL'), \
                f"{l['feature']}: {l['status']}"
