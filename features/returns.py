# -*- coding: utf-8 -*-
"""
features/returns.py — Retornos multi-horizonte TEMPORAIS (P0-A20, v15.14).

O tracker ANTIGO guardava apenas preços e interpretava cada posição como
100ms fixos (retorno_1x100ms = retorno entre 2 negócios consecutivos). Com
rajadas (100 trades em 20ms) ou silêncio (nenhum trade por 2s), a "janela"
medida nada tinha a ver com o tempo — skew live x batch.

Agora as janelas são definidas pelo MASTER CLOCK (timestamp do evento):

    retorno_Hms(t) = preco_asof(t) / preco_asof(t - H) - 1

Política de amostragem: PREVIOUS-TICK / AS-OF. Para o instante alvo
(t - H), usa o último preço com ts <= alvo (bisect). Se não existe tick
até o alvo, retorna None (janela sem cobertura temporal).

Nomes das features PRESERVADOS (retorno_Nx100ms = retorno do horizonte de
N x 100ms reais, contrato do batch ml/features_expansao.py) — apenas a
semântica muda de "N trades" para "N x 100ms".
"""

from bisect import bisect_right

# horizonte em ms -> nome do retorno no manifest/modelo (nomes legados).
# O nome retorno_Nx100ms = retorno do horizonte de N x 100ms do master clock
# (mesmo contrato do batch ml/features_expansao.py, que aplica pct_change(N)
# sobre o grid de 100ms). 500x100ms = 50s (nao 5min como um antigo rotulo
# incorreto do feature_manifest descrevia).
_HORIZONTES_MS = [
    (100, "retorno_1x100ms"),
    (500, "retorno_5x100ms"),
    (1000, "retorno_10x100ms"),
    (5000, "retorno_50x100ms"),
    (10000, "retorno_100x100ms"),
    (15000, "retorno_150x100ms"),
    (30000, "retorno_300x100ms"),
    (50000, "retorno_500x100ms"),
]
# Cobertura temporal do buffer: maior horizonte (50s) + folga
_MAX_HISTORIA_MS = 60_000 + 10_000
# Limite de segurança de entradas (rajadas extremas: 100k trades em 60s)
_MAX_ENTRADAS = 200_000


class ReturnsTracker:
    """Retornos por horizonte TEMPORAL com amostragem previous-tick/as-of.

    Uso:
        tracker.update(ts_ms, preco)   # a cada trade (ts do evento, master clock)
        tracker.snapshot()             # -> {'retorno_1x100ms': 0.0012, ...}
        tracker.snapshot(ts_ms)        # snapshot em um instante arbitrario (as-of)
    """

    def __init__(self):
        self._times = []   # ts_ms ordenados (evento, master clock)
        self._precos = []  # preco correspondente
        self._max_ms = _MAX_HISTORIA_MS
        self._max_entradas = _MAX_ENTRADAS

    def update(self, ts_ms, preco):
        """Registra um preco no instante ts_ms (master clock do evento)."""
        if preco is None or preco <= 0:
            return
        ts_ms = int(ts_ms)
        # Eventos fora de ordem (atrasados) ainda sao registrados, mas o
        # buffer permanece ordenado por ts para o bisect as-of.
        if self._times and ts_ms >= self._times[-1]:
            self._times.append(ts_ms)
            self._precos.append(float(preco))
        else:
            i = bisect_right(self._times, ts_ms)
            self._times.insert(i, ts_ms)
            self._precos.insert(i, float(preco))
        self._poda()

    def _poda(self):
        """Remove entradas fora da cobertura temporal (amortizado O(1))."""
        if self._times:
            corte = self._times[-1] - self._max_ms
            # Poda por idade: descarta do inicio enquanto antigos demais
            n_descartar = 0
            for t in self._times:
                if t < corte:
                    n_descartar += 1
                else:
                    break
            if n_descartar:
                del self._times[:n_descartar]
                del self._precos[:n_descartar]
        # Poda por tamanho (rajada extrema): mantem os mais recentes
        excesso = len(self._times) - self._max_entradas
        if excesso > 0:
            del self._times[:excesso]
            del self._precos[:excesso]

    def _preco_asof(self, ts_ms):
        """Preco do ultimo tick com ts <= ts_ms (previous-tick/as-of)."""
        if not self._times:
            return None
        i = bisect_right(self._times, ts_ms) - 1
        if i < 0:
            return None
        return self._precos[i]

    def snapshot(self, ts_ms=None):
        """Retornos de cada horizonte temporal fechado no instante ts_ms.

        ts_ms default: o tick mais recente do buffer. None nas janelas sem
        cobertura temporal (nao existia tick ate o alvo t - H).
        """
        s = {}
        if not self._times:
            return s
        if ts_ms is None:
            ts_ms = self._times[-1]
        p = self._preco_asof(ts_ms)
        if p is None or p <= 0:
            return s
        for h_ms, nome in _HORIZONTES_MS:
            prev = self._preco_asof(ts_ms - h_ms)
            if prev is None or prev <= 0:
                s[nome] = None
            else:
                s[nome] = (p - prev) / prev
        return s

    def reset_diario(self):
        """v12.2: Reset diário para evitar acúmulo entre dias."""
        self._times = []
        self._precos = []
