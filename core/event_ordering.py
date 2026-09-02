# -*- coding: utf-8 -*-
"""
core/event_ordering.py — Detector de ordenamento temporal (Fase 3).

Detecta e classifica anomalias temporais em eventos de mercado:

  1. evento_atrasado    — event_ts_ms está atrás do receive_ts_ns por > limiar
  2. evento_fora_de_ordem — event_ts_ms < último event_ts_ms do mesmo ativo
  3. timestamp_duplicado — mesmo event_ts_ms já visto (mesmo ativo)
  4. salto_temporal     — gap anormal entre eventos consecutivos
  5. sequencia_regressiva — Múltiplos eventos seguidos no passado

Política de descarte/reordenação:
  - NÃO descartar eventos fora de ordem automaticamente.
  - Registrar, classificar e medir primeiro.
  - O consumidor decide o que fazer com a classificação.

Métricas expostas:
  - events_out_of_order
  - events_duplicate
  - events_timestamp_invalid
  - events_late
  - max_event_lag_ms
  - events_forward (salto temporal para frente)
  - events_backward (sequência regressiva)

Uso:
    detector = EventOrderingDetector()
    result = detector.check(ativo, event_ts_ms, receive_ts_ns)
    if result.is_late:
        ...
    stats = detector.get_stats()
"""

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OrderingResult:
    """Resultado da análise de ordenamento de um evento."""
    ativo: str
    event_ts_ms: int
    receive_ts_ns: int
    # Classificações
    is_late: bool = False          # evento atrasado (lag > limiar)
    is_out_of_order: bool = False  # timestamp < último do ativo
    is_duplicate: bool = False     # mesmo timestamp já visto
    is_forward_jump: bool = False  # salto temporal anormal para frente
    is_backward_sequence: bool = False  # sequência regressiva
    # Métricas
    lag_ms: int = 0                # receive_ts - event_ts (latência)
    gap_ms: int = 0                # diferença para o último evento do ativo
    consecutive_backward: int = 0   # quantos eventos seguidos no passado
    # Decisão
    action: str = "ACCEPT"         # ACCEPT, LOG_ONLY, REJECT
    reason: str = "ok"


