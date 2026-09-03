# config.py - carga unica de config.json para scripts offline.
# v10.0: Now uses unified config package (config/__init__.py)
# This file maintains backward compatibility.
# 
# Nota: Este arquivo é código morto pois "import config" resolve
# para o pacote config/ (diretório com __init__.py) em vez deste arquivo.
# Para scripts standalone que precisam deste arquivo, usar:
#   python config.py  (executar diretamente)
# Ou acessar via:
#   from config import get_config_dict
# 
# O pacote config/__init__.py já exporta as mesmas funções.
from config import (
    CONFIG, SAVE_DIR, ATIVO_PRINCIPAL, ATIVO_CONTEXTO, 
    get_config, load_config, get_config_dict
)

# Re-export for backward compatibility
__all__ = [
    'CONFIG', 'SAVE_DIR', 'ATIVO_PRINCIPAL', 'ATIVO_CONTEXTO', 
    'get_config', 'load_config', 'get_config_dict'
]
