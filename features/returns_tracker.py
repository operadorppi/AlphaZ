# -*- coding: utf-8 -*-
"""
features/returns_tracker.py — ALIAS de compatibilidade (P0-A20, v15.14).

Este arquivo era um duplicado ORFAO do ReturnsTracker (features/returns.py)
e mantinha o bug de indexar janelas por CONTAGEM de trades (1 trade = 100ms).
Qualquer import antigo (`from features.returns_tracker import ...`) pegava a
versao vazada silenciosamente.

Implementacao unica agora vive em features/returns.py (janelas por master
clock, previous-tick/as-of). Este modulo apenas re-exporta — nao existe mais
codigo duplicado para divergir.
"""

from features.returns import ReturnsTracker  # noqa: F401
from features.returns import _HORIZONTES_MS  # noqa: F401

__all__ = ["ReturnsTracker"]
