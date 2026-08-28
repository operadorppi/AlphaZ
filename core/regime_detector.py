# -*- coding: utf-8 -*-
"""
core/regime_detector.py — Detecção de regime bidimensional (direção × volatilidade).

Extrai de Analise:
  - detectar_regime (linha 2936)
  - ajustar_por_regime (linha 2991)
  - Cache de regime por 5s (evita recomputação O(n) no lock)
"""

import time
import logging

from features import PercentilTracker

log = logging.getLogger(__name__)


class RegimeDetector:
    """Detecta regime de mercado: direção × volatilidade.

    A classificação de volatilidade usa distribuição de percentis do range
    realizado (PercentilTracker por ativo) em vez de limiares absolutos fixos,
    tornando o detector adaptativo ao ativo e ao período do dia. Quando ainda
    não há amostras suficientes, cai para os limiares fixos originais.
    """

    # Janela de observação (pontos de 1s) para direção/vol.
    LOOKBACK = 600
    # Janela do tracker de percentis de volatilidade (segundos).
    VOL_PERC_JANELA_S = 1800
    VOL_PERC_MIN = 60

    def __init__(self, config=None):
        self.config = config or {}
        self._cache = {}
        self._vol_perc = {}

    def _classificar_vol(self, ativo, vol_realizada, ts_agora):
        """Classifica volatilidade via percentis adaptativos, com fallback fixo."""
        pt = self._vol_perc.setdefault(
            ativo, PercentilTracker(janela_segs=self.VOL_PERC_JANELA_S,
                                    amostra_minima=self.VOL_PERC_MIN))
        pt.add(vol_realizada, ts=ts_agora)
        if pt.percentil(0.8, None) is None:
            # Sem distribuição suficiente — limiares fixos originais
            if vol_realizada > 100:
                return 'alta'
            if vol_realizada < 20:
                return 'baixa'
            return 'normal'
        p20 = pt.percentil(0.2, vol_realizada)
        p80 = pt.percentil(0.8, vol_realizada)
        if vol_realizada >= p80:
            return 'alta'
        if vol_realizada <= p20:
            return 'baixa'
        return 'normal'

    def detectar(self, ativo, historico):
        """Regime bidimensional: direcao x volatilidade.
        Returns dict {'regime': str, 'direcao': str, 'vol': str}.

        C5: resultado cacheado por 5s para evitar recalculo O(n).
        """
        agora = time.time()
        cached = self._cache.get(ativo)
        if cached and (agora - cached[0]) < 5.0:
            return cached[1]

        hist = list(historico)
        if len(hist) < 10:
            return {'regime': 'indefinido', 'direcao': 'neutro', 'vol': 'normal'}

        ultimos = hist[-self.LOOKBACK:]
        precos = [h['preco_fim'] for h in ultimos if h.get('preco_fim', 0) > 0]
        aggrs = [h['aggr_imb'] for h in ultimos]

        if len(precos) < 5:
            return {'regime': 'indefinido', 'direcao': 'neutro', 'vol': 'normal'}

        delta = precos[-1] - precos[0]
        vol_realizada = max(precos) - min(precos) if len(precos) >= 2 else 0
        aggr_medio = sum(aggrs) / len(aggrs) if aggrs else 0

        vol = self._classificar_vol(ativo, vol_realizada, agora)

        if abs(delta) > 20 and abs(aggr_medio) > 0.15:
            direcao = 'alta' if delta > 0 else 'baixa'
        else:
            direcao = 'neutro'

        if direcao == 'neutro':
            if vol == 'alta':
                regime = 'vol_alta'
            elif vol == 'baixa':
                regime = 'vol_baixa'
            else:
                regime = 'lateral'
        else:
            regime = f'tendencia_{direcao}'
            if vol != 'normal':
                regime += f'_vol_{vol}'

        resultado = {'regime': regime, 'direcao': direcao, 'vol': vol}
        self._cache[ativo] = (time.time(), resultado)
        return resultado

    def ajustar(self, ativo, score, motivos, regime_info=None, historico=None):
        """Ajusta score por regime e atualiza confirmacao_necessaria."""
        if regime_info is None and historico is not None:
            regime_info = self.detectar(ativo, historico)
        regime = regime_info.get('regime', 'lateral') if isinstance(regime_info, dict) else regime_info
        direcao = regime_info.get('direcao', 'neutro') if isinstance(regime_info, dict) else 'neutro'
        vol = regime_info.get('vol', 'normal') if isinstance(regime_info, dict) else 'normal'

        estrategias = self.config.get('estrategias', {})
        estrategia = estrategias.get(regime)
        if estrategia is None:
            regime_base = f'tendencia_{direcao}' if direcao != 'neutro' else 'lateral'
            estrategia = estrategias.get(regime_base, estrategias.get('lateral', {}))

        ajuste = 1.0
        if vol == 'alta':
            ajuste *= 0.8
        if 'tendencia' in regime:
            ajuste *= 1.1
        motivos.append(f'regime={regime}')

        return score * ajuste, regime, estrategia
