"""Pacote de configuração (FASE 10 P1).

Uma única resolução de configuração para todo o projeto:

- ``config.defaults``: **única fonte de verdade** dos padrões
  (incluindo ``DEFAULT_MAX_DRAWDOWN_DIA``);
- ``config.loader``: ``load_config`` aplica a prioridade
  P1 (overrides/ConfigCompleto) > P2 (config.json:environments[ENV]) >
  P3 (config.json:raiz) > P4 (defaults);
- compatibilidade legado: chaves renomeadas são mapeadas e registradas;
  chaves proibidas e conflitos explícitos geram ``ConfigError``.

Os presets ``mlgate`` e ``replaygate`` derivam de ``config.defaults``
(sem duplicação). Não importa ``mlgate``/``replaygate`` em nível de
módulo (as projeções ``Config.to_ml_policy``/``to_env_policy`` usam
import local para manter a DAG de importação sem ciclos).
"""

from config.defaults import (
    DEFAULT_ENV_PRESETS,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MAX_DRAWDOWN_DIA,
    MAX_MAX_DRAWDOWN_DIA,
    MIN_MAX_DRAWDOWN_DIA,
    VALID_ENVIRONMENTS,
)
from config.errors import ConfigError
from config.loader import (
    Config,
    ConfigCompleto,
    FORBIDDEN_KEYS,
    KNOWN_ENV_KEYS,
    KNOWN_KEYS,
    LEGACY_KEY_MAP,
    load_config,
)

# ============================================================
# Compatibilidade legado (API antiga)
# ============================================================

def _load_legacy_config():
    """Carrega config.json na API antiga (dict com chaves flat)."""
    import json
    from pathlib import Path
    config_file = Path('config.json')
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# Cache da config legacy
_legacy_config = None

def get_config():
    """Retorna config loader (compatibilidade legado)."""
    return load_config

def get_config_dict():
    """Retorna config como dict (compatibilidade legado)."""
    global _legacy_config
    if _legacy_config is None:
        _legacy_config = _load_legacy_config()
        # Adicionar valores default se não existirem
        _legacy_config.setdefault('save_dir', r'D:\MarketData\mimo')
        _legacy_config.setdefault('ativo_principal', 'WINV26')
        _legacy_config.setdefault('ativo_contexto', 'WDOV26')
    return _legacy_config

# Exportar para compatibilidade legado
# v14.8: CONFIG nunca deve ser None — era None e qualquer módulo que lia
# `config.CONFIG['...']` (ex: testes de book_split, labeler) quebrava com
# 'NoneType' object is not subscriptable. Agora é um dict real carregado
# do config.json (mesma fonte do App via get_config_dict).
CONFIG = get_config_dict()  # dict legado (chaves do config.json + defaults)
SAVE_DIR = r'D:\MarketData\mimo'
ATIVO_PRINCIPAL = 'WINV26'
ATIVO_CONTEXTO = 'WDOV26'

__all__ = [
    "CONFIG",
    "SAVE_DIR",
    "ATIVO_PRINCIPAL",
    "ATIVO_CONTEXTO",
    "Config",
    "ConfigCompleto",
    "ConfigError",
    "DEFAULT_ENV_PRESETS",
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_MAX_DRAWDOWN_DIA",
    "FORBIDDEN_KEYS",
    "KNOWN_ENV_KEYS",
    "KNOWN_KEYS",
    "LEGACY_KEY_MAP",
    "MAX_MAX_DRAWDOWN_DIA",
    "MIN_MAX_DRAWDOWN_DIA",
    "VALID_ENVIRONMENTS",
    "load_config",
    "get_config",
    "get_config_dict",
]
