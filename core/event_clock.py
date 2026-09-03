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

    def tod_de_ts(self, ts_ms):
        """Normaliza timestamp para time-of-day em ms (hora de Brasilia).

        P0-A22 (v15.16): delega a funcao temporal OFICIAL
        (core.temporal.tod_de_ts_br, UTC-3 fixo America/Sao_Paulo). ANTES
        usava o offset do fuso da maquina (quebrava fora de SP).
        """
        from core.temporal import tod_de_ts_br
        return tod_de_ts_br(ts_ms)

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
