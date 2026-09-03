# -*- coding: utf-8 -*-
"""
ml/feature_manifest.py — Feature Manifest para paridade treino ↔ produção.

Garante que o modelo ML receba EXATAMENTE as features com as quais foi treinado.
Se uma feature faltar em runtime → fail-safe (não gera sinal, loga erro).

Uso:
  1. No treino: manifest = FeatureManifest.from_model(modelo, features_list)
     manifest.save('feature_manifest.json')
  
  2. No scorer: manifest = FeatureManifest.load('feature_manifest.json')
     ok, missing, extra = manifest.validate(flat_dict)
     if not ok: log.error(...)  # fail-safe
"""

import json
import os
import logging
from typing import Dict, List, Tuple, Any, Optional
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def _valor_numerico(v) -> bool:
    """True se v pode virar float SEM perda de semantica.

    P0-A29 (v15.24): string numerica ('0.5') NAO conta — se uma feature
    esperada chega como string, e bug do pipeline (sinalizar, nao adivinhar).
    bool/int/float/Decimal/numpy scalars sao aceitos; None nao.
    """
    if v is None:
        return False
    if isinstance(v, (bool, int, float)):
        return True
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            return True
    except Exception:
        pass
    try:
        import numpy as _np
        if isinstance(v, _np.generic):
            return True
    except Exception:
        pass
    return False


