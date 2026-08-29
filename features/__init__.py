# -*- coding: utf-8 -*-
"""
features/ — Camada de features de microestrutura e contexto.

Importa de features_lib para compatibilidade — gradualmente
features_lib sera removido e tudo passara por aqui.
"""

# Re-export tudo para compatibilidade com imports antigos:
#   from features_lib import X  ->  from features import X
from .utils import (
    ewma_update, hhi, entropia, idade_ms, dias_ate_vencimento,
    fase_sessao, _sanitize, _tod_de_ts, _offset_local_utc_ms,
    MESES_B3, classificar_corretora, asof_join_linhas,
)
from .vpin import VPINTracker
from .book_features import OFITracker, BookLevelFeatures
from .trade_features import JanelaFeatures, GeradorJanelas
from .volume_profile import VolumeProfileTracker
from .ewma_zscore import EWMAZScore
from .kyle_lambda import KyleLambdaTracker
from .patterns import PadroesMemoria
from .cross_asset import CrossAssetEngine, CrossAssetManager
from .percentil import PercentilTracker, RangeTracker, AccumulationTracker
from .institutional_context import InstitutionalContext

__all__ = [
    # Funcoes
    'ewma_update', 'hhi', 'entropia', 'idade_ms',
    'dias_ate_vencimento', 'fase_sessao', '_sanitize',
    '_tod_de_ts', '_offset_local_utc_ms', 'MESES_B3',
    'classificar_corretora', 'asof_join_linhas',
    # Classes
    'VPINTracker', 'OFITracker', 'BookLevelFeatures',
    'JanelaFeatures', 'GeradorJanelas',
    'VolumeProfileTracker', 'EWMAZScore', 'KyleLambdaTracker',
    'PadroesMemoria',    'CrossAssetEngine', 'CrossAssetManager',
    'PercentilTracker', 'RangeTracker', 'AccumulationTracker',
    'InstitutionalContext',
]
