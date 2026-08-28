# volatility_tracker.py — Volatilidade multi-timeframe ao vivo (v9.37)
# EWMA causal de |ret| em 7 janelas: 100ms a 5min.
import time
_VE = 1e-9
_WINDOWS = [(1,"100ms"),(5,"500ms"),(10,"1s"),(50,"5s"),(100,"15s"),(300,"1min"),(1500,"5min")]

class VolatilityTracker:
    def __init__(self):
        self._buf = []  # historico de precos (max 1500)
        self._ews = {}  # nome -> ewma value
        for _, n in _WINDOWS:
            self._ews[n] = 0.0

    def update(self, preco):
        if preco is None or preco <= 0: return
        self._buf.append(float(preco))
        if len(self._buf) > 1600: self._buf = self._buf[-1500:]
        # Atualizar EWMA para cada janela
        for n, nome in _WINDOWS:
            if len(self._buf) > n:
                ret = abs(self._buf[-1] - self._buf[-n-1]) / max(self._buf[-n-1], _VE)
                alpha = 2.0 / (n + 1)
                self._ews[nome] = alpha * ret + (1 - alpha) * self._ews[nome]

    def snapshot(self):
        return {f"vol_{k}": round(v, 6) for k, v in self._ews.items()}
