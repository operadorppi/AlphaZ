# -*- coding: utf-8 -*-
"""
testes/test_labeler_tempo_real_v1530.py — horizonte de holding por TIMESTAMP
real, nao por contagem de linhas (P0-A33, v15.30).

ANTES: label_vectorizado/label_array_ref convertiam max_holding_s em
"max_holding_ms // tick_ms LINHAS". Com dados RAW irregulares (rajada a 1ms,
silêncio a 800ms), N linhas != N*100ms — um holding de 30s podia virar 300
eventos ou 5 eventos, alterando os labels.

Cenarios discriminadores:
  1. RAJADA densa: TP real aos +12s, mas alem da 300a linha (legado cortava
     em 300 linhas e rotulava TIMEOUT) -> agora TP com duracao real ~12000ms.
  2. ESPARSO: barreira so apos 45s reais (holding 30s) -> legado varria 300
     linhas (45s) e rotulava TP; agora TIMEOUT (fora do holding real).
  3. duracao_ms sempre real (delta ts), nunca multiplo assumido de 100.
"""

import numpy as np
import pytest

from ml.labeler_core import LabelOutcome, label_array_ref
from ml.labeler_vectorizado import label_vectorizado

BASE = 1_787_000_000_000


def _ativo(n):
    return np.array(['WINV26'] * n)


class TestRajadaDensa:
    def test_tp_real_alem_da_300a_linha_nao_vira_timeout(self):
        """400 eventos de rajada (5s, precos ~100, nunca tocam TP=250) + subida
        pos-rajada que toca TP (~250) so aos ~10s reais (linha ~450). O legado
        (max 300 linhas) rotulava TIMEOUT; com horizonte temporal real, TP."""
        ts = [BASE + i * 12 for i in range(400)]      # rajada ate ~4.8s
        precos = [100.0 + (i % 7) for i in range(400)]  # nunca >= 250
        p = 107.0
        while ts[-1] < BASE + 10_000 or p < 250.0:
            ts.append(ts[-1] + 200)
            p += 6.0
            precos.append(p)
        precos = np.array(precos)
        ts_arr = np.array(ts, dtype=np.int64)
        ativos = _ativo(len(ts))

        res = label_vectorizado(precos, ts_arr, ativos,
                                tp_pts=150.0, sl_pts=500.0,
                                max_holding_s=30)
        assert res['label'][0] == 1, \
            'TP real dentro de 30s foi truncado pela contagem de linhas'
        dur = int(res['duracao_ms'][0])
        assert 8_000 <= dur <= 13_000, f'duracao irreal: {dur}ms'
        # o toque real esta em linha > 300 (alem do corte legado)
        idx_tp = int(np.argmax(precos >= 250.0))
        assert idx_tp > 300


class TestEsparso:
    def test_barreira_fora_do_holding_real_vira_timeout(self):
        """5 eventos espacados 9s: TP so aos ~45s. Holding 30s -> TIMEOUT.
        O legado (300 linhas = varria os 5 eventos, 45s) rotulava TP errado."""
        ts = [BASE + i * 9_000 for i in range(5)]
        precos = np.array([100.0, 101.0, 102.0, 103.0, 320.0])  # TP em 4
        ts_arr = np.array(ts, dtype=np.int64)
        ativos = _ativo(5)

        res = label_vectorizado(precos, ts_arr, ativos,
                                tp_pts=150.0, sl_pts=500.0,
                                max_holding_s=30)
        assert res['label'][0] == 0, \
            'barreira apos 30s reais NAO pode virar TP (holding real)'


class TestDuracaoReal:
    def test_duracao_usa_timestamp_nao_linhas(self):
        """Eventos a 1ms de intervalo: duracao real de 3 eventos = 2ms (nao
        300ms como o legado '3 linhas * 100ms')."""
        ts = [BASE, BASE + 1, BASE + 2]
        precos = np.array([100.0, 96.0, 106.0])  # SL no evento 1 (1ms)
        ts_arr = np.array(ts, dtype=np.int64)
        res = label_vectorizado(precos, ts_arr, _ativo(3),
                                tp_pts=5.0, sl_pts=3.0, max_holding_s=1)
        assert res['label'][0] == -1  # SL primeiro
        assert res['duracao_ms'][0] == 1  # 1ms real, nao 100ms

    def test_timeout_duracao_tempo_real(self):
        ts = [BASE, BASE + 500, BASE + 800]
        precos = np.array([100.0, 100.5, 100.4])
        res = label_vectorizado(precos, np.array(ts, dtype=np.int64),
                                _ativo(3), tp_pts=50.0, sl_pts=50.0,
                                max_holding_s=1)
        assert res['label'][0] == 0
        assert res['duracao_ms'][0] == 800  # ate o ultimo evento da janela


class TestArrayRefIgualVectorizado:
    def test_equivalencia_em_irregular(self):
        """core (label_array_ref) e producao (label_vectorizado) concordam
        em dados irregulares — ambas com horizonte temporal real."""
        rng = np.random.default_rng(3)
        n = 300
        ts = np.sort(BASE + np.cumsum(rng.integers(1, 300, n)))
        precos = 100 + np.cumsum(rng.normal(0, 2, n))
        ativos = _ativo(n)
        ref = label_array_ref(precos, ts, ativos, tp_pts=30.0, sl_pts=30.0,
                              max_holding_s=10)
        vec = label_vectorizado(precos, ts, ativos, tp_pts=30.0, sl_pts=30.0,
                                max_holding_s=10)
        assert (ref['label'] == vec['label']).all()
        assert (ref['duracao_ms'] == vec['duracao_ms']).all()
        assert (ref['preco_saida'] == vec['preco_saida']).all()


class TestGridUniformeCompat:
    def test_grid_100ms_resultado_identico_ao_esperado(self):
        """Em grid uniforme de 100ms (dataset_100ms), o resultado mantem a
        semantica classica: SL no evento 1 -> duracao 100ms."""
        ts = np.arange(6, dtype=np.int64) * 100
        precos = np.array([100.0, 96, 98, 100, 103, 105])
        res = label_vectorizado(precos, ts, _ativo(6),
                                tp_pts=5.0, sl_pts=3.0, max_holding_s=1)
        assert res['label'][0] == -1
        assert res['duracao_ms'][0] == 100
