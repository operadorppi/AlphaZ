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

import math
import time
import logging
from collections import OrderedDict
from datetime import date

from features import (
    fase_sessao, dias_ate_vencimento, classificar_corretora,
    EWMAZScore,
)
from features.feature_registry import REGISTRY
from core.calibration import create_calibration_system
from dataclasses import asdict
from core.contracts import Signal
from core.decision_journal import DecisionEntry
from features.feature_engine import FeatureEngine

log = logging.getLogger(__name__)


class SignalEngine:
    """Recebe features, produz sinais. Não conhece posição nem risco."""

    def __init__(self, market_state, learning=None, regime=None, feature_engine=None, risk=None,
                 config=None, ativo_principal='WINV26', ativo_contexto='WDOU26', scorer=None):
        self.state = market_state
        self.config = config or (market_state.config if market_state and hasattr(market_state, 'config') else {})
        if learning is not None:
            self.learning = learning
        else:
            from core.learning import Learning
            self.learning = Learning(config=self.config)
            
        if regime is not None:
            self.regime = regime
        else:
            from core.regime_detector import RegimeDetector
            self.regime = RegimeDetector(config=self.config)
            
        if feature_engine is not None:
            self.feature_engine = feature_engine
        else:
            from features.feature_engine import FeatureEngine
            self.feature_engine = FeatureEngine(self.state, config=self.config)

        self.risk = risk
        self.ativo_principal = ativo_principal
        self.ativo_contexto = ativo_contexto
        self.scorer = scorer

        self.features = {}
        self.sinais = {}
        self.confianca_ewma = 0.0
        
        # Calibration system
        self.calibration = create_calibration_system(self.config)
        self._score_confirmado = 0.0
        self._sinal_streak = 0
        self._sinal_anterior_bruto = 0
        self._normalizar_score = bool(self.config.get('normalizar_score', False))
        self._zscore_trackers = {}
        self._last_seg_calc = {}  # ativo -> ultimo seg com features calculadas

    def calcular(self, seg, skip_avaliar=False):
        """Calcula features do segundo para todos os ativos no buffer.
        Otimizacao: recalcula apenas se houver trades novos desde o ultimo calculo.
        Em batch mode (_batch_mode=True), so recalcula quando o segundo muda.
        """
        for ativo, negs in list(self.state.buffer.items()):
            n_negs = len(negs)
            if getattr(self, '_batch_mode', False):
                # Batch: so recalcula quando o segundo muda (nao a cada trade)
                last = self._last_seg_calc.get(ativo)
                if last and last[1] == seg:
                    continue
                self._last_seg_calc[ativo] = (ativo, seg, n_negs)
            else:
                # Real-time: recalcula a cada trade novo
                cache_key = (ativo, seg, n_negs)
                if self._last_seg_calc.get(ativo) == cache_key:
                    continue
                self._last_seg_calc[ativo] = cache_key

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
            
            # Validação de registry (uma vez por sessão)
            if not hasattr(self, '_registry_validado'):
                self._registry_validado = True
                cols = [k for k in f.keys() if not k.startswith('_')]
                result = REGISTRY.validate_dataset(cols)
                if not result['valid']:
                    log.warning(f'[REGISTRY] Features nao registradas: {result["unknown_features"]}')
                    log.warning(f'[REGISTRY] Features nao-causais: {result["non_causal_features"]}')
                else:
                    log.info(f'[REGISTRY] {result["registered"]}/{result["total_features"]} features validadas')

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
                ri = tr['range'].get_estado()
                f['range_estado'] = ri['estado']
                f['range_topo'] = ri['topo']
                f['range_fundo'] = ri['fundo']
                f['range_amplitude'] = ri['amplitude']
                f['range_testes_topo'] = ri['testes_topo']
                f['range_testes_fundo'] = ri['testes_fundo']

            # === DADOS INSTITUCIONAIS (via InstitutionalContext) ===
            preco = f.get('preco_fim', 0)
            vol = f.get('vol_total', 0)
            inst = tr.get('inst_context')
            if inst and preco > 0:
                # OHLC do dia
                oh = self.state.ohlc.get(ativo, {})
                # Atualiza contexto institucional
                inst.update(ativo, preco, vol, ohlc=oh if oh else None)
                # Atualiza ajuste do scorer se disponível
                if hasattr(self, '_app') and self._app and hasattr(self._app, 'scorer'):
                    scorer = self._app.scorer
                    if scorer and hasattr(scorer, 'ajuste_anterior_oficial'):
                        adj = scorer.ajuste_anterior_oficial.get(ativo)
                        if adj and adj > 0:
                            inst.set_ajuste(ativo, adj)
                # Computa todas as features de contexto
                ctx_feats = inst.compute(ativo, preco)
                f.update(ctx_feats)

        # v12.0: retorna sinal do ativo principal
        return self.sinais.get(self.ativo_principal)

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
        hist = list(self.state.historico.get(ativo, []))

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

        # ============================================================
        # ML como FILTRO PRIMÁRIO (v12.2 — simplificado)
        # Regra: ML decide. Heurística não pode sobrepor.
        # ============================================================
        ml_prob = 0.5
        ml_dir = 0  # +1=C, -1=V, 0=neutro
        ml_available = False

        if hasattr(self, 'scorer') and self.scorer:
            try:
                prob_dict = getattr(self.scorer, 'prob', {})
                if isinstance(prob_dict, dict) and ativo in prob_dict:
                    val = prob_dict[ativo]
                    if isinstance(val, (int, float)) and not math.isnan(val):
                        ml_prob = float(val)
                        ml_available = True

                        # Calibração: prob → direção + threshold
                        regime_str = f.get('regime', 'lateral')
                        ml_decision = self.calibration.separate(
                            ml_prob=ml_prob,
                            regime=regime_str,
                            confianca=self.confianca_ewma,
                            score_heuristico=score,
                        )
                        calibrated = ml_decision['model_probability']
                        ml_dir_str = ml_decision['trading_decision']  # 'C', 'V', ''
                        threshold = ml_decision.get('threshold', 0.5)

                        ml_dir = 1 if ml_dir_str == 'C' else (-1 if ml_dir_str == 'V' else 0)

                        if ml_dir == 0:
                            # Zona de incerteza — ML bloqueia
                            motivos.append(f'ML_BLOCK (p={calibrated:.3f}, th={threshold:.3f})')
                        else:
                            motivos.append(f'ML_DIR={ml_dir_str} (p={calibrated:.3f})')
            except Exception as e:
                log.warning(f"[SIGNAL] Erro ML scorer: {e}")

        # --- Decisão final ---
        sinal = 0
        lado_heur = lado  # direção da heurística (aggr_imb)

        if ml_available:
            # ML disponível: ML é o gate
            if ml_dir == 0:
                sinal = 0  # ML bloqueou — não trade, independente da heurística
            elif ml_dir == lado_heur:
                sinal = ml_dir  # ML e heurística concordam
                motivos.append('ML+HEUR_OK')
            else:
                sinal = 0  # Discordam — não trade (heurística não pode sobrepor ML)
                motivos.append('ML_HEUR_DISCORD')
        else:
            # Fallback: sem ML, heurística pura (modo legado/desenvolvimento)
            if score > 0.5:
                sinal = lado_heur
            else:
                motivos.append('HEUR_FRACO')

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

        # v12.0: Unificar confiança — usar EWMA em ambos os lugares
        # Signal.confianca e PositionManager.confianca_ewma devem ser o mesmo valor
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
            symbol=ativo,
            timestamp_ms=int(f.get('time_ms', time.time() * 1000)),
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
        
        # Decision Journal: registrar cada sinal produzido
        if hasattr(self, '_app') and self._app and hasattr(self._app, 'journal'):
            journal = self._app.journal
            ctx_feats = {}
            for k in ['dist_vwap_pts', 'dist_abertura_pts', 'spread', 'ofi', 'microprice']:
                if k in f:
                    ctx_feats[k] = f[k]
            # Top 5 features por contribuição
            top_feat = {c[0]: round(c[1], 4) for c in contrib[:5] if len(c) >= 2}
            
            entry = DecisionEntry(
                ts_ms=int(f.get('time_ms', time.time() * 1000)),
                ativo=ativo,
                acao='SINAL',
                lado=lado_str,
                preco=preco,
                score=round(score, 3),
                confianca=round(self.confianca_ewma, 3),
                ml_prob=round(ml_prob, 3),
                sinal=sinal,
                regime=f.get('regime', 'lateral'),
                regime_info=f.get('regime_info', {}),
                tp=tp, sl=sl,
                motivos=motivos or ['neutro'],
                features_relevantes=top_feat,
                preco_ref=preco,
                **ctx_feats,
            )
            journal.registrar(entry)
        
        return sig_obj

    def get_features(self):
        """Retorna features com regime e OHLC. Usa shallow copy (10x mais rápido que deepcopy)."""
        feat = {k: dict(v) if isinstance(v, dict) else v for k, v in self.features.items()}
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
                f['abertura_dia'] = oh.get('abertura', 0)
                f['maxima_dia'] = oh.get('maxima', 0)
                f['minima_dia'] = oh.get('minima', 0)
                f['fechamento_dia'] = oh.get('fechamento', 0)
            # Contexto institucional (sempre, mesmo sem trades novos)
            tr = self.state.trackers.get(ativo, {})
            inst = tr.get('inst_context') if isinstance(tr, dict) else None
            preco = f.get('preco_fim', 0)
            vol = f.get('vol_total', 0)
            if inst and preco > 0:
                oh = self.state.ohlc.get(ativo, {})
                inst.update(ativo, preco, vol, ohlc=oh if oh else None)
                # Ajuste do scorer
                if hasattr(self, '_app') and self._app and hasattr(self._app, 'scorer'):
                    scorer = self._app.scorer
                    if scorer and hasattr(scorer, 'ajuste_anterior_oficial'):
                        adj = scorer.ajuste_anterior_oficial.get(ativo)
                        if adj and adj > 0:
                            inst.set_ajuste(ativo, adj)
                ctx_feats = inst.compute(ativo, preco)
                f.update(ctx_feats)
            # Range (se disponível)
            rng = tr.get('range') if isinstance(tr, dict) else None
            if rng and hasattr(rng, 'get_estado'):
                ri2 = rng.get_estado()
                f['range_estado'] = ri2.get('estado', 'indefinido')
                f['range_topo'] = ri2.get('topo', 0)
                f['range_fundo'] = ri2.get('fundo', 0)
                f['range_amplitude'] = ri2.get('amplitude', 0)
                f['range_testes_topo'] = ri2.get('testes_topo', 0)
                f['range_testes_fundo'] = ri2.get('testes_fundo', 0)
        return feat

    def get_sinais(self):
        out = {}
        for k, v in self.sinais.items():
            if hasattr(v, '__dataclass_fields__'):
                d = asdict(v)
                d['sinal'] = 1 if v.lado == 'C' else (-1 if v.lado == 'V' else 0)
                out[k] = d
            else:
                out[k] = v
        return out
