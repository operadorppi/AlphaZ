# -*- coding: utf-8 -*-
"""
testes/test_sessao_temporal_v1516.py — Fonte unica temporal de Brasilia (P0-A22).

O SessionTimeTracker ANTIGO usava `ts_ms % 86400000` (TOD UTC) no snapshot()
enquanto o dia usava -3h — a classificacao de bloco da sessao ficava
deslocada +3h (14h BRT = 17h UTC virava 'fechamento' em vez de 'tarde').
O mesmo bug existia no VolumeRelativoTracker (volume de 14h+ BRT nem era
contabilizado) e no batch (features_expansao).

Cobertura:
  1. tod_de_ts_br / dia_de_ts_br (funcoes oficiais) com epoch deterministico
  2. SessionTimeTracker classifica 14h BRT como 'tarde' (bloco 4)
  3. Pre-abertura 08:30 BRT -> bloco 0
  4. VolumeRelativoTracker contabiliza volume de 14h BRT (minuto 300)
  5. EventClock.tod_de_ts delega a fonte unica
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.temporal import tod_de_ts_br, dia_de_ts_br  # noqa: E402


def _epoch_brt(ano, mes, dia, hora, minu=0):
    """Epoch ms de um horario de Brasilia (UTC-3).

    14:00 BRT = 17:00 UTC (UTC adianta 3h de BRT), entao o epoch e o
    datetime UTC de (hora+3h).
    """
    utc = datetime(ano, mes, dia, hora, minu, tzinfo=timezone.utc) + timedelta(hours=3)
    return int(utc.timestamp() * 1000)


class TestFonteUnicaTemporal:
    def test_tod_de_ts_br_14h_brt(self):
        """14:00 BRT (17:00 UTC) -> TOD de 14h em ms."""
        ts = _epoch_brt(2026, 8, 29, 14, 0)
        assert tod_de_ts_br(ts) == 14 * 3600 * 1000

    def test_tod_de_ts_br_meia_noite(self):
        """00:30 BRT (03:30 UTC) -> 30min (nao 3h30)."""
        ts = _epoch_brt(2026, 8, 29, 0, 30)
        assert tod_de_ts_br(ts) == 30 * 60 * 1000

    def test_dia_de_ts_br(self):
        """Dia civil BR: 00:30 BRT do dia D pertence ao dia D (nao ao dia\n        anterior, que seria o caso em UTC)."""
        ts = _epoch_brt(2026, 8, 29, 0, 30)  # 03:30 UTC do dia 29
        dia_br = dia_de_ts_br(ts)
        dia_utc = int(ts) // 86_400_000
        assert dia_br == dia_utc  # 03:30 UTC e dia 29 nos dois

        ts2 = _epoch_brt(2026, 8, 28, 23, 30)  # 02:30 UTC de 29/08
        # Em UTC o dia civil seria 29; em BR ainda e 28 (23:30 BRT)
        assert dia_de_ts_br(ts2) == int(ts2) // 86_400_000 - 1

    def test_session_time_bloco_14h_eh_tarde(self):
        """Cenario do achado: 14h BRT ANTES virava 17h UTC -> bloco 5
        (fechamento). Agora: bloco 4 (tarde) com TOD BR correto."""
        from features.session_time import SessionTimeTracker

        tr = SessionTimeTracker()
        ts = _epoch_brt(2026, 8, 29, 14, 0)
        tr.update(ts)
        s = tr.snapshot(ts)
        assert s["bloco_sessao"] == 4, f"14h BRT deveria ser tarde: {s}"
        # 14h - 9h = 5h = 300 min desde abertura
        assert s["minutos_desde_abertura"] == pytest.approx(300.0)
        # ate 17:45 = 3h45 = 225 min
        assert s["minutos_ate_fechamento"] == pytest.approx(225.0)

    def test_session_time_pre_abertura(self):
        """08:30 BRT -> bloco 0 (pre-abertura)."""
        from features.session_time import SessionTimeTracker

        tr = SessionTimeTracker()
        ts = _epoch_brt(2026, 8, 29, 8, 30)
        s = tr.snapshot(ts)
        assert s["bloco_sessao"] == 0

    def test_session_time_abertura_9h(self):
        """09:30 BRT -> bloco 1 (abertura)."""
        from features.session_time import SessionTimeTracker

        tr = SessionTimeTracker()
        ts = _epoch_brt(2026, 8, 29, 9, 30)
        s = tr.snapshot(ts)
        assert s["bloco_sessao"] == 1

    def test_volume_relativo_contabiliza_14h_brt(self):
        """14h BRT: ANTES o minuto via UTC (480) ficava fora do range 0-405
        e o volume nem era contabilizado. Agora minuto 300 e registrado."""
        from features.volume_relativo import VolumeRelativoTracker

        tr = VolumeRelativoTracker()
        ts = _epoch_brt(2026, 8, 29, 14, 5)
        tr.update(100, ts)
        # 14:05 - 09:00 = 305 min
        assert tr._volume_por_minuto.get(305, 0.0) == 100.0
        assert tr._minuto_atual == 305

    def test_event_clock_delega_fonte_unica(self):
        """EventClock.tod_de_ts e identico a core.temporal.tod_de_ts_br."""
        from core.event_clock import EventClock

        ts = _epoch_brt(2026, 8, 29, 14, 0)
        assert EventClock().tod_de_ts(ts) == 14 * 3600 * 1000
        assert EventClock().tod_de_ts(ts) == tod_de_ts_br(ts)

    def test_alias_duplicados(self):
        """Aliases re-exportam a implementacao unica."""
        from features.session_time_tracker import SessionTimeTracker as ST2
        from features.session_time import SessionTimeTracker as ST1
        from features.volume_relativo_tracker import VolumeRelativoTracker as VR2
        from features.volume_relativo import VolumeRelativoTracker as VR1
        assert ST2 is ST1 and VR2 is VR1
