# -*- coding: utf-8 -*-
"""
core/utils.py — Funções utilitárias partilhadas.
"""

import re
from datetime import datetime


def fnum(v, d=0.0):
    """Converte para float com valor padrão se falhar."""
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def fint(v, d=0):
    """Converte para int com valor padrão se falhar."""
    try:
        return int(float(v)) if v is not None else d
    except (TypeError, ValueError):
        return d


def sstr(v):
    """Converte para string removendo espaços em branco."""
    return "" if v is None else str(v).strip()


_RE_HMS = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,6}))?')


def parse_hms_ms(v):
    """Parse de timestamp HH:MM:SS(.frac) do RTD para time-of-day em ms."""
    m = _RE_HMS.search(str(v))
    if not m:
        return 0
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    frac = m.group(4) or ''
    ms = int(frac.ljust(3, '0')[:3]) if frac else 0
    return ((h * 3600 + mi * 60 + s) * 1000) + ms


def tod_ms(dt=None):
    """Retorna o time-of-day em milissegundos."""
    dt = dt or datetime.now()
    return ((dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000) + dt.microsecond // 1000