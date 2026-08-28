# -*- coding: utf-8 -*-
"""
core/signal_engine.py — Engine de scoring e sinais.

Extrai de Analise:
  - PESOS_INICIAIS (linha 1294)
  - _avaliar (linha 2096 — ~500 linhas de scoring heurístico)
  - _suavizar_sinal (delegado para PositionManager)
  - _calcular (linha 1871 — prepara features por segundo)
  - get_features, get_sinais

O SignalEngine NÃO conhece posição nem risco.
Ele recebe features e produz (lado, score, confianca, motivos, contrib).
"""

import time
import logging
from collections import OrderedDict
from datetime import date

from features import (
    fase_sessao, dias_ate_vencimento, classificar_corretora,
    EWMAZScore,
)
from core.contracts import Signal
from features.feature_engine import FeatureEngine

log = logging.getLogger(__name__)


class SignalEngine:
    """Recebe features, produz sinais. Não conhece posição nem risco."""

    def __init__(self, market_state, learning, regime, feature_engine, risk=None,
                 config=None, ativo_principal='WINV26', ativo_contexto='WDOU26'):
        self.state = market_state
        self.learning = learning
        self.regime = regime
        self.risk = risk
        self.config = config or {}
        self.ativo_principal = ativo_principal
        self.ativo_contexto = ativo_contexto

        self.features = {}
        self.sinais = {}
        self.confianca_ewma = 0.0
        self._score_confirmado = 0.0
        self._sinal_streak = 0
        self._sinal_anterior_bruto = 0
        self._normalizar_score = bool(self.config.get('normalizar_score', False))
        self._zscore_trackers = {}
        self.feature_engine = feature_engine

    def calcular(self, seg, skip_avaliar=False):
        """Calcula features do segundo para todos os ativos no buffer."""
        for ativo, negs in list(self.state.buffer.items()):
            f = self.feature_engine.processar_lote(ativo, negs, seg)
            if not f:
                continue

            self.features[ativo] = f
            with self.state.lock:
                self.state.features_por_seg[(ativo, seg)] = f
                max_feat = self.config.get('features_seg_max', 7200)
                while len(self.state.features_por_seg) > max_feat:
                    self.state.features_por_seg.popitem(last=False)
            self.state.historico[ativo].append(f)

            if not skip_avaliar:
                self.avaliar(ativo, f)

            # Alimenta trackers
            tr = self.state.trackers[ativo]
            ts_now = time.time()
            tr['aggr'].add(abs(f['aggr_imb']), ts_now)
            tr['eff'].add(f['price_eff'], ts_now)
            tr['acel'].add(abs(f['aceleracao']), ts_now)
            bs = self.state.book_stats.get(ativo)
            if bs:
                tr['book_imb'].add(abs(bs['imb']), ts_now)
            if f['preco_fim'] > 0:
                tr['range'].atualizar(f['preco_fim'], ts_now)

    def avaliar(self, ativo, f):
        """Avalia features e produz score + sinal.

        Esta é a função central de scoring heurístico.
        O ML scorer (se carregado) é combinado aqui.
        """
        aggr = f['aggr_imb']
        eff = f['price_eff']
        dp = f['delta_preco']
        vol = f['vol_total']
        preco = f['preco_fim']
        acel = f.get('aceleracao', 0.0)
        hist = self.state.historico.get(ativo, [])

        tr = self.state.trackers[ativo]
        p_aggr = self.config.get('percentil_aggr', 0.85)
        fb_aggr = self.config.get('fallback_aggr_min', 0.3)
        limiar_aggr = tr['aggr'].percentil(p_aggr, fb_aggr)
        limiar_eff = tr['eff'].percentil(self.config.get('percentil_eficiencia', 0.85),
                                         self.config.get('fallback_eficiencia_min', 0.001))
        limiar_acel = tr['acel'].percentil(self.config.get('percentil_aceleracao', 0.85),
                                          self.config.get('fallback_aceleracao_min', 0.05))

        score = 0.0
        motivos = []
        contrib = []

        regime_info = self.regime.detectar(ativo, hist)
        f['regime'] = regime_info.get('regime', 'lateral')

        def add(key, mult, texto):
            nonlocal score
            regime = f.get('regime', 'lateral')
            peso_base = self.learning.pesos_regime.get(regime, self.learning.pesos).get(key, self.learning.pesos.get(key, 0.0))
            if self._normalizar_score:
                zt = self._zscore_trackers.get(key)
                if zt is None:
                    zt = EWMAZScore()
                    self._zscore_trackers[key] = zt
                mult = zt.z(mult)
                zt.atualizar(mult)
            score += peso_base * mult
            contrib.append((key, mult))
            motivos.append(texto)

        # Divergência CVD
        cvd_d = f.get('cvd_div', 0)
        if cvd_d == -1:
            add('cvd_div', -0.6, 'divergencia bearish')
        elif cvd_d == 1:
            add('cvd_div', 0.6, 'divergencia bullish')

        lado = 1 if aggr > 0 else -1

        # Preço andando
        if abs(aggr) >= limiar_aggr:
            preco_andando = (dp > 0 and lado > 0) or (dp < 0 and lado < 0)
            if preco_andando and abs(dp) > 5:
                add('preco_andando', 0.3, f"preco +{dp:.0f}")
            elif preco_andando:
                add('preco_andando', 0.15, f"preco ({dp:.0f})")
            elif not preco_andando and abs(dp) > 2:
                add('preco_andando', -0.3, f"preco CONTRA ({dp:.0f})")

        # Eficiência
        if vol > 10:
            if eff > limiar_eff:
                add('eficiencia', 1.0, f"eff {eff:.4f}")
            elif eff < self.config.get('fallback_absorcao_eficiencia_max', 0.001) and abs(aggr) > 0.3:
                add('eficiencia', -0.8, f"absorcao eff={eff:.4f}")

        # Aceleração
        if abs(acel) > limiar_acel and (acel > 0) == (aggr > 0):
            add('aceleracao', 1.0, f"acelera {acel:+.2f}")
        elif abs(acel) > limiar_acel and (acel > 0) != (aggr > 0):
            add('aceleracao', -0.75, f"desacelera {acel:+.2f}")

        # Persistência
        seguidos = 0
        for h in reversed(hist[-5:]):
            if (h['aggr_imb'] > 0) == (aggr > 0):
                seguidos += 1
            else:
                break
        if seguidos >= 4:
            add('persistencia', 1.0, f"{seguidos}s seguidos")
        elif seguidos >= 3:
            add('persistencia', 0.667, f"{seguidos}s seguidos")

        # Regime ajuste
        result = self.regime.ajustar(ativo, score, motivos, regime_info, hist)
        score, regime = result[0], result[1]
        estrategia = result[2] if len(result) > 2 else {}

        # ML Score
        ml_prob = 0.5
        if hasattr(self, 'scorer') and self.scorer and ativo in getattr(self.scorer, 'prob', {}):
            ml_prob = self.scorer.prob[ativo]
            ml_threshold = self.config.get('ml_threshold', 0.6)
            ml_score = (ml_prob - 0.5) * 2
            score = 0.6 * score + 0.4 * ml_score * 3.0
            motivos.append(f'ML={ml_prob:.2f}')

        # Sinal
        sinal = 0
        if score > 0.5:
            sinal = lado
        else:
            if sinal == 0:
                motivos = ['fluxo fraco']

        # Persistência do sinal
        if sinal != 0 and sinal == self._sinal_anterior_bruto:
            self._sinal_streak += 1
        elif sinal != 0:
            self._sinal_streak = 1
        else:
            self._sinal_streak = 0
        self._sinal_anterior_bruto = sinal

        # Confiança EWMA
        alpha = 0.3
        if abs(score) < 0.1:
            self.confianca_ewma *= 0.85
        else:
            self.confianca_ewma = (1 - alpha) * self.confianca_ewma + alpha * abs(score)

        conf = min(abs(score) / 3.0, 1.0)

        # TP/SL
        ranges_hist = []
        for i in range(60, min(len(hist), 600), 60):
            ph_i = [h['preco_fim'] for h in hist[-i:] if h['preco_fim'] > 0]
            if len(ph_i) >= 2:
                ranges_hist.append(max(ph_i) - min(ph_i))
        
        # vol_p representa a amplitude média em pontos (1 a 10 min)
        vol_p = max(sum(ranges_hist) / len(ranges_hist) if ranges_hist else abs(dp), 100.0)
        vol_bps = f.get('range_vol_bps', 0.0)

        # v10.14: Centralização da lógica de TP/SL no RiskManager
        if self.risk:
            tp, sl = self.risk.calcular_barreiras_dinamicas(ativo, vol_p, vol_bps, regime, conf)
        else:
            # Fallback simplificado se risk não injetado
            tp = round(vol_p * 0.6 / 5) * 5
            sl = round(vol_p * 0.4 / 5) * 5

        # v10.15: Retorno tipado via Contrato Signal
        lado_str = 'C' if sinal > 0 else ('V' if sinal < 0 else '')
        
        sig_obj = Signal(
            lado=lado_str,
            score=round(score, 3),
            confianca=round(self.confianca_ewma, 3), # Usamos a EWMA como confiança principal
            motivos=motivos or ['neutro'],
            contrib=contrib,
            tp=tp, sl=sl,
            ml_prob=round(ml_prob, 3),
            preco_ref=preco,
            horizonte=60
        )
        self.sinais[ativo] = sig_obj

        return sig_obj

    def get_features(self):
        """Retorna features com regime e OHLC."""
        import copy
        feat = copy.deepcopy(self.features)
        for ativo, f in feat.items():
            if ativo.startswith('_'):
                continue
            hist = list(self.state.historico.get(ativo, []))
            if hist:
                ri = self.regime.detectar(ativo, hist)
                f['regime'] = ri.get('regime', 'lateral')
                f['regime_info'] = ri
            if ativo in self.state.ohlc:
                oh = self.state.ohlc[ativo]
                f['abertura_dia'] = oh['abertura']
                f['maxima_dia'] = oh['maxima']
                f['minima_dia'] = oh['minima']
                f['fechamento_dia'] = oh['fechamento']
        return feat

    def get_sinais(self):
        return dict(self.sinais)
