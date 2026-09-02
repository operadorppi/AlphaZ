# -*- coding: utf-8 -*-
"""
motor_rt_alphaz.py — SHIM de compatibilidade (v10.0).

O motor original foi migrado para core/app.py e arquivado em
docs/archive/motor_rt_alphaz_v9_legacy.py.

Este shim permite que código antigo que faz `import motor_rt_alphaz`
continue funcionando, redirecionando para core.app.App.

Para novos código, use: from core.app import App

IMPORTANTE: Todos os imports são LAZY para evitar que falhas em
módulos pesados (pyarrow, comtypes) matem o import inteiro.
"""
import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)


# ------------------------------------------------------------------
# Lazy imports — cada atributo é carregado sob demanda via __getattr__
# ------------------------------------------------------------------

_loaded = {}


def _ensure(name, module_path, attr):
    """Carrega module_path.attr sob demanda, cacheia em _loaded."""
    if attr not in _loaded:
        import importlib
        mod = importlib.import_module(module_path)
        _loaded[attr] = getattr(mod, attr)
    return _loaded[attr]


def __getattr__(name):
    """Suporte a lazy import: motor_rt_alphaz.App, etc."""
    _LAZY = {
        'App':               ('core.app', 'App'),
        'Analise':           ('core.app', '_AnaliseShim'),
        '_sem_dados_por_ativo': ('core.market_state', 'check_staleness'),
        'parse_hms_ms':      ('core.utils', 'parse_hms_ms'),
        'datetime':          ('datetime', 'datetime'),
    }
    if name in _LAZY:
        module_path, attr = _LAZY[name]
        return _ensure(name, module_path, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
