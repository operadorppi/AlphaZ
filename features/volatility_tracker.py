# -*- coding: utf-8 -*-
"""
features/volatility_tracker.py — ALIAS de compatibilidade (P0-A21, v15.15).

Este arquivo era um duplicado ORFAO do VolatilityTracker (features/volatility.py)
e mantinha o bug de indexar janelas por CONTAGEM de trades (1 trade = 100ms).
Qualquer import antigo (`from features.volatility_tracker import ...`) pegava
a versao vazada silenciosamente.

Implementacao unica agora vive em features/volatility.py (grid temporal de
100ms do master clock, borda de corte identica ao GeradorJanelas/batch).
Este modulo apenas re-exporta — nao existe mais codigo duplicado p/ divergir.
"""

from features.volatility import VolatilityTracker  # noqa: F401
from features.volatility import _HORIZONTES  # noqa: F401

__all__ = ["VolatilityTracker"]