class EventOrderingDetector:
    """Detector de anomalias temporais com métricas acumuladas.

    Thread-safe via RLock. Mantém estado por ativo (último timestamp,
    timestamps vistos, contador de regressivos).
    """

    def __init__(self,
                 late_threshold_ms: int = 500,
                 forward_jump_threshold_ms: int = 60_000,
                 backward_sequence_threshold: int = 100,
                 max_seen_timestamps: int = 10_000):
        """
        Args:
            late_threshold_ms: lag > que isso = evento atrasado (default 500ms)
            forward_jump_threshold_ms: gap > que isso = salto temporal anormal (default 60s)
            backward_sequence_threshold: N eventos seguidos no passado = sequência regressiva (default 100)
            max_seen_timestamps: máximo de timestamps por ativo na estrutura de dedup (default 10K)
        """
        self._lock = threading.RLock()

        # Limiares
        self._late_threshold_ms = late_threshold_ms
        self._forward_jump_threshold_ms = forward_jump_threshold_ms
        self._backward_sequence_threshold = backward_sequence_threshold
        self._max_seen = max_seen_timestamps

        # Estado por ativo
        self._last_ts: dict[str, int] = {}              # último event_ts_ms por ativo
        self._seen_ts: dict[str, set] = defaultdict(set) # timestamps vistos por ativo
        self._consecutive_backward: dict[str, int] = {} # contador de regressivos seguidos

        # Métricas acumuladas
        self._stats = {
            'events_total': 0,
            'events_accepted': 0,
            'events_out_of_order': 0,
            'events_duplicate': 0,
            'events_timestamp_invalid': 0,
            'events_late': 0,
            'events_forward_jump': 0,
            'events_backward_sequence': 0,
            'max_event_lag_ms': 0,
            'max_gap_ms': 0,
        }

        # Lag máximo por ativo (para dashboard)
        self._max_lag_per_ativo: dict[str, int] = {}

    def check(self, ativo: str, event_ts_ms: int,
              receive_ts_ns: int) -> OrderingResult:
        """Analisa um evento e retorna classificação + ação sugerida.

        Args:
            ativo: símbolo do ativo
            event_ts_ms: timestamp do evento de mercado (epoch ms)
            receive_ts_ns: timestamp de recebimento (epoch ns)

        Returns:
            OrderingResult com classificações e ação sugerida
        """
        result = OrderingResult(
            ativo=ativo,
            event_ts_ms=event_ts_ms,
            receive_ts_ns=receive_ts_ns,
        )

        with self._lock:
            self._stats['events_total'] += 1

            # 1. Timestamp inválido (zero ou negativo)
            if event_ts_ms <= 0:
                result.action = "REJECT"
                result.reason = "timestamp_invalid_zero"
                self._stats['events_timestamp_invalid'] += 1
                return result

            # Calcular lag (latência: quanto tempo entre evento e recebimento)
            receive_ms = receive_ts_ns // 1_000_000
            lag_ms = receive_ms - event_ts_ms
            result.lag_ms = lag_ms

            # Atualizar max lag
            if lag_ms > self._stats['max_event_lag_ms']:
                self._stats['max_event_lag_ms'] = lag_ms
            if lag_ms > self._max_lag_per_ativo.get(ativo, 0):
                self._max_lag_per_ativo[ativo] = lag_ms

            # 2. Evento atrasado (lag > limiar)
            if lag_ms > self._late_threshold_ms:
                result.is_late = True
                self._stats['events_late'] += 1

            last_ts = self._last_ts.get(ativo, 0)

            if last_ts == 0:
                # Primeiro evento do ativo — aceitar sem análise de ordem
                self._last_ts[ativo] = event_ts_ms
                self._seen_ts[ativo].add(event_ts_ms)
                self._consecutive_backward[ativo] = 0
                result.action = "ACCEPT"
                result.reason = "first_event"
                self._stats['events_accepted'] += 1
                return result

            # Calcular gap (diferença para o último evento do ativo)
            gap_ms = event_ts_ms - last_ts
            result.gap_ms = gap_ms

            if abs(gap_ms) > self._stats['max_gap_ms']:
                self._stats['max_gap_ms'] = abs(gap_ms)

            # 3. Timestamp duplicado (mesmo event_ts_ms já visto)
            # v14.6: NÃO rejeitar — burst trades legítimos da B3
            # (mesmo ms, mesmo preço, mesma quantidade, corretoras iguais)
            # Apenas registrar métrica. A dedup real já acontece no
            # adapter (DAT-primary + coerência).
            if event_ts_ms in self._seen_ts[ativo]:
                result.is_duplicate = True
                self._stats['events_duplicate'] += 1

            # 4. Fora de ordem (timestamp < último do ativo)
            if event_ts_ms < last_ts:
                result.is_out_of_order = True
                self._stats['events_out_of_order'] += 1

                # Incrementar contador de regressivos
                cb = self._consecutive_backward.get(ativo, 0) + 1
                self._consecutive_backward[ativo] = cb

                # 5. Sequência regressiva (N eventos seguidos no passado)
                if cb >= self._backward_sequence_threshold:
                    result.is_backward_sequence = True
                    self._stats['events_backward_sequence'] += 1
                    result.action = "LOG_ONLY"
                    result.reason = f"backward_sequence ({cb} consecutive)"
                else:
                    # Fora de ordem isolado — aceitar mas avisar
                    result.action = "ACCEPT"
                    result.reason = "out_of_order_accepted"
                    self._stats['events_accepted'] += 1

                # NÃO atualizar _last_ts para eventos do passado
                # (senão perdemos a referência do mais recente)
            else:
                # Evento em ordem (timestamp >= último)
                self._consecutive_backward[ativo] = 0

                # 6. Salto temporal anormal (gap muito grande para frente)
                if gap_ms > self._forward_jump_threshold_ms:
                    result.is_forward_jump = True
                    self._stats['events_forward_jump'] += 1
                    result.action = "LOG_ONLY"
                    result.reason = f"forward_jump ({gap_ms}ms gap)"

                # Atualizar último timestamp (só para eventos em ordem)
                self._last_ts[ativo] = event_ts_ms

                if result.action == "ACCEPT" or result.action == "LOG_ONLY":
                    if result.reason in ("ok", "first_event", "forward_jump", "out_of_order_accepted"):
                        if result.action != "LOG_ONLY":
                            result.action = "ACCEPT"
                        if result.reason == "ok" or result.reason == "forward_jump":
                            self._stats['events_accepted'] += 1

            # Registrar timestamp visto (controle de memória)
            self._seen_ts[ativo].add(event_ts_ms)
            if len(self._seen_ts[ativo]) > self._max_seen:
                # Limpar timestamps antigos (manter só os mais recentes)
                sorted_ts = sorted(self._seen_ts[ativo])
                self._seen_ts[ativo] = set(sorted_ts[-self._max_seen // 2:])

            return result

    def get_stats(self) -> dict:
        """Retorna métricas acumuladas de ordenamento."""
        with self._lock:
            stats = dict(self._stats)
            stats['max_lag_per_ativo'] = dict(self._max_lag_per_ativo)
            return stats

    def get_stats_for_dashboard(self) -> dict:
        """Retorna métricas formatadas para o dashboard."""
        with self._lock:
            return {
                'events_total': self._stats['events_total'],
                'events_out_of_order': self._stats['events_out_of_order'],
                'events_duplicate': self._stats['events_duplicate'],
                'events_timestamp_invalid': self._stats['events_timestamp_invalid'],
                'events_late': self._stats['events_late'],
                'max_event_lag_ms': self._stats['max_event_lag_ms'],
                'events_forward_jump': self._stats['events_forward_jump'],
                'events_backward_sequence': self._stats['events_backward_sequence'],
                'events_accepted': self._stats['events_accepted'],
            }

    def reset(self):
        """Reseta estado (para testes ou novo dia)."""
        with self._lock:
            self._last_ts.clear()
            self._seen_ts.clear()
            self._consecutive_backward.clear()
            self._stats = {k: 0 for k in self._stats}
            self._max_lag_per_ativo.clear()
