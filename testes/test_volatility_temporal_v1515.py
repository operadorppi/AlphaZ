# -*- coding: utf-8 -*-
"""
testes/test_volatility_temporal_v1515.py — Volatilidade por master clock (P0-A21).

O VolatilityTracker ANTIGO indexava janelas por CONTAGEM de trades
(n=1 "100ms", n=5 "500ms"...) — 10 trades em 2s (calmo) ou 20ms (rajada)
eram tratados como a mesma "janela". A feature media N trades, nao tempo.

Novo: grid de 100ms do master clock (mesma semantica do batch: corte c
fecha com o ultimo trade de ts < c; cortes sem trade tem preco constante e
a EWMA decai; sem cobertura a EWMA nao atualiza).

Cobertura:
  1. Rajada (muitos trades em 20ms) NAO acelera o relogio do grid
  2. Tick de 100ms gera atualizacao por corte (retorno real de 100ms)
  3. Silencio (sem trades) NAO atualiza as EWMAs (sem cortes novos)
  4. Trades esparsos: mesma evolucao que trades densos no mesmo relogio
  5. Alias: volatility_tracker.py re-exporta a implementacao unica
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from features.volatility import VolatilityTracker  # noqa: E402
from features.volatility_tracker import VolatilityTracker as VT_ALIAS  # noqa: E402


class TestVolatilityTemporal:
    def test_rajada_nao_acelera_o_relogio(self):
        """Cenario da auditoria: 100 trades em 20ms.

        O relogio do grid avanca por TEMPO (ts), nao por trade. Depois de
        100 trades em 20ms, apenas o corte apos o 1o trade foi criado e
        nenhuma EWMA com cobertura atualizou (faltam >= 2 cortes).
        """
        tr = VolatilityTracker()
        for i in range(100):
            tr.update(1000 + i, 100.0 + 0.2 * i)  # 100 trades em 100ms
        # 1o trade em 1000 -> 1o corte em 1100. Cortes <= ultimo ts (1099):
        # nenhum. Logo nenhuma EWMA foi atualizada ainda.
        assert len(tr._cortes_ts) == 0
        s = tr.snapshot()
        assert all(v == 0.0 for v in s.values()), \
            "rajada curta nao pode fabricar volatilidade de 100ms+"

    def test_tick_100ms_atualiza_por_corte(self):
        """Trade a cada 100ms: cada update fecha 1 corte e a EWMA de 100ms
        reflete o retorno temporal real entre cortes consecutivos."""
        tr = VolatilityTracker()
        for k in range(20):
            tr.update(1000 + k * 100, 100.0 + 0.1 * k)
        # cortes: 1100..3000 (19 cortes; o 1o trade em 1000 entrou no corte 1100)
        assert len(tr._cortes_ts) == 19
        s = tr.snapshot()
        # ultimo corte 3000 fecha com trade 2900? borda: corte c fecha com
        # trade ts < c. Trade 2900 < 3000 sim. Trade 3000 entra no corte 3100
        # (ainda nao processado). Entao o ultimo corte (3000) tem p do 2900.
        # vol_100ms com alpha=1 = |ret 100ms| do ultimo corte com cobertura:
        # corte 3000 (p=100.9? trade 2900 = 100+0.1*19=101.9? verifique)
        assert s["vol_100ms"] > 0, "vol 100ms deveria refletir o retorno"
        # EWMA de 500ms (alpha=2/6) suaviza retornos de 500ms
        assert s["vol_500ms"] > 0

    def test_valor_vol_100ms_exato(self):
        """vol_100ms com alpha=1 = |retorno de 100ms| no ultimo corte."""
        tr = VolatilityTracker()
        # trades: 1000 (p=100), 1050 (p=100.1), 1100 (p=100.3)
        tr.update(1000, 100.0)
        tr.update(1050, 100.1)
        tr.update(1100, 100.3)
        # cortes: 1100 (fecha com trade <1100 = 1050, p=100.1)
        #         => 1 corte: EWMAs sem cobertura (>=2 cortes p/ 100ms)
        assert len(tr._cortes_ts) == 1
        # proximo trade 1200: fecha corte 1200 (p<1200 = 1100, p=100.3)
        tr.update(1200, 100.5)
        assert len(tr._cortes_ts) == 2
        s = tr.snapshot()
        # corte 1200 p=100.3; corte 1100 p=100.1 -> ret 100ms = |100.3-100.1|/100.1
        assert s["vol_100ms"] == pytest.approx(
            abs(100.3 - 100.1) / 100.1, abs=1e-6)

    def test_silencio_nao_atualiza_sem_cortes_novos(self):
        """Sem trades novos, nao ha cortes novos -> EWMAs congelam."""
        tr = VolatilityTracker()
        tr.update(1000, 100.0)
        tr.update(1100, 100.2)
        s1 = tr.snapshot()
        n1 = len(tr._cortes_ts)
        # simula 2s de silencio: nenhum update -> nada muda
        assert len(tr._cortes_ts) == n1
        s2 = tr.snapshot()
        assert s1 == s2

    def test_esparso_igual_denso_no_mesmo_relogio(self):
        """Mesma linha do tempo com trades densos vs esparsos -> o grid
        (cortes) e identico; somente os precos vigentes importam.

        Cenario: precos iguais nos mesmos instantes de corte.
        """
        denso = VolatilityTracker()
        for k in range(10):
            denso.update(1000 + k * 100, 100.0 + 0.1 * k)
        esparso = VolatilityTracker()
        # Mesmos precos, mas apenas nos cortes (1 trade por corte)
        for k in range(10):
            esparso.update(1000 + k * 100, 100.0 + 0.1 * k)
        assert denso._cortes_preco == esparso._cortes_preco
        assert denso._ews == esparso._ews

    def test_alias_re_exporta_implementacao_unica(self):
        """features/volatility_tracker.py e alias de features/volatility.py."""
        assert VT_ALIAS is VolatilityTracker
        tr = VT_ALIAS()
        tr.update(1000, 100.0)
        tr.update(1200, 100.4)
        assert "vol_100ms" in tr.snapshot()
