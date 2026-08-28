# -*- coding: utf-8 -*-
"""
core/event_clock.py — Master clock, timestamps, virada de dia.

Centraliza:
  - Conversão TOD (time-of-day) ↔ epoch
  - Detecção de virada de dia por ativo
  - Reset de sessão
  - parse_hms_ms: parsing de timestamps HH:MM:SS do RTD
"""

import time
import threading
import logging
from datetime import date, datetime
from core.utils import parse_hms_ms

log = logging.getLogger(__name__)


class EventClock:
    """Relógio mestre do motor. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.dia_atual = date.today()
        self.session_start = time.time()

    def _offset_local_utc_ms(self):
        """Deslocamento (ms) que a hora local adianta da UTC."""
        ag = datetime.now().astimezone()
        return int(ag.utcoffset().total_seconds() * 1000) if ag.utcoffset() else 0

    def tod_de_ts(self, ts_ms):
        """Normaliza timestamp para time-of-day em ms (hora local B3)."""
        if ts_ms and ts_ms > 1e11:
            utc_tod = ts_ms % 86400000
            return (utc_tod + self._offset_local_utc_ms()) % 86400000
        return ts_ms or 0

    def agora_tod_ms(self):
        """TOD atual em ms."""
        return int(time.time() * 1000) % 86400000

    def agora_epoch_ms(self):
        """Epoch atual em ms."""
        return int(time.time() * 1000)

    def virou_dia(self):
        """Retorna True se o dia mudou desde a última chamada."""
        hoje = date.today()
        with self._lock:
            if hoje != self.dia_atual:
                self.dia_atual = hoje
                return True
            return False

    def segundos_desde_inicio(self):
        """Segundos desde o início da sessão."""
        return time.time() - self.session_start

    def reset_sessao(self):
        """Reseta o início de sessão (após reconexão)."""
        with self._lock:
            self.session_start = time.time()
            self.dia_atual = date.today()
