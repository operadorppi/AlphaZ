import numpy as np

class VWAPTracker:
    """Mantem VWAP intraday causal por (ativo, dia) com reset diario."""

    def __init__(self, ativo, tick=5.0):
        self.ativo = ativo
        self.tick = tick
        self._pv = 0.0
        self._vol = 0
        self._ultimo_ts = None
        self._ultimo_dia = None
        self.vwap = np.nan
        self.dist_vwap_pts = np.nan
        self.acima_vwap = 0.0
        self.abaixo_vwap = 0.0
        self.cruzou_vwap = 0.0
        self._lado_prev = None
        self.vol_total = 0
        self._primeiro_preco = None

    def reset_diario(self):
        self._pv = 0.0
        self._vol = 0
        self.vwap = np.nan
        self.dist_vwap_pts = np.nan
        self.acima_vwap = 0.0
        self.abaixo_vwap = 0.0
        self.cruzou_vwap = 0.0
        self._lado_prev = None
        self._primeiro_preco = None

    def _dia_brt(self, ts_ms):
        return (int(ts_ms) - 3 * 3600 * 1000) // 86_400_000

    def update(self, ts_ms, preco, qtd):
        """Atualiza VWAP com um novo negocio (causal)."""
        if preco is None or qtd is None or qtd <= 0 or preco <= 0:
            return
        dia = self._dia_brt(ts_ms)
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            self.reset_diario()
        self._ultimo_dia = dia
        if self._primeiro_preco is None:
            self._primeiro_preco = preco
        self._pv += float(preco) * float(qtd)
        self._vol += int(qtd)
        self.vol_total = self._vol
        if self._vol > 0:
            self.vwap = self._pv / self._vol
        else:
            self.vwap = np.nan
        self.dist_vwap_pts = preco - self.vwap if not np.isnan(self.vwap) else np.nan
        lado = 1 if preco > self.vwap else (0 if preco < self.vwap else None)
        self.acima_vwap = 1.0 if lado == 1 else 0.0
        self.abaixo_vwap = 1.0 if lado == 0 else 0.0
        if self._lado_prev is not None and lado is not None and lado != self._lado_prev:
            self.cruzou_vwap = 1.0
        else:
            self.cruzou_vwap = 0.0
        self._lado_prev = lado
        self._ultimo_ts = ts_ms

    def snapshot(self):
        return {
            'vwap': self.vwap,
            'dist_vwap_pts': self.dist_vwap_pts,
            'acima_vwap': self.acima_vwap,
            'abaixo_vwap': self.abaixo_vwap,
            'cruzou_vwap': self.cruzou_vwap,
            'vol_total': self.vol_total,
        }