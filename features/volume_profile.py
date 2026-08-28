# -*- coding: utf-8 -*-
"""
features/volume_profile.py — VolumeProfileTracker (POC, VAH/VAL).
"""


class VolumeProfileTracker:
    """Volume Profile do dia: acumula volume por nivel de preco."""

    def __init__(self, tick=5, value_area=0.70):
        self.tick = tick
        self.value_area = value_area
        self.volumes = {}
        self.delta = {}

    def atualizar(self, preco, qtd, agressor):
        if preco <= 0 or qtd <= 0:
            return
        nivel = int(preco / self.tick + 0.5) * self.tick
        self.volumes[nivel] = self.volumes.get(nivel, 0) + qtd
        ag = (agressor or '').lower()
        d = qtd if ag in ('compra', 'comprador') else (-qtd if ag in ('venda', 'vendedor') else 0)
        self.delta[nivel] = self.delta.get(nivel, 0) + d

    def reset(self):
        self.volumes = {}
        self.delta = {}

    def calcular(self, preco_atual):
        if not self.volumes or preco_atual <= 0:
            return {'poc_dist': 0, 'vah_dist': 0, 'val_dist': 0,
                    'poc_acima': 0, 'vp_total': 0}
        total = sum(self.volumes.values())
        poc = max(self.volumes, key=self.volumes.get)
        niveis = sorted(self.volumes.keys())
        idx = niveis.index(poc)
        va_vol = self.volumes[poc]
        lo = hi = idx
        while va_vol < self.value_area * total and (lo > 0 or hi < len(niveis) - 1):
            vol_lo = self.volumes[niveis[lo - 1]] if lo > 0 else -1
            vol_hi = self.volumes[niveis[hi + 1]] if hi < len(niveis) - 1 else -1
            if vol_lo >= vol_hi:
                lo -= 1; va_vol += self.volumes[niveis[lo]]
            else:
                hi += 1; va_vol += self.volumes[niveis[hi]]
        return {
            'poc_dist': round(poc - preco_atual, 1),
            'vah_dist': round(niveis[hi] - preco_atual, 1),
            'val_dist': round(niveis[lo] - preco_atual, 1),
            'poc_acima': 1 if poc > preco_atual else 0,
            'vp_total': total,
        }
