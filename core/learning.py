# -*- coding: utf-8 -*-
"""
core/learning.py — Aprendizado de pesos por MFE/MAE.

Extrai de Analise:
  - aprender_mfe_mae (linha 2861)
  - _recalc_acuracia (linha 2931)
  - carregar_aprendizado (linha 1726)
  - salvar_aprendizado (linha 1726)
  - resultados, previsoes, feature_hits, pesos
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from collections import deque, defaultdict

log = logging.getLogger(__name__)

PESOS_INICIAIS = {
    'preco_andando': 0.3, 'eficiencia': 0.20, 'aceleracao': 0.2,
    'persistencia': 0.15, 'inst_lidera': 0.2, 'varejo_contra': -0.25,
    'book_imb': 0.2, 'defesa': 0.20, 'absorcao_book': -0.30,
    'retirada': -0.2, 'reposicao': 0.2, 'thinning': -0.2,
    'layering': -0.1, 'delta_book': 0.20,
    'cross_asset': 0.25, 'cross_asset_preco': 0.1,
    'liquidez_removida': -0.4, 'stop_hunt': 0.2,
    'horario_inst': 0.3, 'horario_varejo': -0.3,
    'range': 0.3,
    'absorcao_preco': 0.3,
    'corretora_tt_book': 0.4,
    'acumulacao': 0.5,
    'ofi': 0.45,
    'ofi_ewma': 0.25,
    'book_level_spread': 0.15,
    'book_imb_l1': 0.25, 'book_imb_l10': 0.30, 'book_microprice': 0.15, 'book_hhi': 0.10,
    'micro_drift': 0.35,
    'imb_ponderado': 0.35,
    'slope_book': 0.25,
    'trade_metrics': 0.25,
    'cross_lag': 0.35,
    'cvd_div': 0.50,
    'vp_vp_total': 0.60,
    'vpin': 0.40,
    'kyle_lambda': 0.30
}


class Learning:
    """Estado de aprendizado: pesos, feature_hits, acuracia."""

    def __init__(self, config=None):
        self.config = config or {}
        self.pesos = dict(PESOS_INICIAIS)
        self.pesos_regime = {
            'tendencia_alta': dict(PESOS_INICIAIS),
            'tendencia_baixa': dict(PESOS_INICIAIS),
            'lateral': dict(PESOS_INICIAIS),
            'vol_alta': dict(PESOS_INICIAIS),
            'vol_baixa': dict(PESOS_INICIAIS),
            'tendencia_alta_vol_alta': dict(PESOS_INICIAIS),
            'tendencia_alta_vol_baixa': dict(PESOS_INICIAIS),
            'tendencia_baixa_vol_alta': dict(PESOS_INICIAIS),
            'tendencia_baixa_vol_baixa': dict(PESOS_INICIAIS),
            'lateral_vol_alta': dict(PESOS_INICIAIS),
            'lateral_vol_baixa': dict(PESOS_INICIAIS),
        }
        self.feature_hits = {}
        self.acuracia = {}
        self.resultados = deque(maxlen=5000)
        self.previsoes = deque(maxlen=5000)
        # Feature death: features que sistematicamente pioram o resultado
        # tem peso zerado e deixam de contribuir no scoring.
        self.morto = set()
        self.morto_por_regime = defaultdict(set) # v10.11: Suspensão granular

        # Boost de OFI em regimes
        self.pesos_regime['tendencia_alta']['ofi'] = 0.6
        self.pesos_regime['tendencia_alta']['ofi_ewma'] = 0.4
        self.pesos_regime['tendencia_baixa']['ofi'] = 0.6
        self.pesos_regime['tendencia_baixa']['ofi_ewma'] = 0.4
        self.pesos_regime['vol_alta']['ofi'] = 0.5
        self.pesos_regime['vol_alta']['ofi_ewma'] = 0.35
        self.pesos_regime['lateral']['ofi'] = 0.15
        self.pesos_regime['lateral']['ofi_ewma'] = 0.1

    def aprender_mfe_mae(self, contrib, acertou, mfe, mae, regime_abertura=None):
        """Ajusta pesos por MFE/MAE com decay."""
        if not contrib:
            return
        decay = self.config.get('aprendizado_decay', 0.95)
        regime_nome = regime_abertura or 'lateral'
        mortos_reg = self.morto_por_regime[regime_nome]

        for key, _ in contrib:
            inicial = PESOS_INICIAIS.get(key, 0.0)
            floor = abs(inicial) * 0.3
            
            # v10.11: Respeita morte global ou suspensão por regime
            if key in self.morto:
                self.pesos[key] = 0.0
                if regime_nome in self.pesos_regime:
                    self.pesos_regime[regime_nome][key] = 0.0
                continue
                
            if key in mortos_reg:
                if regime_nome in self.pesos_regime:
                    self.pesos_regime[regime_nome][key] = 0.0
            
            atual = self.pesos.get(key, 0.0)
            self.pesos[key] = max(abs(atual) * decay, floor) * (1 if atual >= 0 else -1)
            if regime_nome in self.pesos_regime and key not in mortos_reg:
                atual_r = self.pesos_regime[regime_nome].get(key, 0.0)
                self.pesos_regime[regime_nome][key] = max(abs(atual_r) * decay, floor) * (1 if atual_r >= 0 else -1)

        qualidade_trade = (mfe / max(abs(mae), 1.0)) if mae != 0 else 2.0

        for key, mult in contrib:
            if key in self.morto:
                continue  # feature morta não re-aprende
            h = self.feature_hits.setdefault(key, {'acertos': 0, 'erros': 0, 'per_regime': {}})
            if 'per_regime' not in h: h['per_regime'] = {}
            amostras_previas = h['acertos'] + h['erros']
            min_amostras = self.config.get('aprendizado_min_amostras', 20)
            fator_confianca = min(1.0, amostras_previas / min_amostras) if amostras_previas else 0.2
            peso_atual = self.pesos.get(key, 0.0)

            if acertou:
                alvo = 1.0 if mult >= 0 else -1.0
            else:
                alvo = -1.0 if mult >= 0 else 1.0

            delta = self.config.get('aprendizado_delta', 0.05)
            
            # v10.9 (Fase 9): Learning Rate Annealing
            # Reduz o impacto de novos trades conforme acumulamos histórico (estabilidade)
            annealing = 1.0 / (1.0 + (amostras_previas / 100.0))
            ajuste = delta * min(qualidade_trade, 2.0) * fator_confianca * annealing
            
            novo_peso = peso_atual + (alvo - peso_atual) * ajuste
            self.pesos[key] = max(-1.0, min(1.0, novo_peso))
            h['acertos' if acertou else 'erros'] += 1
            
            # v10.11: Atualiza peso do regime se não estiver suspenso
            if regime_nome in self.pesos_regime and key not in mortos_reg:
                p_r_atual = self.pesos_regime[regime_nome].get(key, 0.0)
                novo_p_r = p_r_atual + (alvo - p_r_atual) * ajuste
                self.pesos_regime[regime_nome][key] = max(-1.0, min(1.0, novo_p_r))
            
            # v10.10: Tracking de performance segmentada por regime
            rh = h['per_regime'].setdefault(regime_nome, {'acertos': 0, 'erros': 0})
            rh['acertos' if acertou else 'erros'] += 1

        self._recalc_acuracia()
        self._verificar_feature_death()

    def get_feature_status(self):
        """Retorna dicionário com acurácia e status de vida/morte de cada feature."""
        status = {}
        todas_features = set(PESOS_INICIAIS.keys()) | set(self.feature_hits.keys())
        for feat in todas_features:
            h = self.feature_hits.get(feat, {'acertos': 0, 'erros': 0, 'per_regime': {}})
            total = h['acertos'] + h['erros']
            status[feat] = {
                'acuracia': round(self.acuracia.get(feat, 0.0), 3),
                'amostras': total,
                'status': 'morto' if feat in self.morto else 'ativo',
                'peso_atual': round(self.pesos.get(feat, 0.0), 3),
                'performance_regime': {},
                'suspenso_em': [r for r, m in self.morto_por_regime.items() if feat in m]
            }
            
            # v10.10: Adiciona detalhamento por regime (Tendência vs Lateral)
            for reg, r_h in h.get('per_regime', {}).items():
                r_total = r_h['acertos'] + r_h['erros']
                status[feat]['performance_regime'][reg] = {
                    'acuracia': round(r_h['acertos'] / r_total, 3) if r_total > 0 else 0.0,
                    'amostras': r_total
                }
                
        return status

    def _verificar_feature_death(self):
        """Feature death: zera o peso de features persistentemente inuteis.

        Feature com amostras suficientes e acuracia abaixo de um limiar
        deixa de contribuir (peso = 0), evitando que continuem poluindo o
        score indefinidamente apos o decay estacionar no floor positivo.
        """
        min_amostras = self.config.get('aprendizado_morte_min_amostras', 40)
        limiar = self.config.get('aprendizado_morte_acuracia', 0.4)
        for key in list(self.feature_hits.keys()):
            h = self.feature_hits[key]
            
            # v10.11: Primeiro verifica suspensão por regime
            for regime, rh in h.get('per_regime', {}).items():
                if key in self.morto_por_regime[regime]:
                    continue
                total_r = rh['acertos'] + rh['erros']
                if total_r >= min_amostras:
                    acc_r = rh['acertos'] / total_r
                    if acc_r < limiar:
                        self.morto_por_regime[regime].add(key)
                        if regime in self.pesos_regime:
                            self.pesos_regime[regime][key] = 0.0
                        log.warning("[REGIME-DEATH] feature %s suspensa no regime %s (acc=%.3f, n=%d)",
                                    key, regime, acc_r, total_r)

            if key in self.morto:
                continue
            total = h['acertos'] + h['erros']
            if total < min_amostras:
                continue
            acuracia = h['acertos'] / total
            if acuracia < limiar:
                self.morto.add(key)
                self.pesos[key] = 0.0
                self.pesos_regime = {
                    r: dict({**w, key: 0.0} if key in w else w)
                    for r, w in self.pesos_regime.items()
                }
                log.warning("[FEATURE-DEATH] feature %s zerada globalmente (acc=%.3f, n=%d)",
                            key, acuracia, total)

    def _recalc_acuracia(self):
        for ft, h in self.feature_hits.items():
            total = h['acertos'] + h['erros']
            self.acuracia[ft] = h['acertos'] / total if total > 0 else 0

    def carregar(self, base_dir):
        p = Path(base_dir) / 'learning_state.json'
        if not p.exists():
            return
        try:
            st = json.loads(p.read_text(encoding='utf-8'))
            for k, v in st.get('pesos', {}).items():
                if k in self.pesos and isinstance(v, (int, float)):
                    self.pesos[k] = max(-1.0, min(1.0, float(v)))
            for k, v in st.get('feature_hits', {}).items():
                self.feature_hits[k] = {
                    'acertos': int(v.get('acertos', 0)), 
                    'erros': int(v.get('erros', 0)),
                    'per_regime': v.get('per_regime', {})
                }
            morto = st.get('morto', [])
            if isinstance(morto, list):
                self.morto = set(morto)
            # v10.11: Carrega suspensões por regime
            morto_reg = st.get('morto_por_regime', {})
            self.morto_por_regime = defaultdict(set)
            for r, m_list in morto_reg.items():
                self.morto_por_regime[r] = set(m_list)
            self.resultados.extend(st.get('resultados', [])[-500:])
            self.previsoes.extend(st.get('previsoes', [])[-500:])
            self._recalc_acuracia()
            log.info(f"[LEARN] estado carregado: {len(self.resultados)} resultados, "
                     f"{len(self.morto)} features mortas")
        except Exception as e:
            log.warning(f"[LEARN] falha ao carregar: {e}")

    def carregar_aprendizado(self, base_dir):
        """Alias de compatibilidade para o nome antigo."""
        self.carregar(base_dir)

    def salvar(self, base_dir):
        try:
            out = Path(base_dir)
            out.mkdir(parents=True, exist_ok=True)
            st = {
                'pesos': dict(self.pesos),
                'feature_hits': {k: dict(v) for k, v in self.feature_hits.items()},
                'acuracia': dict(self.acuracia),
                'morto': sorted(self.morto),
                'morto_por_regime': {r: sorted(list(m)) for r, m in self.morto_por_regime.items()},
                'resultados': list(self.resultados)[-500:],
                'previsoes': list(self.previsoes)[-500:],
                'salvo_em': datetime.now().isoformat(timespec='seconds')
            }
            (out / 'learning_state.json').write_text(
                json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception as e:
            log.warning(f"[LEARN] falha ao salvar: {e}")
