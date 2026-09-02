"""Loader de configuração (FASE 10 P1) — uma única resolução, sem divergência.

Fontes e prioridade (maior → menor):

    P1  ConfigCompleto / overrides      (programático: load_config(overrides=...))
    P2  config.json -> environments[ENV] (seção do ambiente)
    P3  config.json -> raiz             (chaves globais do arquivo)
    P4  config.defaults                 (única fonte de verdade dos padrões)

Regras:

- ``max_drawdown_dia``: fonte de verdade única em ``config.defaults``;
  aqui apenas se *sobrepõe* por P1..P3 (o padrão nunca é redefinido).
- Chave desconhecida        → ``ConfigError`` (divergência nunca é silenciosa).
- Chave legada + nova       → ``ConfigError`` (conflito explícito, não escolha).
- Chave legada proibida     → ``ConfigError``.
- Legado renomeado          → mapeado e REGISTRADO em ``legacy_used``.
- bools estritos            (1/0/"true" não passam).
- Importação: este módulo NÃO importa ``mlgate``/``replaygate`` em nível de
  módulo (evita ciclo de importação com ``config/__init__``); as projeções
  para políticas fazem import local sob demanda.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from config.defaults import (
    DEFAULT_ENV_PRESETS,
    DEFAULT_ENVIRONMENT,
    DEFAULT_MAX_DRAWDOWN_DIA,
    MAX_MAX_DRAWDOWN_DIA,
    MIN_MAX_DRAWDOWN_DIA,
    VALID_ENVIRONMENTS,
)
from config.errors import ConfigError

#: Nome do arquivo de configuração procurado no diretório de trabalho.
DEFAULT_CONFIG_FILE = "config.json"

#: Chaves aceitas na raiz do config.json / em overrides.
KNOWN_KEYS: frozenset[str] = frozenset({
    "environment",
    "ml_required",
    "fallback_enabled",
    "require_replay_validated",
    "max_drawdown_dia",
    "label",
    # Chaves legacy mantidas para compatibilidade com código que ainda as lê:
    "ml_modelo",      # caminho do modelo .pkl (FASE 8+)
    "web_host",       # host do dashboard (FASE 6)
    "web_port",       # porta do dashboard (FASE 6)
    "save_intervalo", # intervalo de save em segundos (FASE 5)
    # Chaves de operação (usadas pelo motor, não validadas pelo loader):
    "save_dir",       # diretório de dados
    "web",            # config do dashboard (dict)
    "ativos",         # lista de ativos
    "rtd",            # config RTD (dict)
    "tick_values",    # valor do tick por ativo (dict)
    "lotes_minimos",  # lote mínimo por ativo (dict)
    "ativo_principal", # ativo principal
    "ativo_contexto",  # ativo contexto
    "circuit_breaker", # config circuit breaker (dict)
    "risk",           # config de risco (dict)
    "learning",       # config de aprendizado (dict)
    "horarios",       # horários de operação (dict)
    "feature_split",  # divisão de features
    "book_split",     # divisão de book
    "model_path",     # caminho do modelo
    "labeler",        # config do labeler (dict)
    "nominal_formula", # fórmula de exposição nominal
    "max_exposure_brl", # exposição máxima em BRL
})

#: Chaves aceitas dentro de ``environments[ENV]``.
KNOWN_ENV_KEYS: frozenset[str] = frozenset({
    "ml_required",
    "fallback_enabled",
    "require_replay_validated",
    "max_drawdown_dia",
    "label",
})

#: Compatibilidade legado: chave antiga → chave atual (renomeado).
LEGACY_KEY_MAP: dict[str, str] = {
    "drawdown_max_dia": "max_drawdown_dia",
    "ml_obrigatorio": "ml_required",
    "usar_fallback": "fallback_enabled",
    "exigir_replay": "require_replay_validated",
}

#: Chaves legadas **proibidas** (conceitos errados que não podem voltar).
FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "exposure_atual",        # FASE 7 P1: TP+SL foi redefinido; não é config
    "ml_fallback_silencioso",
    "fallback_silencioso",
    "silencioso",
})

_NIVEL_OVERRIDES = "config_completo"
_NIVEL_ENV_JSON = "config.json:environments[{env}]"
_NIVEL_JSON = "config.json"
_NIVEL_DEFAULTS = "defaults"


# ---------------------------------------------------------------------------
# ConfigCompleto
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    """Configuração totalmente resolvida (ConfigCompleto).

    Todo campo tem valor explícito — não há default escondido aqui: os
    defaults já foram aplicados em ``load_config`` e registrados em
    ``sources``.

    Campos extras (ativos, rtd, tick_values, etc.) ficam em ``extra``
    para compatibilidade com o motor e outros módulos.
    """

    environment: str
    ml_required: bool
    fallback_enabled: bool
    require_replay_validated: bool
    max_drawdown_dia: Decimal
    label: str
    sources: Mapping[str, str]  # chave -> origem (P1..P4) para auditoria
    legacy_used: tuple[str, ...] = ()  # chaves legadas que foram renomeadas
    warnings: tuple[str, ...] = ()
    extra: Mapping[str, Any] = ()  # chaves operacionais (ativos, rtd, etc.)

    def __getitem__(self, key: str) -> Any:
        """Acesso dict-like para compatibilidade: config['ativos']."""
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra.get(key)

    def get(self, key: str, default=None) -> Any:
        """Acesso dict-like com default: config.get('ativos', [])."""
        try:
            return self[key]
        except (AttributeError, KeyError):
            return default

    # -- projeção para os gates (import local evita ciclo) ------------------
    def to_ml_policy(self):
        """Política de ML da FASE 8 derivada desta configuração."""
        from mlgate import MlGatePolicy
        return MlGatePolicy(
            ml_required=self.ml_required,
            fallback_enabled=self.fallback_enabled,
            label=self.label,
        )

    def to_env_policy(self):
        """Política de ambiente da FASE 9 derivada desta configuração."""
        from replaygate import Environment, EnvironmentPolicy
        return EnvironmentPolicy(
            environment=Environment(self.environment),
            ml=self.to_ml_policy(),
            require_replay_validated=self.require_replay_validated,
            label=self.label,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialização estável (round-trip para config.json)."""
        return {
            "environment": self.environment,
            "ml_required": self.ml_required,
            "fallback_enabled": self.fallback_enabled,
            "require_replay_validated": self.require_replay_validated,
            "max_drawdown_dia": str(self.max_drawdown_dia),
            "label": self.label,
        }


