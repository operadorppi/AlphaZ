# -*- coding: utf-8 -*-
"""
features/session_time_tracker.py — ALIAS de compatibilidade (P0-A22, v15.16).

Duplicado orfao do SessionTimeTracker (features/session_time.py) que mantinha
o bug do TOD UTC cru (`ts_ms % 86400000`) na classificacao de bloco da
sessao. Implementacao unica vive em features/session_time.py e usa a funcao
temporal oficial de Brasilia (core.temporal). Este modulo apenas re-exporta.
"""

from features.session_time import SessionTimeTracker  # noqa: F401

__all__ = ["SessionTimeTracker"]
