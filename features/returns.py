# returns_tracker.py — Retornos multi-horizonte ao vivo (v9.37)
_HORIZONS = [(1,"100ms"),(5,"500ms"),(10,"1s"),(50,"5s"),(100,"15s"),(300,"1min"),(500,"5min")]

class ReturnsTracker:
    def __init__(self):
        self._buf = []

    def update(self, preco):
        if preco is None or preco <= 0: return
        self._buf.append(float(preco))
        if len(self._buf) > 600: self._buf = self._buf[-500:]

    def snapshot(self):
        s = {}
        p = self._buf[-1] if self._buf else None
        if p is None or p <= 0: return s
        for n, nome in _HORIZONS:
            if len(self._buf) > n:
                prev = self._buf[-n-1]
                if prev > 0:
                    s[f"retorno_{n}x100ms"] = (p - prev) / prev
                else:
                    s[f"retorno_{n}x100ms"] = None
            else:
                s[f"retorno_{n}x100ms"] = None
        return s
