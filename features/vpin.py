# -*- coding: utf-8 -*-
"""
features/vpin.py — VPINTracker (toxicidade do fluxo por bucket de volume).
"""

from collections import deque


class VPINTracker:
    """VPIN clássico: fecha buckets de volume fixo, mede |compra - venda|."""

    def __init__(self, bucket_vol=500, n_buckets=50):
        self.bucket_vol = bucket_vol
        self.n_buckets = n_buckets
        self._compra_bucket = 0
        self._venda_bucket = 0
        self._vol_bucket = 0
        self.imbalances = deque(maxlen=n_buckets)

    def add_evento(self, qtd, agressor):
        restante = qtd
        while restante > 0:
            espaco = self.bucket_vol - self._vol_bucket
            usado = min(restante, espaco)
            if agressor == 'Comprador':
                self._compra_bucket += usado
            elif agressor == 'Vendedor':
                self._venda_bucket += usado
            self._vol_bucket += usado
            restante -= usado
            if self._vol_bucket >= self.bucket_vol:
                imb = abs(self._compra_bucket - self._venda_bucket) / self.bucket_vol
                self.imbalances.append(imb)
                self._compra_bucket = 0
                self._venda_bucket = 0
                self._vol_bucket = 0

    def valor(self):
        if not self.imbalances:
            return 0.0
        return sum(self.imbalances) / len(self.imbalances)
