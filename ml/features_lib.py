# -*- coding: utf-8 -*-
"""
features_lib.py — SHIM DE COMPATIBILIDADE.

Todas as classes e funcoes foram movidas para o pacote features/.
Este arquivo existe apenas para nao quebrar imports antigos:
    from ml.features_lib import GeradorJanelas  # funciona quando rodado de qualquer dir

Gradualmente, todos os imports serao atualizados para:
    from features import GeradorJanelas
"""

from features import *  # noqa: F401,F403
from features import (  # noqa: F401
    ewma_update, hhi, entropia, idade_ms, dias_ate_vencimento,
    fase_sessao, _sanitize, _tod_de_ts, _offset_local_utc_ms,
    MESES_B3, classificar_corretora, asof_join_linhas,
    VPINTracker, OFITracker, BookLevelFeatures,
    JanelaFeatures, GeradorJanelas,
    VolumeProfileTracker, EWMAZScore, KyleLambdaTracker,
    PadroesMemoria, CrossAssetEngine,
    PercentilTracker, RangeTracker, AccumulationTracker,
)