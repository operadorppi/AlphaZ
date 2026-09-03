# -*- coding: utf-8 -*-
"""
testes/test_cross_asset_temporal_v1517.py — CrossAssetEngine nunca usa wall
clock; janelas são relativas ao ts do evento (P0-A23).

Cenários:
  1. Módulo não importa/usar time.time / datetime.now (guard de fonte).
  2. Replay determinístico: mesma sequência em 2 engines → features idênticas.
  3. As-of: calcular(T) após registrar eventos futuros (t > T) devolve
     EXATAMENTE as features de quando só existiam eventos ≤ T.
  4. calcular() sem ts == calcular(último ts registrado) (fallback as-of).
  5. wdo_delta/resposta não vazam o futuro (eventos violentos pós-ref não
     alteram o cálculo no ref).
"""

import inspect

from features.cross_asset import CrossAssetEngine, CrossAssetManager

WIN = 'WINV26'
WDO = 'WDOV26'


def _engine():
    return CrossAssetEngine(ativo_principal=WIN, ativo_contexto=WDO,
                            janela_corr=60, max_lag_ms=2000)


def _alimentar(eng, ate=8, futuro=False):
    """WIN sobe +1/tick e WDO +2/tick (WIN lidera, mesma direção)."""
    for i in range(0, ate + 1):
        t = 1000 + i * 1000
        eng.registrar(WIN, t, 100.0 + i, 0.3)
        eng.registrar(WDO, t, 50.0 + 2 * i, 0.3)
    if futuro:
        # Eventos VIOLENTOS e de direção OPOSTA depois do ref — se vazarem
        # no cálculo as-of, mudariam wdo_delta/resposta/divergência.
        for j, (t, p_win, p_wdo) in enumerate([
                (10000, 200.0, 10.0),
                (11000, 210.0, 8.0),
                (12000, 220.0, 6.0),
                (13000, 230.0, 4.0)]):
            eng.registrar(WIN, t, p_win, -0.9)
            eng.registrar(WDO, t, p_wdo, -0.9)


class TestSemWallClock:
    def test_fonte_sem_time_time(self):
        """Guard: o módulo não pode depender do relógio da máquina."""
        src = inspect.getsource(__import__('features.cross_asset', fromlist=['x']))
        assert 'time.time' not in src
        assert 'datetime.now' not in src
        assert 'time()' not in src

    def test_engine_nao_recebe_relogio(self):
        """calcular() sem ts usa as-of do último ts registrado, nunca now()."""
        eng = _engine()
        _alimentar(eng, ate=8)
        f_sem_ts = eng.calcular()
        f_com_ts = eng.calcular(9000)
        assert f_sem_ts == f_com_ts


class TestDeterminismoReplay:
    def test_mesma_sequencia_mesmas_features(self):
        """Replay da mesma sequência em engines independentes é idêntico."""
        e1, e2 = _engine(), _engine()
        for i in range(0, 9):
            t = 1000 + i * 1000
            for eng in (e1, e2):
                eng.registrar(WIN, t, 100.0 + i, 0.3)
                eng.registrar(WDO, t, 50.0 + 2 * i, 0.3)
                if i == 8:
                    f1 = eng.calcular(t)
        assert f1 == e1.calcular(9000) == e2.calcular(9000)


class TestAsOfSemFuturo:
    def test_calcular_no_ref_ignora_futuro(self):
        """Features em T são as mesmas antes e depois de registrar t > T."""
        eng = _engine()
        _alimentar(eng, ate=8)
        f_antes = eng.calcular(9000)
        assert f_antes['wdo_delta'] == 2.0   # (68-66) por 1s, mesma direção
        assert f_antes['resposta_win'] == 1.0

        # Registra futuro violento (WDO despenca enquanto WIN dispara).
        _alimentar(eng, ate=8, futuro=True)

        f_depois = eng.calcular(9000)
        assert f_depois == f_antes, \
            'Eventos futuros vazaram no cálculo as-of do ref'
        # Prova de que o futuro FOI registrado (senão o teste seria vazio):
        assert eng.calcular(13000)['wdo_delta'] < 0

    def test_fallback_sem_ts_igual_ultimo_ref(self):
        eng = _engine()
        _alimentar(eng, ate=8, futuro=True)
        assert eng.calcular() == eng.calcular(13000)


class TestManagerAsOf:
    def test_manager_para_ativo_respeita_ref(self):
        mgr = CrossAssetManager(pairs=[[WIN, WDO]])
        for i in range(0, 9):
            t = 1000 + i * 1000
            mgr.registrar(WIN, t, 100.0 + i, 0.3)
            mgr.registrar(WDO, t, 50.0 + 2 * i, 0.3)
        antes = mgr.calcular_para_ativo(WIN, 9000)
        mgr.registrar(WIN, 20000, 500.0, -0.9)
        mgr.registrar(WDO, 20000, 5.0, -0.9)
        assert mgr.calcular_para_ativo(WIN, 9000) == antes
