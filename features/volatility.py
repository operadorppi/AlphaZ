# -*- coding: utf-8 -*-
"""
features/volatility.py — Volatilidade multi-horizonte TEMPORAL (P0-A21, v15.15).

O tracker ANTIGO declarava janelas 100ms..5min mas indexava o buffer por
CONTAGEM de trades (n=1,5,10,...) — com mercado calmo, "10 trades" levavam 2s;
em rajada, ocorriam em 20ms. A feature media N trades, nao N milissegundos.

O batch (GeradorJanelas + features_expansao) amostra o MASTER CLOCK: cortes
regulares de 100ms (forward-filled). Este tracker replica ESSA semantica
para paridade live x batch:

    - cada corte de 100ms do relogio fecha com o preco do ULTIMO trade com
      ts ESTRITAMENTE menor que o corte (mesma borda do GeradorJanelas: o
      trade que dispara a emissao do corte entra no corte SEGUINTE);
    - a cada corte, atualiza a EWMA do horizonte H:
        ret_H(corte) = |p(corte) - p(corte - H)| / p(corte - H)
        alpha = 2 / (H_linhas + 1)      (mesmo ewm do batch por horizonte)
    - cortes sem trade novo tem p constante -> ret_H = 0 -> EWMA decai
      (igual ao batch com preco forward-filled);
    - sem cobertura temporal (poucos cortes) a EWMA NAO atualiza (o batch
      ignora o NaN inicial do pct_change).

Horizontes (nome = tempo REAL no grid de 100ms):
    vol_100ms (1 linha), vol_500ms (5), vol_1s (10), vol_5s (50),
    vol_15s (150), vol_1min (600), vol_5min (3000)
"""

from bisect import bisect_left, bisect_right

_GRID_MS = 100
# (nome_feature, horizonte em LINHAS de 100ms) — linha x 100ms = tempo real
_HORIZONTES = [
    ("100ms", 1),
    ("500ms", 5),
    ("1s", 10),
    ("5s", 50),
    ("15s", 150),
    ("1min", 600),
    ("5min", 3000),
]
_MAX_HISTORIA_LINHAS = 3000 + 100       # maior horizonte (5min) + folga
_MAX_HISTORIA_TRADES_MS = 310_000       # 5min + 10s de trades p/ as-of
_MAX_ENTRADAS = 200_000


class VolatilityTracker:
    """EWMA causal de volatilidade por horizonte temporal (grid 100ms).

    Uso:
        tracker.update(ts_ms, preco)   # a cada trade (ts do evento)
        tracker.snapshot()             # -> {'vol_100ms': 0.0, ...}
    """

    def __init__(self):
        self._times = []      # ts_ms dos trades (ordenado, podado por idade)
        self._precos = []     # precos correspondentes
        self._cortes_ts = []  # ts de cada corte de 100ms processado
        self._cortes_preco = []  # preco de fechamento de cada corte
        self._ews = {nome: 0.0 for nome, _ in _HORIZONTES}
        self._proximo_corte = None  # proximo corte de grid a processar

    def update(self, ts_ms, preco):
        """Registra um preco no instante ts_ms e avanca os cortes de 100ms
        pendentes (cortes intermediarios inclusos — forward-filled)."""
        if preco is None or preco <= 0 or ts_ms is None:
            return
        ts_ms = int(ts_ms)
        # Buffer de trades ordenado (eventos fora de ordem nao corrompem)
        if self._times and ts_ms >= self._times[-1]:
            self._times.append(ts_ms)
            self._precos.append(float(preco))
        else:
            i = bisect_right(self._times, ts_ms)
            self._times.insert(i, ts_ms)
            self._precos.insert(i, float(preco))

        if self._proximo_corte is None:
            # 1o corte: o PRIMEIRO corte APOS o 1o evento (mesmo do GeradorJanelas)
            self._proximo_corte = (ts_ms // _GRID_MS + 1) * _GRID_MS
            self._poda_trades()
            return

        self._poda_trades()
        while self._proximo_corte <= ts_ms:
            p = self._preco_antes_de(self._proximo_corte)
            if p is not None:
                self._processar_corte(self._proximo_corte, p)
            self._proximo_corte += _GRID_MS

    def _poda_trades(self):
        """Remove trades fora da cobertura temporal do maior horizonte."""
        if self._times:
            corte_ref = self._times[-1] - _MAX_HISTORIA_TRADES_MS
            n = 0
            for t in self._times:
                if t < corte_ref:
                    n += 1
                else:
                    break
            if n:
                del self._times[:n]
                del self._precos[:n]
        excesso = len(self._times) - _MAX_ENTRADAS
        if excesso > 0:
            del self._times[:excesso]
            del self._precos[:excesso]

    def _preco_antes_de(self, corte_ts):
        """Preco do ultimo trade com ts ESTRITAMENTE menor que corte_ts."""
        if not self._times:
            return None
        i = bisect_left(self._times, corte_ts) - 1
        if i < 0:
            return None
        return self._precos[i]

    def _processar_corte(self, corte_ts, p):
        """Fecha 1 corte de 100ms e atualiza as EWMAs dos horizontes."""
        self._cortes_ts.append(corte_ts)
        self._cortes_preco.append(float(p))
        excesso = len(self._cortes_ts) - _MAX_HISTORIA_LINHAS
        if excesso > 0:
            del self._cortes_ts[:excesso]
            del self._cortes_preco[:excesso]

        for nome, n_linhas in _HORIZONTES:
            if len(self._cortes_preco) <= n_linhas:
                continue  # sem cobertura temporal — EWMA nao atualiza
            prev = self._cortes_preco[-n_linhas - 1]
            if prev <= 0:
                continue
            ret = abs(p - prev) / prev
            alpha = 2.0 / (n_linhas + 1)
            self._ews[nome] = alpha * ret + (1 - alpha) * self._ews[nome]

    def snapshot(self):
        """Estado atual das EWMAs de cada horizonte temporal."""
        return {f"vol_{nome}": round(v, 6) for nome, v in self._ews.items()}

    def reset_diario(self):
        """v12.2: Reset diário para evitar acúmulo entre dias."""
        self._times = []
        self._precos = []
        self._cortes_ts = []
        self._cortes_preco = []
        self._ews = {nome: 0.0 for nome, _ in _HORIZONTES}
        self._proximo_corte = None