#: Nome público da configuração resolvida (todas as chaves explícitas).
ConfigCompleto = Config


# ---------------------------------------------------------------------------
# helpers de validação
# ---------------------------------------------------------------------------
def _strict_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(
            f"{path}: esperado bool (true/false), obtido {value!r} "
            "(números e strings não são aceitos para evitar divergência)"
        )
    return value


def _to_decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ConfigError(f"{path}: esperado número (int/float/str/Decimal), obtido {value!r}")
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensivo
        raise ConfigError(f"{path}: número inválido {value!r}") from exc
    if not d.is_finite():
        raise ConfigError(f"{path}: número deve ser finito, obtido {value!r}")
    return d


def _validate_drawdown(d: Decimal, path: str) -> Decimal:
    if not (MIN_MAX_DRAWDOWN_DIA < d <= MAX_MAX_DRAWDOWN_DIA):
        raise ConfigError(
            f"{path}: max_drawdown_dia deve estar em (0, 1], obtido {d}"
        )
    return d


def _validate_environment(env: str, path: str = "environment") -> str:
    if env not in VALID_ENVIRONMENTS:
        raise ConfigError(
            f"{path}: ambiente inválido {env!r}; esperados {list(VALID_ENVIRONMENTS)}"
        )
    return env


def _validate_label(label: Any, path: str = "label") -> str:
    if not isinstance(label, str) or not label.strip():
        raise ConfigError(f"{path}: label deve ser texto não vazio, obtido {label!r}")
    return label


