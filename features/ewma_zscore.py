# -*- coding: utf-8 -*-
"""
features/ewma_zscore.py — EWMAZScore (z-score por EWMA, estacionaridade).
"""

import math


class EWMAZScore:
    """Z-score por EWMA da média e da média do quadrado (O(1) por valor)."""

    def __init__(self, alpha=0.01, min_amostras=100, piso=1e-9):
        self.alpha = alpha
        self.min_amostras = min_amostras
        self.piso = piso
        self._media = 0.0
        self._media_quad = 0.0
        self._n = 0

    def atualizar(self, x):
        a = self.alpha
        self._media = (1 - a) * self._media + a * x
        self._media_quad = (1 - a) * self._media_quad + a * x * x
        self._n += 1

    def z(self, x):
        if self._n < self.min_amostras:
            return 0.0
        var = self._media_quad - self._media * self._media
        std = math.sqrt(var) if var > 0 else 0.0
        if std < self.piso:
            return 0.0
        return (x - self._media) / std
