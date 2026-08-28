# -*- coding: utf-8 -*-
"""
config/ — Single source of truth for configuration.

Usage:
    from config import load_config
    cfg = load_config()  # returns ConfigCompleto with all values merged

Priority (highest wins):
    1. config.json (nested + flat keys)
    2. Environment variables (FLAT_ prefix, e.g., FLAT_TP_PTS=120)
    3. ConfigCompleto defaults
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .defaults import (
    ConfigCompleto,
    NESTED_TO_FLAT,
    _aplicar_valor_config,
    _aplicar_chaves_flat,
)


def _load_config_json() -> Dict[str, Any]:
    """Load config.json from project root."""
    root = Path(__file__).parent.parent
    cfg_path = root / 'config.json'
    if not cfg_path.exists():
        return {}
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        print(f'[config] WARNING: config.json invalid ({exc}); ignoring')
        return {}


def _apply_env_overrides(cfg: ConfigCompleto) -> None:
    """Apply environment variable overrides (FLAT_ prefix)."""
    for key in os.environ:
        if not key.startswith('FLAT_'):
            continue
        flat_key = key[5:].lower()
        if not hasattr(cfg, flat_key):
            continue
        val = os.environ[key]
        atual = getattr(cfg, flat_key)
        try:
            setattr(cfg, flat_key, _aplicar_valor_config(atual, val))
        except Exception:
            pass  # ignore bad env values


def _apply_config_json(ext: Dict[str, Any], cfg: ConfigCompleto) -> None:
    """Apply config.json to ConfigCompleto (nested + flat)."""
    # 1. Direct flat keys
    _aplicar_chaves_flat(ext, cfg)

    # 2. Nested sections mapped to flat
    for secao_key, secao_val in ext.items():
        if not isinstance(secao_val, dict):
            continue
        for sub_key, sub_val in secao_val.items():
            caminho = f"{secao_key}.{sub_key}"
            flat_key = NESTED_TO_FLAT.get(caminho)
            if flat_key and hasattr(cfg, flat_key):
                atual = getattr(cfg, flat_key)
                setattr(cfg, flat_key, _aplicar_valor_config(atual, sub_val))

    # 3. Direct nested mappings (trading.* -> flat)
    trading = ext.get('trading', {})
    if isinstance(trading, dict):
        _aplicar_chaves_flat(trading, cfg)

    circuit = ext.get('circuit_breaker', {})
    if isinstance(circuit, dict):
        _aplicar_chaves_flat(circuit, cfg)

    aprendizado = ext.get('aprendizado', {})
    if isinstance(aprendizado, dict):
        _aplicar_chaves_flat(aprendizado, cfg)

    horarios = ext.get('horarios', {})
    if isinstance(horarios, dict):
        _aplicar_chaves_flat(horarios, cfg)

    web = ext.get('web', {})
    if isinstance(web, dict):
        _aplicar_chaves_flat(web, cfg)

    rtd = ext.get('rtd', {})
    if isinstance(rtd, dict):
        _aplicar_chaves_flat(rtd, cfg)

    position = ext.get('position_sizing', {})
    if isinstance(position, dict):
        _aplicar_chaves_flat(position, cfg)


def load_config(config_json: Optional[Dict[str, Any]] = None) -> ConfigCompleto:
    """
    Load unified configuration.

    Args:
        config_json: Optional pre-loaded config dict (for testing).
                    If None, loads from config.json file.

    Returns:
        ConfigCompleto with all defaults, config.json, and env vars merged.
    """
    cfg = ConfigCompleto()

    # Apply config.json
    ext = config_json or _load_config_json()
    if ext:
        _apply_config_json(ext, cfg)

    # Apply environment overrides
    _apply_env_overrides(cfg)

    # Derived fields
    cfg.ativos = cfg.ativos or ['WINV26', 'WDOU26']
    cfg.ativo_principal = cfg.ativo_principal or cfg.ativos[0]
    cfg.ativo_contexto = cfg.ativo_contexto or (cfg.ativos[1] if len(cfg.ativos) > 1 else '')

    # Validate critical values
    _validate_config(cfg)

    return cfg


def _validate_config(cfg: ConfigCompleto) -> None:
    """Validate critical config values, warn on issues."""
    issues = []

    if cfg.tp_pts <= 0:
        issues.append(f'tp_pts must be > 0, got {cfg.tp_pts}')
    if cfg.sl_pts <= 0:
        issues.append(f'sl_pts must be > 0, got {cfg.sl_pts}')
    if cfg.tp_pts < cfg.sl_pts:
        issues.append(f'tp_pts ({cfg.tp_pts}) < sl_pts ({cfg.sl_pts}) — possible inverted config')
    if not (0.0 <= cfg.ml_threshold <= 1.0):
        issues.append(f'ml_threshold must be 0-1, got {cfg.ml_threshold}')
    if cfg.max_trades_dia <= 0:
        issues.append(f'max_trades_dia must be > 0, got {cfg.max_trades_dia}')

    for issue in issues:
        print(f'[config] WARNING: {issue}')

    if issues:
        print(f'[config] Config validation: {len(issues)} issue(s) found')
    else:
        print(f'[config] Config validated: TP={cfg.tp_pts} SL={cfg.sl_pts} threshold={cfg.ml_threshold} '
              f'max_trades={cfg.max_trades_dia} modelo={cfg.ml_modelo or "none"}')


# Backward compatibility: expose CONFIG dict like old config.py
def _config_to_dict(cfg: ConfigCompleto) -> Dict[str, Any]:
    """Convert ConfigCompleto to nested dict compatible with old CONFIG."""
    return {
        'trading': {
            'tp_pts': cfg.tp_pts,
            'sl_pts': cfg.sl_pts,
            'max_holding_s': cfg.max_holding_s,
            'max_trades_dia': cfg.max_trades_dia,
            'max_drawdown_dia': cfg.max_drawdown_dia_pontos,
            'custo_execucao': cfg.custos_execucao_pontos,
        },
        'ml_threshold': cfg.ml_threshold,
        'ativos': cfg.ativos,
        'ativo_principal': cfg.ativo_principal,
        'ativo_contexto': cfg.ativo_contexto,
        'ml_modelo': cfg.ml_modelo,
        'save_dir': cfg.save_dir,
        'book_split': cfg.book_split,
        'web': {'host': cfg.web_host, 'port': cfg.web_port},
        'rtd': {
            'book_linhas': cfg.book_linhas,
            'tt_linhas': cfg.tt_linhas,
            'poll_s': cfg.poll_s,
            'max_janelas': cfg.max_janelas,
        },
        'horarios': {
            'abertura_fim': cfg.horario_abertura_fim,
            'almoco_inicio': cfg.horario_almoco_inicio,
            'almoco_fim': cfg.horario_almoco_fim,
            'fechamento': cfg.horario_fechamento,
        },
        'circuit_breaker': {
            'nivel1_perdas': cfg.cb_nivel1_perdas,
            'nivel1_pnl': cfg.cb_nivel1_pnl,
            'nivel2_perdas': cfg.cb_nivel2_perdas,
            'nivel2_pnl': cfg.cb_nivel2_pnl,
            'nivel3_perdas': cfg.cb_nivel3_perdas,
            'nivel3_pnl': cfg.cb_nivel3_pnl,
        },
        'aprendizado': {
            'delta': cfg.aprendizado_delta,
            'decay': cfg.aprendizado_decay,
            'min_amostras': cfg.aprendizado_min_amostras,
        },
        'position_sizing': {
            'target_risk_per_trade': cfg.target_risk_per_trade,
            'max_position_size': cfg.max_position_size,
        },
        'desligar_horarios_ruins': cfg.desligar_horarios_ruins,
        'normalizar_score': cfg.normalizar_score,
        'faixas_preco': cfg.faixas_preco,
        'cooldown_entre_trades_s': cfg.cooldown_entre_trades_s,
    }


# Global instance (lazy loaded)
_CONFIG_INSTANCE: Optional[ConfigCompleto] = None


def get_config() -> ConfigCompleto:
    """Get singleton config instance (loads on first call)."""
    global _CONFIG_INSTANCE
    if _CONFIG_INSTANCE is None:
        _CONFIG_INSTANCE = load_config()
    return _CONFIG_INSTANCE


def get_config_dict() -> Dict[str, Any]:
    """Get config as nested dict (backward compatible with old CONFIG)."""
    return _config_to_dict(get_config())


# For backward compatibility with config.py
CONFIG = get_config_dict()
SAVE_DIR = CONFIG['save_dir']
ATIVO_PRINCIPAL = CONFIG['ativo_principal']
ATIVO_CONTEXTO = CONFIG['ativo_contexto']

# Re-export from defaults for backward compatibility
from .defaults import (  # noqa: F401, E402
    ConfigCompleto,
    _aplicar_valor_config,
    _aplicar_chaves_flat,
    _aplicar_config_externa,
    NESTED_TO_FLAT,
    _NESTED_TO_FLAT,
)

# Compatibilidade de path: testes e scripts usam
# os.path.dirname(cfg_mod.__file__) para achar config.json na raiz.
# Apontamos __file__ para o config.py da raiz para preservar esse contrato.
__file__ = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.py")