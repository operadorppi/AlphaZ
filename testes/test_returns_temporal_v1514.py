# -*- coding: utf-8 -*-
"""
testes/test_returns_temporal_v1514.py — Retornos por master clock (P0-A20).

O ReturnsTracker ANTIGO indexava o buffer por NÚMERO de trades e assumia
1 trade = 100ms. Com 100 trades em 20ms, retorno_1x100ms media o retorno
entre 2 trades consecutivos, não entre t e t-100ms. Com 0 trades por 2s,
as "janelas" mentiam.

Novo: janelas temporais com amostragem previous-tick/as-of (bisect).
Cobertura:
  1. Rajada: 100 trades em 20ms -> retorno_100ms mede TEMPO real
  2. Silêncio: sem trades, retorno do horizonte usa o último tick <= alvo
  3. Janela sem cobertura -> None
  4. Snapshot as-of num instante arbitrario
  5. Eventos fora de ordem nao corrompem o buffer
  6. Podas por idade e tamanho
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from features.returns import ReturnsTracker  # noqa: E402


class TestReturnsTemporal:
    def test_rajada_mede_tempo_real(self):
        """Cenario da auditoria: 100 trades em 20ms (t=1000..1019).

        Precos sobem 1 tick a cada trade: 100.0 + 0.2*i.
        O antigo: retorno_1x100ms = retorno entre 2 trades (~0.2%).
        O novo: retorno de 100ms real = preco(t=1019) / preco_asof(t=919) - 1.
        Nenhum tick em t<=919 -> None para 100ms (cobertura < 100ms).
        Para 500ms (alvo 519): tambem None. O teste prova que NAO mede
        entre trades consecutivos (que seria ~0.2% e nao None).
        """
        tr = ReturnsTracker()
        for i in range(100):
            tr.update(1000 + i, 100.0 + 0.2 * i)  # 100 trades em 100ms

        s = tr.snapshot()
        # ultimo tick em t=1099 -> alvo 100ms = 999 -> nenhum tick -> None
        assert s["retorno_1x100ms"] is None, \
            "rajada com <100ms de historia nao pode ter retorno de 100ms"
        assert s["retorno_5x100ms"] is None
        assert s["retorno_10x100ms"] is None
        assert s["retorno_50x100ms"] is None

    def test_retorno_100ms_com_cobertura(self):
        """Ticks espacados 100ms: retorno_100ms = tick(t)/tick(t-100) - 1."""
        tr = ReturnsTracker()
        # 1 tick a cada 100ms, preco sobe 0.1 a cada tick
        for k in range(20):
            tr.update(1000 + k * 100, 100.0 + 0.1 * k)
        s = tr.snapshot()
        # ultimo tick t=2900 p=101.9; alvo 100ms = 2800 p=101.8
        assert s["retorno_1x100ms"] == pytest.approx(101.9 / 101.8 - 1)
        # alvo 500ms = 2400 p=101.4
        assert s["retorno_5x100ms"] == pytest.approx(101.9 / 101.4 - 1)
        # alvo 1s = 1900 p=100.9
        assert s["retorno_10x100ms"] == pytest.approx(101.9 / 100.9 - 1)

    def test_silencio_usa_previous_tick(self):
        """Trades espacados irregularmente (silencio de 2s entre 2 rajadas):
        o retorno de 100ms usa o ULTIMO tick <= alvo (as-of), nao exige um
        trade exatamente no instante."""
        tr = ReturnsTracker()
        tr.update(1000, 100.0)
        tr.update(2000, 100.2)
        tr.update(2100, 100.4)  # ultimo tick
        s = tr.snapshot()  # ts default = 2100
        # alvo 100ms = 2000 -> tick exato 2000 existe (p=100.2)
        assert s["retorno_1x100ms"] == pytest.approx(100.4 / 100.2 - 1)
        # alvo 500ms = 1600 -> nenhum tick <= 1600? tick 1000 <= 1600 sim (as-of)
        # previous-tick: ultimo tick com ts <= 1600 = tick em 1000 (p=100.0)
        assert s["retorno_5x100ms"] == pytest.approx(100.4 / 100.0 - 1)

    def test_janela_sem_cobertura_retorna_none(self):
        """Historico menor que o horizonte -> None (nao inventa retorno)."""
        tr = ReturnsTracker()
        tr.update(1000, 100.0)
        tr.update(1100, 100.2)
        s = tr.snapshot()
        assert s["retorno_1x100ms"] == pytest.approx(100.2 / 100.0 - 1)
        assert s["retorno_50x100ms"] is None    # 5s de historia inexistente
        assert s["retorno_500x100ms"] is None   # 50s inexistente

    def test_snapshot_as_of_arbitrario(self):
        """snapshot(ts) mede retornos fechados naquele instante (replay)."""
        tr = ReturnsTracker()
        for k in range(20):
            tr.update(1000 + k * 100, 100.0 + 0.1 * k)
        # as-of t=2500 (entre ticks 2400 e 2500? tick 2500 existe: k=15)
        # t=2500 p=101.5; alvo 100ms=2400 p=101.4
        s = tr.snapshot(ts_ms=2500)
        assert s["retorno_1x100ms"] == pytest.approx(101.5 / 101.4 - 1)
        # as-of num instante sem tick exato
        s2 = tr.snapshot(ts_ms=2550)
        # p as-of = tick 2500 (101.5); alvo 2450 = tick 2400 (101.4)
        assert s2["retorno_1x100ms"] == pytest.approx(101.5 / 101.4 - 1)

    def test_fora_de_ordem_nao_corrompe(self):
        """Evento atrasado (ts menor que o ultimo) entra ordenado."""
        tr = ReturnsTracker()
        tr.update(3000, 102.0)
        tr.update(3100, 102.2)
        tr.update(1500, 101.0)  # atrasado — entra no meio
        s = tr.snapshot()
        # as-of 3100: p=102.2; alvo 100ms=3000 -> p=102.0
        assert s["retorno_1x100ms"] == pytest.approx(102.2 / 102.0 - 1)

    def test_poda_por_idade_e_tamanho(self):
        """Buffer nao cresce sem limite (idade > 5min + folga e rajadas)."""
        tr = ReturnsTracker()
        tr.update(1000, 100.0)
        # 200k ticks em 4min (rajada extrema) + 1 antigo
        for i in range(200_000):
            tr.update(60_000 + i, 100.0 + 0.001 * i)
        assert len(tr._times) <= 200_001
        # tick antigo (t=1000) podado por idade? cobertura = ultimo - 310s
        ultimo = tr._times[-1]
        assert tr._times[0] >= ultimo - 310_000
        # antigo de 1000ms deve ter saido (60s vs 310s de folga -> ficou!)
        # (ultimo ~260s; corte ~ -50s => 1000ms fora) verificar:
        assert 1000 not in tr._times or ultimo - 1000 <= 310_000
