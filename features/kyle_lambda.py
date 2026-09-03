# -*- coding: utf-8 -*-
"""
features/kyle_lambda.py — KyleLambdaTracker (impacto de preço / liquidez).
"""


class KyleLambdaTracker:
    """Kyle's Lambda: regressao dP ~ lambda*V_signed."""

    def __init__(self, janela=200):
        self.janela = janela
        self._dv = []
        self._dp = []
        self._ultimo_preco = None

    def reset(self):
        self._dv = []
        self._dp = []
        self._ultimo_preco = None

    def reset_diario(self):
        """v12.2: Reset diário para evitar acúmulo entre dias."""
        self.reset()

    def atualizar(self, preco, qtd, agressor):
        if preco <= 0:
            return
        if self._ultimo_preco is not None:
            ag = (agressor or '').lower()
            sv = qtd if ag in ('compra', 'comprador') else (-qtd if ag in ('venda', 'vendedor') else 0)
            self._dv.append(sv)
            self._dp.append(preco - self._ultimo_preco)
            if len(self._dv) > self.janela:
                self._dv.pop(0); self._dp.pop(0)
        self._ultimo_preco = preco

    def calcular(self):
        n = len(self._dv)
        if n < 20:
            return {'kyle_lambda': 0.0, 'kyle_n': n}
        mv = sum(self._dv) / n; mp = sum(self._dp) / n
        cov = sum((v - mv) * (p - mp) for v, p in zip(self._dv, self._dp)) / n
        var = sum((v - mv) ** 2 for v in self._dv) / n
        lam = cov / var if var > 0 else 0.0
        return {'kyle_lambda': round(lam, 6), 'kyle_n': n}
