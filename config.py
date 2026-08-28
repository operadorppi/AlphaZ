# config.py - carga unica de config.json para scripts offline.
# v10.0: Now uses unified config package (config/__init__.py)
# This file maintains backward compatibility.
from config import (
    CONFIG, SAVE_DIR, ATIVO_PRINCIPAL, ATIVO_CONTEXTO, 
    get_config, load_config, get_config_dict
)

# Re-export for backward compatibility
__all__ = [
    'CONFIG', 'SAVE_DIR', 'ATIVO_PRINCIPAL', 'ATIVO_CONTEXTO', 
    'get_config', 'load_config', 'get_config_dict'
]