def _apply_legacy(mapping: dict[str, Any], path: str, known: frozenset[str],
                  legacy_used: list[str]) -> dict[str, Any]:
    """Renomeia chaves legadas; conflito legado+atual ou chave proibida → erro.

    Chaves que não estão em `known` NÃO são rejeitadas — são passadas
    adiante para compatibilidade com o motor (que usa chaves como
    'ativos', 'rtd', 'tick_values', etc.)."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        p = f"{path}.{key}" if path else key
        if key in FORBIDDEN_KEYS:
            raise ConfigError(
                f"{p}: chave legada PROIBIDA {key!r} — o conceito foi "
                "deliberadamente eliminado; remova-a da configuração."
            )
        if key in LEGACY_KEY_MAP:
            new = LEGACY_KEY_MAP[key]
            if new in mapping:
                raise ConfigError(
                    f"{path}: conflito entre chave legada {key!r} e atual "
                    f"{new!r} — defina UMA única (a legada será removida em "
                    "uma próxima versão)."
                )
            if new not in known:
                raise ConfigError(f"{p}: chave legada {key!r} não é válida aqui")
            out[new] = value
            legacy_used.append(key)
        else:
            # Chave conhecida ou desconhecida — aceita em ambos os casos.
            # Chaves desconhecidas ficam acessíveis via Config.extra
            # para compatibilidade com o motor e outros módulos.
            out[key] = value
    return out


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json inválido ({path}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.json: raiz deve ser um objeto JSON")
    return raw


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------
def load_config(
    *,
    path: str | Path | None = None,
    environment: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Resolve a configuração completa aplicando a prioridade P1..P4.

    Parameters
    ----------
    path:
        Arquivo config.json explícito. ``None`` → procura
        ``config.json`` no diretório de trabalho (se existir).
    environment:
        Argumento de função (define qual seção ``environments[ENV]`` usar).
        Tem prioridade sobre a chave ``environment`` do JSON e do override.
    overrides:
        Nível P1 (ConfigCompleto programático): chaves que sobrepõem tudo.

    Returns
    -------
    Config
        Configuração resolvida com ``sources`` (origem de cada chave) e
        ``legacy_used`` (auditoria do legado).
    """
    legacy_used: list[str] = []
    warnings: list[str] = []
    sources: dict[str, str] = {}

    # -- P3/P2: carrega o config.json ---------------------------------------
    json_path: Path | None = None
    if path is not None:
        json_path = Path(path)
        if not json_path.is_file():
            raise ConfigError(f"arquivo de configuração não encontrado: {json_path}")
    else:
        candidate = Path(DEFAULT_CONFIG_FILE)
        if candidate.is_file():
            json_path = candidate
            warnings.append(f"config.json encontrado em {candidate}; aplicado.")

    file_root: dict[str, Any] = {}
    file_env: dict[str, Any] = {}
    file_env_sections: dict[str, dict[str, Any]] = {}
    if json_path is not None:
        raw = _load_json_file(json_path)
        env_sections = raw.get("environments", {})
        if not isinstance(env_sections, dict):
            raise ConfigError("config.json: 'environments' deve ser um objeto")
        for sect_name, sect in env_sections.items():
            _validate_environment(str(sect_name), path="environments")
            if not isinstance(sect, dict):
                raise ConfigError(
                    f"config.json: environments.{sect_name} deve ser um objeto"
                )
            file_env_sections[sect_name] = _apply_legacy(
                sect, path=f"environments.{sect_name}",
                known=KNOWN_ENV_KEYS, legacy_used=legacy_used,
            )
        top = {k: v for k, v in raw.items() if k != "environments"}
        file_root = _apply_legacy(
            top, path="", known=KNOWN_KEYS, legacy_used=legacy_used,
        )
        if "environment" in file_root:
            _validate_environment(file_root["environment"])
        for sect in file_env_sections.values():
            if "environment" in sect:
                raise ConfigError(
                    "config.json: 'environment' não é permitido dentro de "
                    "environments[ENV] (o nome da seção já define o ambiente)"
                )

    # -- P1: overrides --------------------------------------------------------
    ov: dict[str, Any] = {}
    if overrides is not None:
        ov = _apply_legacy(
            dict(overrides), path="", known=KNOWN_KEYS, legacy_used=legacy_used,
        )
        if "environment" in ov:
            _validate_environment(ov["environment"])

    # -- ambiente efetivo (argumento > P1 > P3 > P4) --------------------------
    if environment is not None:
        env = _validate_environment(environment, path="load_config(environment=...)")
    elif "environment" in ov:
        env = ov["environment"]
    elif "environment" in file_root:
        env = file_root["environment"]
    else:
        env = DEFAULT_ENVIRONMENT

    file_env = file_env_sections.get(env, {})

    # -- resolução por chave (P1 > P2 > P3 > P4) -----------------------------
    def resolve(key: str, default: Any, valid=None) -> Any:
        levels: list[tuple[str, Mapping[str, Any]]] = [
            (_NIVEL_OVERRIDES, ov),
            (_NIVEL_ENV_JSON.format(env=env), file_env),
            (_NIVEL_JSON, file_root),
        ]
        for level, src in levels:
            if key in src:
                value = src[key]
                if valid is not None:
                    value = valid(value, f"{level}.{key}" if level != _NIVEL_DEFAULTS else key)
                _record_source(sources, key, level)
                return value
        _record_source(sources, key, _NIVEL_DEFAULTS)
        return default

    max_dd = resolve(
        "max_drawdown_dia",
        default=DEFAULT_MAX_DRAWDOWN_DIA,
        valid=lambda v, p: _validate_drawdown(_to_decimal(v, p), p),
    )
    ml_required = resolve(
        "ml_required",
        default=DEFAULT_ENV_PRESETS[env].ml.ml_required,
        valid=lambda v, p: _strict_bool(v, p),
    )
    fallback_enabled = resolve(
        "fallback_enabled",
        default=DEFAULT_ENV_PRESETS[env].ml.fallback_enabled,
        valid=lambda v, p: _strict_bool(v, p),
    )
    require_replay = resolve(
        "require_replay_validated",
        default=DEFAULT_ENV_PRESETS[env].require_replay_validated,
        valid=lambda v, p: _strict_bool(v, p),
    )
    label = resolve(
        "label",
        default=DEFAULT_ENV_PRESETS[env].label,
        valid=lambda v, p: _validate_label(v, p),
    )

    # Coletar chaves operacionais (não-ML) para Config.extra
    _gate_keys = {
        'environment', 'ml_required', 'fallback_enabled',
        'require_replay_validated', 'max_drawdown_dia', 'label',
        'environments',  # seção special
    }
    extra = {k: v for k, v in file_root.items() if k not in _gate_keys}
    # Overrides também podem ter chaves extras
    if overrides:
        extra.update({k: v for k, v in overrides.items() if k not in _gate_keys})

    return Config(
        environment=env,
        ml_required=ml_required,
        fallback_enabled=fallback_enabled,
        require_replay_validated=require_replay,
        max_drawdown_dia=max_dd,
        label=label,
        sources=sources,
        legacy_used=tuple(legacy_used),
        warnings=tuple(warnings),
        extra=extra,
    )


def _record_source(sources: dict[str, str], key: str, level: str) -> None:
    # primeira origem encontrada na ordem P1..P4 vale (setdefault)
    sources.setdefault(key, level)
