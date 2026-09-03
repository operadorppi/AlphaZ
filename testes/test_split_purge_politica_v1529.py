# -*- coding: utf-8 -*-
"""
testes/test_split_purge_politica_v1529.py — embargo solicitado != realizado
(P1-A32, v15.29).

ANTES: split_com_purge() reduzia o embargo em silencio quando os dados apos
o corte nao comportavam o gap pedido (purge=5s/embargo=30s viravam um gap
menor) e o resultado seguia sendo apresentado como "sem leakage".

AGORA:
  - retornar_politica=True  -> devolve (train, test, politica) com
    embargo_solicitado/realizado e status OK | EMBARGO_REDUZIDO;
  - exigir_integral=True    -> NAO adapta: levanta ValueError
    (VALIDACAO INCONCLUSIVA) quando o embargo solicitado nao cabe;
  - fallback operacional (default) loga INCONCLUSIVA e nunca pode ser
    confundido com embargo integral (politica explicita).
"""

import time

import pandas as pd
import pytest

from ml.treino_lib import split_com_purge


def _df(segundos=100, step_ms=100, base=None):
    if base is None:
        base = int(time.time() * 1000)
    ts = [base + i * step_ms for i in range(segundos * (1000 // step_ms))]
    return pd.DataFrame({'ts_ms': ts, 'label': 1, 'f1': range(len(ts))}), base


class TestEmbargoIntegral:
    def test_embargo_cabe_status_ok(self):
        """60s de dados apos o corte comportam embargo de 30s -> OK integral."""
        df, _ = _df(segundos=200)  # corte em 160s; sobra 40s
        train, test, pol = split_com_purge(
            df, train_pct=0.8, purge_s=5, embargo_s=30,
            retornar_politica=True)
        assert pol['status'] == 'OK'
        assert pol['embargo_integral'] is True
        assert pol['embargo_realizado_s'] == 30.0
        assert len(test) > 0
        # barreira total: gap treino->teste >= purge (5s) + embargo (30s)
        gap_s = (test['ts_ms'].min() - train['ts_ms'].max()) / 1000
        assert gap_s >= 34.0

    def test_default_continua_compativel(self):
        """Sem kwargs novos, retorno continua (train, test)."""
        df, _ = _df(segundos=200)
        train, test = split_com_purge(df, train_pct=0.8, purge_s=5,
                                      embargo_s=30)
        assert len(test) > 0


class TestEmbargoReduzido:
    def test_reducao_explicita_na_politica(self):
        """Janela curta de teste: embargo realizado < solicitado e o status
        EMBARGO_REDUZIDO nunca mente que o integral foi aplicado."""
        df, _ = _df(segundos=100)  # corte em 80s; sobra 20s < embargo 30s
        train, test, pol = split_com_purge(
            df, train_pct=0.8, purge_s=5, embargo_s=30,
            retornar_politica=True)
        assert pol['status'] == 'EMBARGO_REDUZIDO'
        assert pol['embargo_integral'] is False
        assert pol['embargo_solicitado_s'] == 30.0
        assert pol['embargo_realizado_s'] < 30.0
        # o fallback preserva pelo menos o gap de purge
        assert pol['embargo_realizado_s'] >= 5.0 - 1e-9
        assert len(test) > 0
        # o retorno (sem a politica) nao pode ser confundido com integral
        assert not any('embargo_integral' in str(x) for x in (train, test))

    def test_exigir_integral_falha_explicita(self):
        """exigir_integral=True: NAO adapta — VALIDACAO INCONCLUSIVA."""
        df, _ = _df(segundos=100)
        with pytest.raises(ValueError, match='VALIDACAO INCONCLUSIVA'):
            split_com_purge(df, train_pct=0.8, purge_s=5, embargo_s=30,
                            exigir_integral=True)

    def test_exigir_integral_ok_quando_cabe(self):
        df, _ = _df(segundos=200)
        train, test = split_com_purge(df, train_pct=0.8, purge_s=5,
                                      embargo_s=30, exigir_integral=True)
        assert len(test) > 0

    def test_janela_degenerada_nunca_passa_como_ok(self, caplog):
        """Dados de cauda menores que o purge: o split degenera (teste
        minusculo) mas NUNCA e rotulado OK — status EMBARGO_REDUZIDO e o log
        marca VALIDACAO INCONCLUSIVA (nao vira 'sem leakage' silencioso)."""
        import logging
        df, _ = _df(segundos=5)  # corte em 4s; cauda de ~1s
        train, test, pol = split_com_purge(
            df, train_pct=0.8, purge_s=5, embargo_s=30,
            retornar_politica=True)
        assert pol['status'] == 'EMBARGO_REDUZIDO'
        assert pol['embargo_integral'] is False
        assert len(test) >= 1
        assert any('VALIDACAO INCONCLUSIVA' in r.message for r in caplog.records)


class TestPurgePreservado:
    def test_purge_remove_fim_do_treino(self):
        df, _ = _df(segundos=200)
        train, test, pol = split_com_purge(
            df, train_pct=0.8, purge_s=5, embargo_s=30,
            retornar_politica=True)
        # treino termina purge_s antes do corte
        assert (pol['ts_corte'] - train['ts_ms'].max()) / 1000 >= 5.0
        assert (test['ts_ms'].min() - pol['ts_corte']) / 1000 >= 30.0
