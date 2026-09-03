# -*- coding: utf-8 -*-
"""
features/volume_relativo_tracker.py — ALIAS de compatibilidade (P0-A22, v15.16).

Duplicado orfao do VolumeRelativoTracker (features/volume_relativo.py) que
mantinha o bug do minuto do dia em TOD UTC (`ts % 86400000`) — volume de 14h
BRT nao era contabilizado. Implementacao unica vive em features/volume_relativo.py
e usa as funcoes temporais oficiais de Brasilia (core.temporal).
Este modulo apenas re-exporta.
"""

from features.volume_relativo import VolumeRelativoTracker  # noqa: F401

__all__ = ["VolumeRelativoTracker"]
