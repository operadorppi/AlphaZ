import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_config_flat.py — Testes R3: config.json é aplicado de verdade (flat + aninhado)

v14.8: o refactor da FASE 10 substituiu a API antiga (_aplicar_valor_config,
_aplicar_chaves_flat, _aplicar_config_externa, ConfigCompleto()) pelo loader
único (config/loader.py). Estes testes validam o MESMO objetivo com a API
atual:

  - load_config() resolve P1(overrides) > P2(environments[ENV]) > P3(raiz) > P4(defaults)
  - Chaves operacionais (ativos, rtd, tick_values, ...) vão para Config.extra
  - Chaves desconhecidas NÃO são descartadas silenciosamente (ficam em extra)
  - Chaves proibidas (FORBIDDEN_KEYS) geram ConfigError
  - bools e números são estritos (sem coerção silenciosa)
"""
import sys
import os
import json
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import config as cfg_mod
from config.errors import ConfigError


class TestValidacaoEstrita:
    """Validação de tipos no loader (substitui _aplicar_valor_config)."""

    def test_bool_estrito(self):
        assert cfg_mod.loader._strict_bool(True, 'x') is True
        assert cfg_mod.loader._strict_bool(False, 'x') is False

    def test_bool_rejeita_coercao(self):
        # bool nunca vira True por número/string (divergência silenciosa é erro)
        for bad in (1, 0, 'true', 'false', '1', 0.0):
            with pytest.raises(ConfigError):
                cfg_mod.loader._strict_bool(bad, 'x')

    def test_decimal_estrito(self):
        d = cfg_mod.loader._to_decimal('0.02', 'x')
        assert d == Decimal('0.02')
        assert isinstance(d, Decimal)

    def test_decimal_rejeita_bool(self):
        with pytest.raises(ConfigError):
            cfg_mod.loader._to_decimal(True, 'x')


class TestAplicarChavesFlat:
    """R3: chaves flat são aplicadas; desconhecidas vão para extra (não somem)."""

    def test_chaves_operacionais_vao_para_extra(self):
        """Chaves operacionais (ativos, rtd, ...) ficam acessíveis via extra."""
        cfg = cfg_mod.load_config(overrides={
            'ativos': ['WINV26', 'WDOV26'],
            'rtd': {'book_linhas': 500},
            'chave_inexistente': 123,
        })
        assert cfg.extra['ativos'] == ['WINV26', 'WDOV26']
        assert cfg.extra['rtd'] == {'book_linhas': 500}
        # Desconhecidas NÃO são descartadas silenciosamente — ficam em extra
        assert cfg.extra['chave_inexistente'] == 123
        # Acesso dict-like também funciona
        assert cfg['ativos'] == ['WINV26', 'WDOV26']

    def test_chaves_proibidas_geram_erro(self):
        """FORBIDDEN_KEYS (conceitos eliminados) geram ConfigError explícito."""
        with pytest.raises(ConfigError):
            cfg_mod.load_config(overrides={'exposure_atual': 100})

    def test_legado_renomeado_e_registrado(self):
        """Chave legada (exigir_replay) é renomeada e registrada em legacy_used."""
        cfg = cfg_mod.load_config(overrides={'exigir_replay': True})
        assert cfg.require_replay_validated is True
        assert 'exigir_replay' in cfg.legacy_used

    def test_override_prevalece_sobre_json(self):
        """P1 (overrides) prevalece sobre P3 (config.json raiz)."""
        cfg = cfg_mod.load_config(overrides={'max_drawdown_dia': 0.01})
        assert cfg.max_drawdown_dia == Decimal('0.01')

    def test_paridade_config_json_real(self):
        """Para cada chave operacional do config.json, o Config efetivo reflete
        o valor do JSON (via extra). Chaves gate resolvem nos campos."""
        cfg_path = os.path.join(_base, 'config.json')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            ext = json.load(f)

        cfg = cfg_mod.load_config(path=cfg_path)

        # Chaves gate resolvem em campos
        assert cfg.environment == ext.get('environment', 'DEVELOPMENT')
        assert cfg.ml_required == ext.get('ml_required', False)
        assert cfg.fallback_enabled == ext.get('fallback_enabled', True)
        assert cfg.require_replay_validated == ext.get('require_replay_validated', False)
        assert cfg.label == ext.get('label', '')

        # Chaves operacionais ficam em extra — paridade direta
        verificadas = 0
        for chave, valor in ext.items():
            if chave in ('environment', 'ml_required', 'fallback_enabled',
                         'require_replay_validated', 'max_drawdown_dia', 'label'):
                continue
            assert cfg.extra.get(chave) == valor, (
                f"chave operacional '{chave}' divergiu: extra={cfg.extra.get(chave)!r} "
                f"config.json={valor!r}"
            )
            verificadas += 1

        assert verificadas >= 5, (
            f"Poucas chaves operacionais verificadas ({verificadas}) — config.json mudou?"
        )