class FeatureManifest:
    """Manifesto formal das features que o modelo espera.
    
    Cada feature possui:
      - nome: nome canônico
      - tipo: float, int, bool, str
      - required: se True, falta = fail-safe
      - source: de qual módulo vem (trade_features, book, vp, kyle, etc)
      - default: valor default se faltar (None = erro)
      - description: descrição humana
    """
    
    def __init__(self, features: List[Dict[str, Any]], model_name: str = '',
                 model_version: str = '', train_date: str = ''):
        self.features = features  # lista de dicts ordenada
        self.model_name = model_name
        self.model_version = model_version
        self.train_date = train_date
        self._names = [f['nome'] for f in features]
        self._required = {f['nome'] for f in features if f.get('required', True)}
        self._defaults = {f['nome']: f.get('default') for f in features}
    
    @classmethod
    def from_model(cls, modelo, features_list: List[str],
                   model_name: str = '', model_version: str = '',
                   train_date: str = '') -> 'FeatureManifest':
        """Cria manifest a partir de um modelo treinado.
        
        Args:
            modelo: objeto do modelo (LightGBM, XGBoost, etc)
            features_list: lista ordenada de nomes das features
            model_name: nome do modelo
            model_version: versão
            train_date: data de treino
        """
        # Importância das features (se disponível)
        importancias = {}
        if hasattr(modelo, 'feature_importances_'):
            imp = modelo.feature_importances_
            for i, name in enumerate(features_list):
                importancias[name] = float(imp[i]) if i < len(imp) else 0.0
        
        features = []
        for name in features_list:
            feat = {
                'nome': name,
                'tipo': 'float',
                'required': True,
                'default': None,
                'importancia': importancias.get(name, 0.0),
                'source': _infer_source(name),
                'description': _describe(name),
            }
            features.append(feat)
        
        return cls(features, model_name=model_name,
                   model_version=model_version, train_date=train_date)
    
    @classmethod
    def load(cls, path: str) -> 'FeatureManifest':
        """Carrega manifest de um arquivo JSON."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(
            features=data.get('features', []),
            model_name=data.get('model_name', ''),
            model_version=data.get('model_version', ''),
            train_date=data.get('train_date', ''),
        )
    
    def save(self, path: str):
        """Salva manifest em JSON."""
        data = {
            'model_name': self.model_name,
            'model_version': self.model_version,
            'train_date': self.train_date,
            'n_features': len(self.features),
            'generated_at': datetime.now().isoformat(),
            'features': self.features,
        }
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info(f'[MANIFEST] Salvo: {path} ({len(self.features)} features)')
    
    def validate(self, flat_dict: Dict[str, Any]) -> Tuple[bool, List[str], List[str]]:
        """Valida se um dict de features flatten tem tudo que o modelo precisa.
        
        Returns:
            (ok, missing, extra) — ok=True se todas required estão presentes
        """
        flat_keys = set(flat_dict.keys())
        manifest_keys = set(self._names)
        
        missing = [f for f in self._required if f not in flat_keys]
        extra = [f for f in flat_keys if f not in manifest_keys
                 and f not in ('ativo', 'ts_ms', 'label')]
        
        ok = len(missing) == 0
        return ok, missing, extra
    
    def extract(self, flat_dict: Dict[str, Any]) -> List[float]:
        """Extrai valores na ordem exata que o modelo espera (compat).
        
        P0-A29 (v15.24): MANTIDO para compat, mas o scorer nao usa mais
        este caminho — usa montar_vetor(), que nunca fabrica zero fake.
        
        Returns:
            Lista de floats na ordem das features do manifest.
            Features faltantes recebem default ou 0.0.
        """
        vals = []
        for feat in self.features:
            name = feat['nome']
            val = flat_dict.get(name)
            if val is None:
                default = feat.get('default')
                if default is None:
                    val = 0.0  # fail-safe: não crasha
                else:
                    val = default
            try:
                vals.append(float(val))
            except (TypeError, ValueError):
                vals.append(0.0)
        return vals

    def montar_vetor(self, flat_dict: Dict[str, Any]):
        """P0-A29 (v15.24): monta o vetor com CONTRATO EXPLICITO de cobertura.

        Separa tres estados que antes eram confundidos:
          - feature OBRIGATORIA ausente  -> problema AUSENTE:name
          - feature presente mas NAO numerica -> problema INVALIDA:name
          - feature OPCIONAL ausente sem default -> problema SEM_DEFAULT:name
        (opcional ausente COM default -> usa o default documentado, ok)
        (presente e zero -> ZERO LEGITIMO, ok)

        Returns:
            (vals, problemas)
              vals:      lista de floats na ordem do manifest, ou None se
                         houver qualquer problema;
              problemas: lista de strings 'TIPO:name' (vazia se ok).
        """
        problemas = []
        vals = []
        for feat in self.features:
            name = feat['nome']
            if name in flat_dict:
                v = flat_dict[name]
                if not _valor_numerico(v):
                    problemas.append(f'INVALIDA:{name}')
                    vals.append(None)
                else:
                    vals.append(float(v))
            elif name in self._required:
                problemas.append(f'AUSENTE:{name}')
                vals.append(None)
            else:
                default = feat.get('default')
                if default is None:
                    problemas.append(f'SEM_DEFAULT:{name}')
                    vals.append(None)
                else:
                    vals.append(float(default))
        if problemas:
            return None, problemas
        return vals, []
    
    @property
    def names(self) -> List[str]:
        return list(self._names)
    
    @property
    def n_features(self) -> int:
        return len(self.features)
    
    def summary(self) -> Dict[str, Any]:
        return {
            'model_name': self.model_name,
            'model_version': self.model_version,
            'train_date': self.train_date,
            'n_features': self.n_features,
            'required': len(self._required),
            'features': self._names,
        }


# ============================================================
# HELPERS
# ============================================================

def _infer_source(name: str) -> str:
    """Infere a origem de uma feature pelo nome."""
    vp = ('vp_poc', 'vp_vah', 'vp_val', 'vp_vp')
    book = ('spread', 'ofi', 'microprice', 'imb_', 'hhi_book',
            'micro_drift', 'imb_ponderado', 'slope_')
    kyle = ('kyle_',)
    trade = ('n_eventos', 'vol_total', 'aggr_imb', 'ewma_imb', 'hhi_compra',
             'hhi_venda', 'delta_preco', 'vpin', 'preco_ultimo', 'cvd_total',
             'realized_vol', 'range_vol', 'delta_vol', 'vol_1s')
    
    for prefix in vp:
        if name.startswith(prefix):
            return 'volume_profile'
    for prefix in book:
        if name.startswith(prefix):
            return 'book_features'
    for prefix in kyle:
        if name.startswith(prefix):
            return 'kyle_lambda'
    for prefix in trade:
        if name.startswith(prefix):
            return 'trade_features'
    return 'unknown'


def _describe(name: str) -> str:
    """Descrição humana de uma feature."""
    descriptions = {
        # Trade features
        'n_eventos_janela': 'Número de eventos na janela de 1s',
        'vol_total': 'Volume total negociado',
        'aggr_imb': 'Imbalance de agressão (compra - venda) / total',
        'ewma_imb_longa': 'EWMA do imbalance (janela longa)',
        'ewma_imb_media': 'EWMA do imbalance (janela media)',
        'ewma_imb_curta': 'EWMA do imbalance (janela curta)',
        'hhi_compra': 'HHI concentração compradora',
        'hhi_venda': 'HHI concentração vendedora',
        'delta_preco_janela': 'Variação de preço na janela (pontos)',
        'vpin': 'Volume-Synchronized Probability of Informed Trading',
        'preco_ultimo': 'Último preço de negociação',
        'cvd_total': 'Cumulative Volume Delta total',
        'cvd_div': 'Divergência CVD-preço',
        'realized_vol_bps': 'Volatilidade realizada em bps',
        'range_vol_bps': 'Amplitude do range em bps',
        'absorcao_ratio': 'Ratio de absorção',
        'fluxo_persist': 'Persistência do fluxo',
        'taxa_eventos': 'Taxa de eventos por segundo',
        'fase_sessao': 'Fase da sessão (abertura/meio/fechamento)',
        'dias_ate_venc': 'Dias até vencimento',
        
        # Book features
        'spread': 'Spread bid-ask',
        'ofi': 'Order Flow Imbalance',
        'ofi_total': 'OFI total acumulado',
        'ofi_ewma': 'OFI suavizado EWMA',
        'microprice': 'Microprice (preço ponderado por volume)',
        'microprice_vs_mid': 'Diferença microprice - mid',
        'delta_vol_janela': 'Variação de volume na janela',
        'imb_book': 'Imbalance do book',
        'imb_L1': 'Imbalance nível 1 do book',
        'imb_L3': 'Imbalance nível 3 do book',
        'imb_L5': 'Imbalance nível 5 do book',
        'imb_L10': 'Imbalance nível 10 do book',
        'imb_L20': 'Imbalance nível 20 do book',
        'imb_L30': 'Imbalance nível 30 do book',
        'imb_L50': 'Imbalance nível 50 do book',
        'imb_L100': 'Imbalance nível 100 do book',
        'imb_L200': 'Imbalance nível 200 do book',
        'imb_L250': 'Imbalance nível 250 do book',
        'imb_L500': 'Imbalance nível 500 do book',
        'hhi_book': 'HHI do book (concentração)',
        'micro_drift_bps': 'Microprice drift em bps',
        'micro_drift_ewma': 'Microprice drift EWMA',
        'imb_ponderado': 'Imbalance ponderado por profundidade',
        'slope_bid': 'Slope do book comprador',
        'slope_ask': 'Slope do book vendedor',
        'liq_dist_bid': 'Distância média ponderada bid',
        'liq_dist_ask': 'Distância média ponderada ask',
        'vel_bid': 'Velocidade mudança volume bid',
        'vel_ask': 'Velocidade mudança volume ask',
        'vel_bid_ewma': 'Velocidade bid EWMA',
        'vel_ask_ewma': 'Velocidade ask EWMA',
        'n_bid_levels': 'Número de níveis bid',
        'n_ask_levels': 'Número de níveis ask',
        
        # Volume Profile
        'vp_poc_dist': 'Distância ao POC (Volume Profile)',
        'vp_vah_dist': 'Distância ao VAH (Value Area High)',
        'vp_val_dist': 'Distância ao VAL (Value Area Low)',
        'vp_vp_total': 'Volume total do Volume Profile',
        'dist_preco_poc': 'Distância preço-POC',
        'dist_preco_poc_ticks': 'Distância preço-POC em ticks',
        'preco_acima_poc': 'Preço acima do POC',
        'preco_abaixo_poc': 'Preço abaixo do POC',
        'aproximando_poc': 'Aproximando POC',
        'afastando_poc': 'Afastando POC',
        
        # Kyle Lambda
        'kyle_kyle_lambda': 'Kyle\'s Lambda (impacto de preço)',
        
        # Cross-asset
        'cross_lag': 'Lag temporal WIN×WDO (ms)',
        # P1-A25 (v15.19): semântica explícita da correlação cross-asset.
        # Cada lado é amostrado em buckets de 100ms (grid do master clock) e
        # cada bucket vira UM valor: MÉDIA dos fluxos do bucket (default).
        # Pearson sobre os buckets comuns dentro de janela_corr (60s).
        # Veja features/cross_asset.py (docstring do módulo).
        'cross_corr_aggr': 'Corr. fluxo agressão WIN×WDO (média/bucket 100ms)',
        'cross_divergencia': 'Divergência preço WIN×WDO',
        'wdo_leading': 'Score liderança WDO',
        'resposta_win': 'Resposta WIN a WDO',
        'wdo_delta': 'Variação instantânea WDO',
        
        # Institutional context
        'dist_vwap_pts': 'Distância ao VWAP (pontos)',
        'dist_vwap_norm': 'Distância ao VWAP (normalizada)',
        'dist_vwap_ticks': 'Distância ao VWAP (ticks)',
        'dist_abertura_pts': 'Distância à abertura (pontos)',
        'dist_abertura_norm': 'Distância à abertura (normalizada)',
        'dist_maxima_pts': 'Distância à máxima (pontos)',
        'dist_minima_pts': 'Distância à mínima (pontos)',
        'dist_ajuste_pts': 'Distância ao ajuste (pontos)',
        'dist_ajuste_norm': 'Distância ao ajuste (normalizada)',
        'zona_vwap': 'Zona VWAP (0=far,1=near,2=at)',
        'zona_abertura': 'Zona abertura',
        'zona_maxima': 'Zona máxima',
        'zona_minima': 'Zona mínima',
        'zona_ajuste': 'Zona ajuste',
        'posicao_range_dia': 'Posição no range do dia (0-1)',
        'posicao_relativa': 'Posição relativa (alias de posicao_range_dia)',
        'amplitude_dia_pts': 'Amplitude do dia (pontos)',
        'bounces_vwap_norm': 'Bounces no VWAP (normalizado)',
        'bounces_ajuste_norm': 'Bounces no ajuste (normalizado)',
        'reversao_perto_vwap': 'Reversão perto VWAP',
        'reversao_perto_abertura': 'Reversão perto abertura',
        'reversao_perto_ajuste': 'Reversão perto ajuste',
        'momento_pos_break_max': 'Momentum pós-break máxima',
        'momento_pos_break_min': 'Momentum pós-break mínima',
        
        # VWAP avançado
        'acima_vwap': 'Preço acima VWAP',
        'abaixo_vwap': 'Preço abaixo VWAP',
        'aproximando_vwap': 'Aproximando VWAP',
        'afastando_vwap': 'Afastando VWAP',
        'cruzou_vwap': 'Cruzou VWAP',
        'vwap': 'VWAP intraday',
        'dist_vwap_abs': 'Distância absoluta VWAP',
        'dist_ajuste_oficial_pts': 'Distância ajuste oficial (pontos)',
        'dist_ajuste_oficial_norm': 'Distância ajuste oficial (norm)',
        'acima_ajuste_oficial': 'Acima ajuste oficial',
        'abaixo_ajuste_oficial': 'Abaixo ajuste oficial',
        'ajuste_anterior_oficial': 'Ajuste anterior oficial',
        
        # Regime features (v12.0)
        'regime_realiz_vol': 'Volatilidade realizada (ratio curto/longo)',
        'regime_realiz_vol_bps': 'Volatilidade realizada (bps)',
        'regime_vol_zscore': 'Z-score da volatilidade',
        'regime_aggr_persistencia': 'Persistência do fluxo (EWMA aggr)',
        'regime_cvd_aceleracao': 'Aceleração do CVD',
        'regime_range_dia_norm': 'Range do dia normalizado',
        'regime_pos_vs_vwap': 'Posição vs VWAP',
        'regime_pos_vs_ajuste': 'Posição vs ajuste',
        
        # ATR (v12.0)
        'atr_14': 'ATR 14-period (EWMA)',
        'atr_14_norm': 'ATR normalizado',
        
        # Volume relativo
        'volume_acumulado_dia': 'Volume acumulado no dia',
        'volume_por_minuto': 'Volume por minuto',
        'volume_relativo': 'Volume relativo vs histórico',
        # P1-A26 (v15.21): disponibilidade da baseline p/ distinguir o 1.0
        # de cold start (sem referencia) do 1.0 real (volume normal).
        'referencia_disponivel': 'Baseline histórico disponível p/ o minuto (1.0=fallback se False)',
        'referencia_dias': 'Dias de histórico que sustentam o volume relativo',
        
        # POC migration
        # P0-A28 (v15.23): poc_delta/velocity/direction medem a migracao no
        # grid temporal de 100ms (paridade com o diff()/ewm() do batch):
        #   delta    = pontos de POC na ultima linha de 100ms fechada
        #   velocity = EWMA(alpha=0.1) das deltas por linha (pts/100ms)
        #   direction= sinal da delta da linha
        'poc_delta': 'Delta do POC na ultima linha de 100ms (pts)',
        'poc_velocity': 'Velocidade do POC — EWMA das deltas por linha de 100ms (pts/100ms)',
        'poc_direction': 'Direção da migração do POC (sinal da delta da linha)',
        
        # Session time
        'segundos_desde_abertura': 'Segundos desde abertura',
        'minutos_desde_abertura': 'Minutos desde abertura',
        'minutos_ate_fechamento': 'Minutos até fechamento',
        'sin_horario': 'Senoido do horário',
        'cos_horario': 'Cosseno do horário',
        'bloco_sessao': 'Bloco da sessão',
        
        # Interactions
        'inter_aggr_vwap': 'Interação aggr_imb × dist_vwap_norm',
        'inter_poc_vol': 'Interação dist_poc × vol_1s',
        'aggr_x_dist_vwap': 'aggr_imb × dist_vwap_pts',
        'aggr_x_dist_ajuste_oficial': 'aggr_imb × dist_ajuste_oficial_pts',
        'aggr_x_acima_vwap': 'aggr_imb × acima_vwap',
        'aggr_x_acima_ajuste_oficial': 'aggr_imb × acima_ajuste_oficial',
        'aggr_x_posicao_range_dia': 'aggr_imb × posicao_range_dia',
        'cvd_x_dist_vwap': 'cvd_total × dist_vwap_pts',
        'cvd_x_dist_ajuste_oficial': 'cvd_total × dist_ajuste_oficial_pts',
        'cvd_x_acima_vwap': 'cvd_total × acima_vwap',
        'cvd_x_acima_ajuste_oficial': 'cvd_total × acima_ajuste_oficial',
        'imb_x_dist_vwap': 'imb_L5 × dist_vwap_pts',
        'imb_x_dist_ajuste_oficial': 'imb_L5 × dist_ajuste_oficial_pts',
        'vol_x_acima_vwap': 'vol × acima_vwap',
        'vol_x_acima_ajuste_oficial': 'vol × acima_ajuste_oficial',
        
        # Retorno e volatilidade multi-TF
        'retorno_1x100ms': 'Retorno 100ms',
        'retorno_5x100ms': 'Retorno 500ms',
        'retorno_10x100ms': 'Retorno 1s',
        'retorno_50x100ms': 'Retorno 5s',
        'retorno_100x100ms': 'Retorno 10s',
        'retorno_150x100ms': 'Retorno 15s',
        'retorno_300x100ms': 'Retorno 30s',
        'retorno_500x100ms': 'Retorno 50s (500 x 100ms do master clock)',
        'vol_100ms': 'Volatilidade 100ms',
        'vol_500ms': 'Volatilidade 500ms',
        'vol_1s': 'Volatilidade 1s',
        'vol_5s': 'Volatilidade 5s',
        'vol_15s': 'Volatilidade 15s',
        'vol_1min': 'Volatilidade 1min',
        'vol_5min': 'Volatilidade 5min',
        'range_dia': 'Range do dia',
        'range_dia_norm': 'Range do dia normalizado',
        
        # Contexto preço avançado
        'dist_fechamento_anterior_pts': 'Distância fechamento D-1',
        'dist_fechamento_anterior_norm': 'Distância fechamento D-1 (norm)',
        'dist_maxima_dia_pts': 'Distância máxima do dia',
        'dist_minima_dia_pts': 'Distância mínima do dia',
        'dist_maxima_anterior_pts': 'Distância máxima D-1',
        'dist_minima_anterior_pts': 'Distância mínima D-1',
        'dist_maxima_dia_norm': 'Distância máxima do dia (norm)',
        'dist_minima_dia_norm': 'Distância mínima do dia (norm)',
        'dist_maxima_anterior_norm': 'Distância máxima D-1 (norm)',
        'dist_minima_anterior_norm': 'Distância mínima D-1 (norm)',
        'posicao_range_anterior': 'Posição no range D-1',
        'gap_abertura_fechamento_anterior': 'Gap abertura vs fechamento D-1',
        'gap_abertura_ajuste': 'Gap abertura vs ajuste D-1',
        'acima_ajuste': 'Acima ajuste D-1',
        'abaixo_ajuste': 'Abaixo ajuste D-1',
        'dist_ajuste_abs': 'Distância absoluta ao ajuste',
        'retorno_em_relacao_ao_ajuste': 'Retorno em relação ao ajuste',
        'acima_abertura': 'Acima abertura',
        'abaixo_abertura': 'Abaixo abertura',
        'dist_abertura_reduzindo': 'Distância à abertura reduzindo',
        'perto_maxima': 'Perto da máxima',
        'perto_minima': 'Perto da mínima',
        'rompimento_maxima': 'Rompimento máxima',
        'rompimento_minima': 'Rompimento mínima',
        'rejeicao_maxima': 'Rejeição máxima',
        'rejeicao_minima': 'Rejeição mínima',
        'range_anterior_pts': 'Range D-1 (pontos)',
        'posicao_vs_range_anterior': 'Posição vs range D-1',
        'dist_maxima_anterior': 'Distância máxima D-1',
        'dist_minima_anterior': 'Distância mínima D-1',
        'preco_acima_maxima_anterior': 'Preço acima máxima D-1',
        'preco_abaixo_minima_anterior': 'Preço abaixo mínima D-1',
        'rompimento_maxima_anterior': 'Rompimento máxima D-1',
        'rompimento_minima_anterior': 'Rompimento mínima D-1',
        'abertura_vs_ajuste_pts': 'Abertura vs ajuste D-1',
        'abertura_vs_ajuste_norm': 'Abertura vs ajuste D-1 (norm)',
        'delta_x_acima_abertura': 'Delta × acima_abertura',
        'delta_x_acima_ajuste': 'Delta × acima_ajuste',
        'aggr_imb_x_dist_ajuste_norm': 'aggr_imb × dist_ajuste_norm',
        'aggr_imb_x_posicao_range_dia': 'aggr_imb × posicao_range_dia',
        'aggr_imb_x_dist_maxima_dia_norm': 'aggr_imb × dist_maxima_dia_norm',
        'aggr_imb_x_dist_minima_dia_norm': 'aggr_imb × dist_minima_dia_norm',
        'cvd_norm_x_acima_abertura': 'cvd_norm × acima_abertura',
        'cvd_norm_x_acima_ajuste': 'cvd_norm × acima_ajuste',
        'imb_L5_x_dist_maxima_dia_norm': 'imb_L5 × dist_maxima_dia_norm',
        'imb_L5_x_dist_minima_dia_norm': 'imb_L5 × dist_minima_dia_norm',
    }
    return descriptions.get(name, f'Feature: {name}')
