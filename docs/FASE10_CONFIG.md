# FASE 10 — P1 · Configuração (fonte única, prioridade, legado)

## 1. Problema corrigido

Configurações espalhadas em múltiplos lugares (`mlgate`, `replaygate`,
documentos, scripts) geravam **divergência** e **silêncio sobre fontes**.
Sem matriz de prioridade, era impossível saber qual valor valia em
qual contexto. O problema mais crítico: `max_drawdown_dia` podia estar
definido em dois lugares — um deles silenciosamente ignorado.

## 2. Fontes mapeadas (inventário pré-implementação)

| # | Fonte | Localização | Situação |
|---|---|---|---|
| 1 | `config.json` | — | **não existia** (fonte declarada) |
| 2 | Defaults implícitos | `mlgate/__init__.py` (2 presets) + `replaygate/__init__.py` (3 presets) | **duplicados** — valores repetidos em 2 módulos |
| 3 | `ConfigCompleto` | — | **não existia** (estrutura criada) |
| 4 | Constantes internas | `DECISION_SOURCE_*`, `Environment`, presets | OK (são tipos, não config) |
| 5 | Argumentos de função | `evaluate_gate(policy=...)`, `evaluate_replay_gate(policy=...)` | política já resolvida chega como argumento — sem nova fonte |
| 6 | Legado | nenhum existente no código atual | **mapeamento preventivo** (chaves que podem aparecer em arquivos antigos) |

Conclusão: **fontes 2 e 3 estavam duplicadas**; todas as outras eram inexistentes ou triviais. A solução: **uma única fonte de verdade para todos os defaults**.

## 3. Prioridade resolvida (P1 > P2 > P3 > P4)

```text
P1  ConfigCompleto / overrides         load_config(overrides={"ml_required": False})
P2  config.json:environments[ENV]      {"environments":{"PRODUCTION":{"ml_required":true}}}
P3  config.json (raiz)                 {"ml_required": true}
P4  config.defaults                    DEFAULT_MAX_DRAWDOWN_DIA, DEFAULT_ENV_PRESETS[...]
```

A ordem de resolução é fixa e **auditável**: cada chave em
`Config.sources` carrega sua origem (`"config_completo"`,
`"config.json:environments[PRODUCTION]"`, `"config.json"`,
`"defaults"`).

## 4. Matriz PARÂMETRO → origem → padrão → consumidor → prioridade

| Parâmetro | Origem única | Valor padrão | Consumidor principal | Prioridade (quando sobrepõe) |
|---|---|---|---|---|
| `max_drawdown_dia` | `config.defaults.DEFAULT_MAX_DRAWDOWN_DIA` | `Decimal("0.02")` | risco (futuro), auditoria, gate de drawdown | P1>P2>P3>P4 |
| `environment` | `config.defaults.DEFAULT_ENVIRONMENT` | `"DEVELOPMENT"` | seleção de seção config.json | argumento > P1>P3>P4 |
| `ml_required` | `config.defaults.DEFAULT_ENV_PRESETS[ENV].ml.ml_required` | varía por ambiente | `mlgate.evaluate_gate(policy=...)` | P1>P2>P3>P4 |
| `fallback_enabled` | `config.defaults.DEFAULT_ENV_PRESETS[ENV].ml.fallback_enabled` | varía por ambiente | `mlgate.evaluate_gate(policy=...)` | P1>P2>P3>P4 |
| `require_replay_validated` | `config.defaults.DEFAULT_ENV_PRESETS[ENV].require_replay_validated` | varía por ambiente | `replaygate.evaluate_replay_gate(policy=...)` | P1>P2>P3>P4 |
| `label` | `config.defaults.DEFAULT_ENV_PRESETS[ENV].label` | varía por ambiente | auditoria/telemetria | P1>P2>P3>P4 |

## 5. Estrutura do pacote (`config/`)

```text
config/
  __init__.py    # re-exporta tudo para acesso unificado
  errors.py      # ConfigError (nunca se esconde divergência)
  defaults.py    # ÚNICA fonte de verdade (sem importar mlgate/replaygate)
  loader.py      # load_config(prioridade P1..P4, validação, legacy map)
```

DAG de importação (sem ciclos):
```
config.defaults  ←  mlgate  ←  replaygate  ←  config.loader
```

## 6. Compatibilidade legado

| Chave antiga | Chave atual | Ação |
|---|---|---|
| `drawdown_max_dia` | `max_drawdown_dia` | renomeado + registrado em `legacy_used` |
| `ml_obrigatorio` | `ml_required` | renomeado + registrado em `legacy_used` |
| `usar_fallback` | `fallback_enabled` | renomeado + registrado em `legacy_used` |
| `exigir_replay` | `require_replay_validated` | renomeado + registrado em `legacy_used` |

**Chaves proibidas** (conceitos eliminados, nunca aceitas):
- `exposure_atual` (FASE 7 P1: TP+SL foi redefinido como exposição)
- `ml_fallback_silencioso` / `fallback_silencioso` / `silencioso`
  (FASE 8/9: silêncio proibido)

Conflito legado+atual (`drawdown_max_dia` + `max_drawdown_dia` no mesmo
arquivo) gera `ConfigError` explícito — nunca se escolhe em silêncio.

## 7. Chaves legadas rejeitadas (sempre)

`FORBIDDEN_KEYS`: `exposure_atual`, `ml_fallback_silencioso`,
`fallback_silencioso`, `silencioso`. Nenhum desses conceitos pode
retornar via config — são incompatíveis com as definições das FASES 7/8/9.

## 8. Regras de validação

- `bool` **estrito** (1/0/"true" rejeitados — evitar conversões
  silenciosas que geram divergência entre ambientes)
- `max_drawdown_dia` em `(0, 1]` (fração válida de equity)
- `environment` em `{"DEVELOPMENT","PAPER","PRODUCTION"}`
- `label` texto não vazio
- `config.json` raiz deve ser objeto; `environments` deve ser objeto de objetos

## 9. Testes (`tests/test_config.py` — 22 testes)

| Grupo | O que cobre |
|---|---|
| `TestDefaultSourceUniqueness` | `max_drawdown_dia` só existe em `config.defaults`; mlgate e replaygate derivam sem duplicar |
| `TestPriorityLevels` | Overrides > config.json:environments > config.json:raiz > defaults; ambiente por argumento; default quando não há JSON |
| `TestLegacyCompatibility` | Renomeio funciona; `legacy_used` registra; conflito legado+atual gera erro; chave proibida gera erro; chave desconhecida gera erro |
| `TestValidation` | Bool estrito; drawdown 0 e >1 rejeitados; drawdown=1 aceito; ambiente inválido rejeitado; label vazio rejeitado; flag legado não-bool rejeitado |
| `TestConfigCompletoProjection` | `to_ml_policy()` alinha com `PRODUCTION_POLICY`; `to_env_policy()` alinha com `PRODUCTION_ENV_POLICY`; `as_dict()` round-trip |
| `TestIntegrationWithGates` | Produção com replay pendente → bloqueado; dev com replay pendente → operou (informativo) |
| `TestBackwardCompatibility` | Valores de presets FASE 8/9 intactos |

Execução:

```bash
pytest -v
```

Suíte completa: **112 FASE 7/8/9 + 22 FASE 10 = 134 testes passando**.