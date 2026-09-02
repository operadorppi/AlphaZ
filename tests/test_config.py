"""FASE 10 P1 — testes de configuração (fonte única, prioridade, legado, integração)."""

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

import config
from config import (
    KNOWN_KEYS,
    KNOWN_ENV_KEYS,
    LEGACY_KEY_MAP,
    FORBIDDEN_KEYS,
    DEFAULT_MAX_DRAWDOWN_DIA,
    load_config,
    ConfigCompleto,
    ConfigError,
)


class TestDefaultSourceUniqueness:
    """max_drawdown_dia tem EXATAMENTE uma fonte de verdade no projeto."""

    def test_only_defaults_defines_max_drawdown(self):
        from config.defaults import DEFAULT_MAX_DRAWDOWN_DIA as src
        assert src == Decimal("0.02")
        # mlgate e replaygate nao devem definir este padrão
        import mlgate, replaygate
        for mod in (mlgate, replaygate):
            ns = vars(mod)
            for k in ("MAX_MAX_DRAWDOWN", "MIN_MAX_DRAWDOWN",
                      "DEFAULT_MAX_DRAWDOWN", "max_drawdown"):
                assert not any(k.lower() in name.lower() for name in ns), \
                    f"{mod.__name__} duplica max_drawdown_dia em {k}"

    def test_presets_derived_from_defaults(self):
        from config.defaults import DEFAULT_ENV_PRESETS
        from mlgate import PRODUCTION_POLICY as mlp, DEVELOPMENT_POLICY as dlp
        from replaygate import PRODUCTION_ENV_POLICY as prep, DEVELOPMENT_ENV_POLICY as dep

        assert mlp.ml_required == DEFAULT_ENV_PRESETS["PRODUCTION"].ml.ml_required
        assert mlp.fallback_enabled == DEFAULT_ENV_PRESETS["PRODUCTION"].ml.fallback_enabled
        assert dlp.ml_required == DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.ml_required
        assert dlp.fallback_enabled == DEFAULT_ENV_PRESETS["DEVELOPMENT"].ml.fallback_enabled

        assert prep.require_replay_validated == DEFAULT_ENV_PRESETS["PRODUCTION"].require_replay_validated
        assert dep.require_replay_validated == DEFAULT_ENV_PRESETS["DEVELOPMENT"].require_replay_validated


class TestPriorityLevels:
    """P1 (overrides) > P2 (config.json:environments) > P3 (config.json root) > P4 (defaults)."""

    def test_overrides_win_over_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "environment": "PAPER",
            "ml_required": True,
            "fallback_enabled": False,
            "max_drawdown_dia": 0.05,
            "label": "from-json",
        }), encoding="utf-8")
        c = load_config(path=cfg, overrides={"ml_required": False, "label": "p1"})
        assert c.ml_required is False
        assert c.label == "p1"
        assert c.max_drawdown_dia == Decimal("0.05")
        assert c.sources.get("ml_required") == "config_completo"

    def test_environment_arg_wins_over_json_root(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "environment": "DEVELOPMENT",
        }), encoding="utf-8")
        c = load_config(path=cfg, environment="PRODUCTION")
        assert c.environment == "PRODUCTION"
        assert c.require_replay_validated is True

    def test_json_env_section_over_json_root(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "environment": "DEVELOPMENT",
            "ml_required": False,
            "environments": {
                "PRODUCTION": {"ml_required": True, "require_replay_validated": True},
            },
        }), encoding="utf-8")
        c = load_config(path=cfg, environment="PRODUCTION")
        assert c.ml_required is True
        assert c.require_replay_validated is True
        assert c.sources.get("ml_required") == "config.json:environments[PRODUCTION]"

    def test_default_when_no_json(self):
        c = load_config()
        assert c.environment == "DEVELOPMENT"
        assert c.ml_required is False
        assert c.fallback_enabled is True
        assert c.require_replay_validated is False
        assert c.max_drawdown_dia == DEFAULT_MAX_DRAWDOWN_DIA


