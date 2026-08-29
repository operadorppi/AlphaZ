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
        """Extrai valores na ordem exata que o modelo espera.
        
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
        'n_eventos_janela': 'Número de eventos na janela de 1s',
        'vol_total': 'Volume total negociado',
        'aggr_imb': 'Imbalance de agressão (compra - venda) / total',
        'ewma_imb_longa': 'EWMA do imbalance (janela longa)',
        'hhi_compra': 'HHI concentração compradora',
        'hhi_venda': 'HHI concentração vendedora',
        'delta_preco_janela': 'Variação de preço na janela (pontos)',
        'vpin': 'Volume-Synchronized Probability of Informed Trading',
        'preco_ultimo': 'Último preço de negociação',
        'cvd_total': 'Cumulative Volume Delta total',
        'realized_vol_bps': 'Volatilidade realizada em bps',
        'range_vol_bps': 'Amplitude do range em bps',
        'vp_poc_dist': 'Distância ao POC (Volume Profile)',
        'vp_vah_dist': 'Distância ao VAH (Value Area High)',
        'vp_val_dist': 'Distância ao VAL (Value Area Low)',
        'vp_vp_total': 'Volume total do Volume Profile',
        'kyle_kyle_lambda': 'Kyle\'s Lambda (impacto de preço)',
        'spread': 'Spread bid-ask',
        'ofi': 'Order Flow Imbalance',
        'microprice': 'Microprice (preço ponderado por volume)',
        'vol_1s': 'Volume no último segundo',
        'delta_vol_janela': 'Variação de volume na janela',
        'imb_book': 'Imbalance do book',
        'imb_L1': 'Imbalance nível 1 do book',
        'imb_L10': 'Imbalance médio 10 níveis',
        'hhi_book': 'HHI do book (concentração)',
        'micro_drift_bps': 'Microprice drift em bps',
        'micro_drift_ewma': 'Microprice drift EWMA',
        'imb_ponderado': 'Imbalance ponderado por profundidade',
        'slope_bid': 'Slope do book comprador',
        'slope_ask': 'Slope do book vendedor',
    }
    return descriptions.get(name, f'Feature: {name}')
