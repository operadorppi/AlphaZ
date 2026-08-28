# -*- coding: utf-8 -*-
"""
core/metrics.py — Cálculo de métricas e estatísticas.

Extrai de Analise:
  - calcular_metricas (acuracia, PF, Sharpe, drawdown, expectancy)
  - get_estatisticas (total, acertos, pesos, acuracia_por_feature)
  - get_memoria (contadores globais)
"""

import logging

log = logging.getLogger(__name__)


class Metrics:
    """Cálculo de métricas de desempenho do motor."""

    def __init__(self, resultados=None, previsoes=None, pesos=None,
                 feature_hits=None, acuracia=None):
        self.resultados = resultados or []
        self.previsoes = previsoes or []
        self.pesos = pesos or {}
        self.feature_hits = feature_hits or {}
        self.acuracia = acuracia or {}

    def calcular(self):
        """Calcula métricas agregadas: acuracia, PF, Sharpe, DD, expectancy."""
        if len(self.resultados) < 2:
            return {}
        import numpy as np
        pnls = np.array([r['delta'] for r in self.resultados])
        acertos = sum(1 for r in self.resultados if r['acertou'])
        total = len(self.resultados)
        acuracia = acertos / total

        ganhos = pnls[pnls > 0].sum()
        perdas = abs(pnls[pnls < 0].sum())
        profit_factor = ganhos / perdas if perdas > 0 else float('inf')

        media = np.mean(pnls)
        std = np.std(pnls)

        dias = len(set(r.get('ts', '')[:10] for r in self.resultados if r.get('ts')))
        trades_por_dia = max(len(pnls) / max(1, dias), 1)
        sharpe = (media / std * np.sqrt(252 * trades_por_dia)) if std > 0 else 0

        cumsum = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumsum)
        drawdown = running_max - cumsum
        max_dd = drawdown.max()

        return {
            'acuracia': round(acuracia, 4),
            'profit_factor': round(profit_factor, 2),
            'sharpe': round(sharpe, 2),
            'max_drawdown': round(float(max_dd), 1),
            'expectancy': round(float(media), 2),
            'total_trades': total,
            'ganhos': int(acertos),
            'perdas': total - acertos,
        }

    def get_estatisticas(self):
        """Estatísticas de aprendizado."""
        ac = sum(1 for r in self.resultados if r['acertou'])
        total = len(self.resultados)
        return {
            'total': total, 'acertos': ac,
            'acuracia': ac / total if total > 0 else 0,
            'pesos': dict(self.pesos),
            'acuracia_por_feature': dict(self.acuracia),
            'resultados': list(self.resultados[-20:])
        }