class TestLegacyCompatibility:
    def test_legacy_rename(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"drawdown_max_dia": 0.01}), encoding="utf-8")
        c = load_config(path=cfg)
        assert c.max_drawdown_dia == Decimal("0.01")
        assert c.legacy_used == ("drawdown_max_dia",)

    def test_legacy_in_env_section(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "environments": {
                "PAPER": {"exigir_replay": True, "ml_obrigatorio": True},
            }
        }), encoding="utf-8")
        c = load_config(path=cfg, environment="PAPER")
        assert c.require_replay_validated is True
        assert c.ml_required is True
        assert set(c.legacy_used) == {"exigir_replay", "ml_obrigatorio"}

    def test_conflict_legacy_and_new_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"drawdown_max_dia": 0.01, "max_drawdown_dia": 0.02}),
                       encoding="utf-8")
        with pytest.raises(ConfigError, match="conflito"):
            load_config(path=cfg)

    def test_forbidden_key_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"exposure_atual": 100}), encoding="utf-8")
        with pytest.raises(ConfigError, match="PROIBIDA|proibida"):
            load_config(path=cfg)

    def test_unknown_key_stored_in_extra(self, tmp_path):
        """Chaves desconhecidas ficam em Config.extra (não rejeitadas)."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"foo_bar": True, "ativos": ["WIN"]}), encoding="utf-8")
        c = load_config(path=cfg)
        assert c.extra.get("foo_bar") is True
        assert c.extra.get("ativos") == ["WIN"]


class TestValidation:
    def test_strict_bool_rejects_int(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"ml_required": 1}), encoding="utf-8")
        with pytest.raises(ConfigError, match="esperado bool"):
            load_config(path=cfg)

    def test_drawdown_zero_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"max_drawdown_dia": 0}), encoding="utf-8")
        with pytest.raises(ConfigError, match="deve estar em"):
            load_config(path=cfg)

    def test_drawdown_one_ok(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"max_drawdown_dia": 1}), encoding="utf-8")
        c = load_config(path=cfg)
        assert c.max_drawdown_dia == Decimal("1")

    def test_drawdown_above_one_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"max_drawdown_dia": 1.1}), encoding="utf-8")
        with pytest.raises(ConfigError, match="deve estar em"):
            load_config(path=cfg)

    def test_invalid_environment_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"environment": "STAGING"}), encoding="utf-8")
        with pytest.raises(ConfigError, match="ambiente inválido"):
            load_config(path=cfg)

    def test_empty_label_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"label": ""}), encoding="utf-8")
        with pytest.raises(ConfigError, match="label deve ser texto"):
            load_config(path=cfg)

    def test_non_bool_legacy_flag_raises(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"usar_fallback": 1}), encoding="utf-8")
        with pytest.raises(ConfigError, match="esperado bool"):
            load_config(path=cfg)


class TestConfigCompletoProjection:
    def test_to_ml_policy_aligned_with_production(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"environment": "PRODUCTION"}), encoding="utf-8")
        c = load_config(path=cfg, environment="PRODUCTION")
        pol = c.to_ml_policy()
        from mlgate import PRODUCTION_POLICY
        assert pol.ml_required == PRODUCTION_POLICY.ml_required
        assert pol.fallback_enabled == PRODUCTION_POLICY.fallback_enabled

    def test_to_env_policy_aligned_with_production(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"environment": "PRODUCTION"}), encoding="utf-8")
        c = load_config(path=cfg, environment="PRODUCTION")
        pol = c.to_env_policy()
        from replaygate import PRODUCTION_ENV_POLICY
        assert pol.environment.name == "PRODUCTION"
        assert pol.require_replay_validated == PRODUCTION_ENV_POLICY.require_replay_validated
        assert pol.ml.ml_required == PRODUCTION_ENV_POLICY.ml.ml_required

    def test_as_dict_roundtrip(self):
        c = load_config(environment="PAPER")
        d = c.as_dict()
        assert isinstance(d["max_drawdown_dia"], str)
        assert Decimal(d["max_drawdown_dia"]) == c.max_drawdown_dia
        assert isinstance(d["ml_required"], bool)


class TestIntegrationWithGates:
    """Config resolve → Gate decision works end-to-end."""

    def test_production_config_blocks_without_replay(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"environment": "PRODUCTION"}), encoding="utf-8")
        c = load_config(path=cfg, environment="PRODUCTION")
        from mlgate import MlAvailability
        from replaygate import ReplayStatus, evaluate_replay_gate
        d = evaluate_replay_gate(MlAvailability.up(), ReplayStatus.pending("pendente"), c.to_env_policy())
        assert d.allowed is False
        assert d.decision_source == "BLOCKED"

    def test_development_config_allows_with_pending_replay(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"environment": "DEVELOPMENT"}), encoding="utf-8")
        c = load_config(path=cfg, environment="DEVELOPMENT")
        from mlgate import MlAvailability
        from replaygate import ReplayStatus, evaluate_replay_gate
        d = evaluate_replay_gate(MlAvailability.up(), ReplayStatus.pending("pendente"), c.to_env_policy())
        assert d.allowed is True
        assert d.decision_source == "ML"


class TestBackwardCompatibility:
    """Os testes das FASES 8 e 9 continuam passando."""

    def test_mlgate_presets_unchanged_values(self):
        from mlgate import PRODUCTION_POLICY, DEVELOPMENT_POLICY
        assert PRODUCTION_POLICY.ml_required is True
        assert PRODUCTION_POLICY.fallback_enabled is False
        assert DEVELOPMENT_POLICY.ml_required is False
        assert DEVELOPMENT_POLICY.fallback_enabled is True

    def test_replaygate_presets_unchanged_values(self):
        from replaygate import PRODUCTION_ENV_POLICY, DEVELOPMENT_ENV_POLICY, PAPER_ENV_POLICY
        assert PRODUCTION_ENV_POLICY.ml.ml_required is True
        assert PRODUCTION_ENV_POLICY.require_replay_validated is True
        assert DEVELOPMENT_ENV_POLICY.ml.ml_required is False
        assert DEVELOPMENT_ENV_POLICY.require_replay_validated is False
        assert PAPER_ENV_POLICY.ml.ml_required is True
        assert PAPER_ENV_POLICY.require_replay_validated is False
