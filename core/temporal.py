# -*- coding: utf-8 -*-
"""
core/temporal.py — Contrato temporal único do sistema.

Regra absoluta: o timestamp do evento de mercado (event_ts_ms) NUNCA pode
ser substituído pelo horário de processamento (receive_ts_ns).

Três timestamps são preservados:
  1. event_ts_ms   — timestamp do evento de mercado (do Profit RTD / replay)
  2. receive_ts_ns — momento em que o processo Python recebeu o evento
  3. sequence_id    — ordem determinística local (contador monotônico)

Timezone: America/Sao_Paulo (UTC-3, sem DST em agosto/setembro).
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    TZ_BR = ZoneInfo("America/Sao_Paulo")
except ImportError:
    TZ_BR = timezone(timedelta(hours=-3))

# Epoch fixo para calcular offset do DAT (HH:MM:SS.mmm) quando
# não temos a data completa. É recalculado a cada chamada.
_epoch_midnight_cache = {'date': None, 'epoch_ms': 0}

# Contador global de sequência (thread-safe)
_sequence_counter = 0
_sequence_lock = threading.Lock()


def next_sequence_id() -> int:
    """Retorna próximo ID de sequência monotônico global."""
    global _sequence_counter
    with _sequence_lock:
        _sequence_counter += 1
        return _sequence_counter


def now_ns() -> int:
    """Retorna timestamp atual em nanosegundos (epoch)."""
    return time.time_ns()


def now_ms() -> int:
    """Retorna timestamp atual em milissegundos (epoch)."""
    return int(time.time() * 1000)


def dat_to_epoch_ms(dat_str: str, ref_dt: Optional[datetime] = None) -> int:
    """Converte string DAT do Profit (HH:MM:SS.mmm) para epoch ms.

    Usa a data de hoje (no timezone de Brasília) como referência.
    Se ref_dt for fornecido, usa essa data.

    Args:
        dat_str: string no formato "HH:MM:SS.mmm" ou "HH:MM:SS"
        ref_dt: datetime de referência (default: agora em TZ_BR)

    Returns:
        epoch ms (int) ou 0 se inválido

    Raises:
        Nenhum — retorna 0 para timestamps inválidos.
    """
    if not dat_str or not isinstance(dat_str, str):
        return 0

    s = dat_str.strip()
    if not s:
        return 0

    # Usar data de referência
    if ref_dt is None:
        ref_dt = datetime.now(TZ_BR)
    elif ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=TZ_BR)

    try:
        # Separar hora principal de microssegundos
        principal, _, frac = s.partition(".")
        parts = principal.split(":")
        if len(parts) != 3:
            return 0

        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        ms = 0
        if frac:
            # Frac pode ter 1-6 dígitos (ms ou microssegundos)
            ms = int((frac + "000")[:3])

        # Construir datetime com data de referência + hora do DAT
        dt = ref_dt.replace(
            hour=h, minute=m, second=sec, microsecond=ms * 1000,
            tzinfo=TZ_BR,
        )

        # Converter para epoch ms
        return int(dt.timestamp() * 1000)

    except (ValueError, IndexError, OverflowError):
        return 0


def validate_event_ts(event_ts_ms: int, receive_ts_ns: int,
                       max_future_s: float = 30.0,
                       max_past_s: float = 300.0) -> tuple[bool, str]:
    """Valida timestamp do evento de mercado.

    Regras:
      - event_ts_ms > 0
      - event_ts_ms não pode estar mais de max_future_s no futuro
        (clock drift, dados corrompidos)
      - event_ts_ms não pode estar mais de max_past_s no passado
        (buffer RTD inicial, dados antigos)

    Args:
        event_ts_ms: timestamp do evento em epoch ms
        receive_ts_ns: timestamp de recebimento em epoch ns
        max_future_s: tolerância para o futuro (default 30s)
        max_past_s: tolerância para o passado (default 300s = 5min)
           v14.8: alinhado com o FileStorage (rejeita > 300s). Antes era
           600s no adapter e 300s na gravação — eventos entre 300-600s
           passavam no adapter e eram descartados silenciosamente.
           A gravação é sempre tempo real (baseline absorve o 1º ciclo).

    Returns:
        (valido: bool, motivo: str)
    """
    if event_ts_ms <= 0:
        return False, "timestamp_zero"

    receive_ms = receive_ts_ns // 1_000_000
    diff_s = (event_ts_ms - receive_ms) / 1000.0

    if diff_s > max_future_s:
        return False, f"timestamp_futuro ({diff_s:.1f}s ahead)"

    if diff_s < -max_past_s:
        return False, f"timestamp_passado ({abs(diff_s):.0f}s behind)"

    return True, "ok"


class MonotonicityChecker:
    """Verifica monotonicidade de timestamps por ativo.

    Garante que eventos do mesmo ativo chegam em ordem temporal
    (ou pelo menos não voltam mais de max_backward_s no tempo).

    trades no mesmo milissegundo são aceitos (mesmo timestamp).
    """

    def __init__(self, max_backward_s: float = 5.0):
        self._last_ts: dict[str, int] = {}  # ativo -> último event_ts_ms
        self._max_backward_ms = int(max_backward_s * 1000)

    def check(self, ativo: str, event_ts_ms: int) -> tuple[bool, str]:
        """Verifica se o timestamp é monótono para o ativo.

        Returns:
            (ok: bool, motivo: str)
            ok=True se o timestamp avançou ou repetiu
            ok=False se voltou mais que max_backward_s
        """
        last = self._last_ts.get(ativo, 0)
        if last == 0:
            self._last_ts[ativo] = event_ts_ms
            return True, "primeiro_evento"

        diff = event_ts_ms - last
        if diff < -self._max_backward_ms:
            motivo = (f"timestamp_voltou {abs(diff)}ms "
                       f"(ultimo={last}, atual={event_ts_ms})")
            # Atualizar mesmo assim (não bloquear, só avisar)
            self._last_ts[ativo] = event_ts_ms
            return False, motivo

        self._last_ts[ativo] = max(last, event_ts_ms)
        return True, "ok"

    def reset(self):
        """Reseta estado (para testes ou novo dia)."""
        self._last_ts.clear()
