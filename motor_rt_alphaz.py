# -*- coding: utf-8 -*-
"""
motor_rt_alphaz.py — SHIM de compatibilidade (v10.0).

O motor original foi migrado para core/app.py e arquivado em
docs/archive/motor_rt_alphaz_v9_legacy.py.

Este shim permite que código antigo que faz `import motor_rt_alphaz`
continue funcionando, redirecionando para core.app.App.

Para novos código, use: from core.app import App
"""
import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from core.app import App, _AnaliseShim as Analise  # noqa: F401
from core.market_state import check_staleness as _sem_dados_por_ativo # noqa: F401
from core.utils import parse_hms_ms # noqa: F401

# Funções auxiliares que alguns testes esperam
from datetime import datetime  # noqa: F401 — testes fazem monkeypatch.setattr(mra, 'datetime', ...)