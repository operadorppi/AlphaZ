# -*- coding: utf-8 -*-
"""
testes/test_timestamp_sessao_anterior_v1513.py — Reconstrução do DAT (P1-A18).

O DAT do Profit (HH:MM:SS.mmm) não carrega DATA. A conversão para epoch usa
uma data de referência. A auditoria apontou risco na virada de meia-noite:
um DAT 23:59:59.xxx recebido após 00:00 seria montado com o dia NOVO
(futuro ~24h) e rejeitado — evento perdido.

Correção defensiva em dat_to_epoch_ms: se o DAT montado ficar no futuro de
ref_dt por mais de uma sessão inteira (> 6h), retrocede 1 dia
(determinístico — compara com ref_dt, não com now).

Cobertura:
  1. Virada de meia-noite retrocede 1 dia e o validate aceita (atraso <1s)
  2. Sessão normal: mesmo dia, sem retrocesso
  3. DAT futuro legítimo (<30s, mesmo dia) não retrocede
  4. A reconstrução antiga (offset = agora_epoch - agora_tod) não existe mais
     no código vivo
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.temporal import dat_to_epoch_ms, validate_event_ts  # noqa: E402

TZ = ZoneInfo("America/Sao_Paulo")


def _dt(ano, mes, dia, hora, minu=0, seg=0, ms=0):
    return datetime(ano, mes, dia, hora, minu, seg, ms * 1000, tzinfo=TZ)


class TestTimestampViradaSessao:
    def test_virada_meia_noite_retrocede_1_dia(self):
        """DAT 23:59:59.500 recebido 00:00:00.100 do dia novo -> dia anterior."""
        ref = _dt(2026, 9, 1, 0, 0, 0, 100)
        ts = dat_to_epoch_ms("23:59:59.500", ref_dt=ref)
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ)

        assert dt.date().day == 31   # agosto
        assert dt.hour == 23 and dt.second == 59

    def test_virada_aceita_pelo_validate(self):
        """Atraso <1s na virada: passado pequeno -> validate aceita."""
        ref = _dt(2026, 9, 1, 0, 0, 0, 100)
        ts = dat_to_epoch_ms("23:59:59.500", ref_dt=ref)
        ref_ms = int(ref.timestamp() * 1000)
        ok, motivo = validate_event_ts(ts, ref_ms * 1_000_000)
        assert ok, f"virada com atraso <1s deveria ser aceita: {motivo}"

    def test_sessao_normal_sem_retrocesso(self):
        """DAT 0.35s no passado do ref, mesmo dia -> sem retrocesso."""
        ref = _dt(2026, 9, 1, 10, 35, 21, 481)
        ts = dat_to_epoch_ms("10:35:21.127", ref_dt=ref)
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ)
        assert dt.date().day == 1 and dt.hour == 10 and dt.minute == 35

    def test_futuro_legitimo_nao_retrocede(self):
        """DAT +20s do ref (mesmo dia, jitter de rede) -> sem retrocesso."""
        ref = _dt(2026, 9, 1, 10, 0, 0)
        ts = dat_to_epoch_ms("10:00:20.000", ref_dt=ref)
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ)
        assert dt.date().day == 1 and dt.hour == 10

    def test_snapshot_antigo_sem_reconstrucao(self):
        """Guard: a reconstrução `offset = agora_epoch - agora_tod` (28/08)
        não existe mais no código vivo."""
        raizes = ["adapters", "core"]
        achou = []
        for raiz in raizes:
            caminho_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), raiz)
            for nome in os.listdir(caminho_dir):
                if not nome.endswith(".py"):
                    continue
                caminho = os.path.join(caminho_dir, nome)
                with open(caminho, encoding="utf-8", errors="replace") as f:
                    if "agora_epoch - agora_tod" in f.read():
                        achou.append(caminho)
        assert achou == [], f"reconstrucao antiga ainda existe em: {achou}"
