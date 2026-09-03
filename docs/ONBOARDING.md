# ONBOARDING — Freebuff (Motor de Trading B3)

**Gerado em:** 30/08/2026
**Status dos números deste documento:** medidos nesta data, não copiados de docs antigos.

---

## 1. O que é este projeto

Motor de **trading algorítmico de alta frequência (escala de segundos)** para os contratos
mini da B3:

| Ativo | Contrato | Valor do ponto |
|---|---|---|
| `WIN` | Mini-índice Ibovespa | R$ 0,20 |
| `WDO` | Mini-dólar | R$ 10,00 |
| `IND` | Índice cheio | R$ 1,00 |
| `DOL` | Dólar cheio | R$ 50,00 |

Duas formas de operar:

1. **Ao vivo (Windows)** — conecta na plataforma **Profit** via **RTD/COM** (`win32com`),
   consome o book de ofertas e o times&trades, decide e executa.
2. **Replay (offline)** — relê os JSONL/Parquet capturados e simula a estratégia para
   validar se há edge antes de liberar o motor para operar.

O projeto é escrito em **português** (código, comentários, documentação). Roda em
`C:\Python314\python.exe` — é esse interpretador que tem `lightgbm`, `pyarrow`, `numpy`,
`pandas` e `pytest` instalados.

---

## 2. Pipeline de dados

```
Profit (RTD / COM)
   │
   ▼
adapters/profit_rtd.py ──► MarketEvent(TRADE | BOOK)
   │  rtd_connection.py   conexão e refresh
   │  rtd_parser.py       parse do refresh + horário HMS.ms
   │  rtd_writer.py       grava parquet_part + consolidação
   │  com_watchdog.py     heartbeat do COM em thread separada
   ▼
core/app.py::App._handle_market_event()
   │
   ├─► core/market_state.py       estado de mercado, trackers (OFI, VP, Kyle, BLF, CrossAsset)
   ├─► core/capture_daemon.py     gravação bruta JSONL em thread imortal
   │
   ├─► ml/scorer.py               ScorerML — roda ANTES do signal engine
   │      (a prob do ML tem que ser do evento atual, não do anterior)
   │
   ├─► features/feature_engine.py → dict de features (70 registradas)
   ├─► core/signal_engine.py      scoring heurístico + gate binário do ML + calibração
   │                              → Signal(lado, score, tp, sl, quantidade, valor_ponto)
   ├─► core/risk_engine.py        16 proteções → RiskDecision
   ├─► core/position_manager.py   abre/fecha, TP/SL/trailing, cooldown, piramidação → Action
   └─► core/persistence.py        JSONL de trades e decisões + checkpoint de posição
                                  core/decision_journal.py registra o audit trail
```

Saídas laterais: `adapters/dashboard_server.py` (HTTP :5001), `core/metrics.py`,
`core/observability.py`, `watchdog.py` (reinicia o motor se ele morrer).

**Replay gate:** em `App.__init__`, `_verificar_replay_gate()` decide se o motor opera ou
entra em **modo captura pura** (grava dados, não tradeia). Se não aprovado, o loop retorna
antes de calcular sinal — de propósito, para não gastar CPU à toa.

---

## 3. Mapa de módulos

### `core/` — orquestração e decisão

| Arquivo | Linhas | Papel |
|---|---|---|
| `app.py` | 782 | Orquestrador: loop RTD, roteamento TRADE/BOOK, replay gate |
| `market_state.py` | 676 | Estado de mercado + trackers de microestrutura |
| `risk_engine.py` | 675 | Risk Engine v2 — 16 proteções, fonte de verdade do risco |
| `decision_journal.py` | 571 | Audit trail de cada decisão (`TradeDecision`) |
| `calibration.py` | 493 | Calibração de probabilidade por regime, com feedback ao vivo |
| `signal_engine.py` | 484 | Features → sinal (`lado`, `score`, `confianca`, TP/SL) |
| `capture_daemon.py` | 441 | Thread imortal de captura bruta JSONL |
| `position_manager.py` | 341 | Abertura/fechamento, TP/SL, trailing, cooldown |
| `contracts.py` | 273 | Dataclasses congeladas: `MarketEvent`, `TradeEvent`, `BookSnapshot`, `Signal` |
| `event_ordering.py` | 267 | Detecta atraso, fora de ordem, duplicado, salto, sequência regressiva |
| `risk_manager.py` | 215 | Gatekeeper **legado** — duplica o RiskEngine (ver §8) |
| `observability.py` | 351 | Coleta de métricas (FASE 19) — existe, **não está integrado** |
| `temporal.py` | 184 | Política de timestamps (contrato triplo: mercado / recebimento / processamento) |

### `features/` — 70 features, todas causais
`trade_features` (18), `institutional_context` (19), `book_features` (15), `volume_profile` (4),
`ofi`, `kyle_lambda`, `vpin`, `cross_asset` (3), `returns`, `volatility`, `volume_relativo`,
`poc_migration`, `session_time`, `vwap`. Catálogo completo em `FEATURE_REGISTRY.md`.

### `ml/` — pipeline de aprendizado
`dataset_builder` → `labeler_*` → `treino_lib` → `scorer` (inferência) →
`walk_forward` / `validacao_rigorosa` / `ablation_test` (validação) →
`feature_manifest` (paridade treino↔produção) → `model_registry` / `model_metadata`.

### Pacotes de gate (pequenos e isolados de propósito)
- `exposure/` — `E = N·P·V` (exposição nominal), `R = d_stop·N·V` (risco no stop), agregação bruta/líquida.
- `mlgate/` — `MlAvailability`, `MlGatePolicy`, `evaluate_gate`, `MLDecisionLog`.
- `replaygate/` — `ReplayStatus`, `Environment`, `EnvironmentPolicy`, `evaluate_replay_gate`.
- `config/` — fonte única de configuração (`defaults.py` é a **única** fonte de verdade).

---

## 4. Gates — a parte que o projeto leva mais a sério

O princípio central é **não-silêncio**: o sistema nunca deve operar "como se nada
tivesse acontecido" quando uma dependência falha.

### Políticas por ambiente

| Ambiente | `ml_required` | `fallback_enabled` | `require_replay_validated` |
|---|---|---|---|
| `DEVELOPMENT` | `False` | `True` | `False` |
| `PAPER` | `True` | `False` | `False` |
| `PRODUCTION` | `True` | `False` | **`True`** |

Em PRODUCTION, ML fora do ar ⇒ bloqueio. Replay não validado ⇒ bloqueio **antes** do gate de ML.

### Critérios do replay gate
`PF > 1.2`, `win_rate > 45%`, `max_drawdown > -200 pts`, `n_trades >= 3`.
Em modo `validacao` (padrão 3 dias), **todos** os dias precisam passar.

### Prioridade de configuração (P1 → P4)
```
P1  overrides programáticos (load_config(overrides=...))
P2  config.json → environments[ENV]
P3  config.json → raiz
P4  config/defaults.py
```
Chave desconhecida ou chave legada em conflito ⇒ `ConfigError`. Divergência nunca é silenciosa.

---

## 5. Replay e validação

- `replay_engine.py` (v11) e `replay_engine_v13.py` (v13, o que importa).
- Modos: `paper` (1 dia) e `validacao` (N dias, go/no-go).
- **FASE 17** tornou a execução realista: latência simulada, spread variável por
  volatilidade, slippage proporcional ao tamanho, execução parcial, rejeição por
  circuit breaker e spread excessivo, stop intrabar, prioridade de fila.
- Resultado em `replay_resultado.json`, que o replay gate consome.

---

## 6. Linhagem de fases

| Fase | Conteúdo |
|---|---|
| 1–4 | Auditoria cirúrgica: dedup T&T, timestamp de mercado, ordenamento temporal, overflow |
| 7 | `exposure/` — correção de `exposure_atual = TP + SL` para `E = N·P·V` |
| 8 | `mlgate/` — política de ML configurável |
| 9 | `replaygate/` — replay obrigatório por ambiente |
| 10 | `config/` — fonte única de configuração |
| 13–15 | Paridade treino↔produção, causalidade E2E, paridade de book |
| 16 | Classificação PRODUCTION / EXPERIMENTAL / LEGACY |
| 17 | Replay realista |
| 18 | Testes de estresse (degradação graciosa) |
| 19 | `core/observability.py` — watchdog e métricas |
| 20 | `core/decision_journal.py` — audit trail |

---

## 7. Estado real dos testes (medido em 31/08/2026)

> Os documentos do projeto afirmam "154 passed, 0 failed" e "205 testes passando".
> **Isso não é mais verdade.** Os números abaixo são os medidos agora.

```
pytest tests/    →  342 passed,  26 failed                        (~10s)
pytest testes/   →  435 passed,  24 failed, 4 skipped, 4 xfailed  (~55s)
```

### `tests/` — 26 falhas (estável, sem regressão desde 30/08)

| Arquivo | Falhas | Causa raiz |
|---|---|---|
| `test_position.py` | 10 | `exposure/` divergente da especificação (§8.2) |
| `test_formulas.py` | 5 | idem |
| `test_aggregate.py` | 4 | idem |
| `test_config.py` | 3 | `config.json` do repo sobrepõe defaults (§8.3) |
| `test_invariants.py` | 2 | `exposure/` |
| `test_audit.py` | 2 | `exposure/` |

`test_decision_journal.py` (57 testes) verde desde 30/08/2026 — ver §8.1.

### `testes/` — suíte destravada em 31/08/2026

Até 30/08 os 456 testes estavam **mortos**: 3 erros de coleta, 0 executados.
Os imports órfãos de `motor_web` / `captura_eventos_ms` foram reapontados para os
módulos vivos — detalhes em §8.8.

| Arquivo | Falhas | Observação |
|---|---|---|
| `test_risk_unification.py` | 9 | reproduz isolado |
| `test_edge_case_book_split.py` | 6 | **todos** os testes do arquivo falham |
| `test_config_flat.py` | 5 | **todos** falham |
| `test_book_split_edge_cases.py` | 3 | **todos** falham |
| `test_features.py` | 1–6 | sensível a lixo em disco (§8.9) |
| `test_edge_case_scorer.py` | 1 | reproduz isolado |
| `test_capture_overflow.py` | 0–1 | só falha em suíte; isolado passa 19/19 |

**A contagem total varia entre 24 e 31** em execuções idênticas — ver §8.9.

---

## 8. Problemas encontrados — priorizados

### 8.1 `[CORRIGIDO em 30/08/2026]` `confianca` sobrescrevia `score` no audit trail
`core/decision_journal.py`

```python
if hasattr(self, 'confianca'):          # sempre True — confianca tem default 0.0
    object.__setattr__(self, "score", float(self.confianca))
```
`confianca` (EWMA) tem default `0.0`, então `hasattr` era **sempre** `True` e o `score`
do ML era destruído: toda decisão era gravada com `score = 0`, anulando justamente a
razão de existir do decision journal.

**Correção:** `confianca` só preenche `score` quando ele não foi informado.
São conceitos diferentes e não devem se sobrescrever.

Na mesma linha, o journal não expunha a API que os consumidores usavam. Foram
adicionados (puramente aditivos, sem alterar os existentes):

| Método | Consumidor |
|---|---|
| `registrar(entry)` | `core/app.py:481`, `core/signal_engine.py:423` |
| `count()` | `run_all_tests.py` |
| `buscar(id=..., ativo=...)` | `run_all_tests.py`, `adapters/dashboard/handlers.py` |
| `listar(limite=..., ativo=...)` | `adapters/dashboard/handlers.py` |
| `resumo()` | `run_all_tests.py` |
| campo `TradeDecision.id` | todos acima |

Também corrigido: `DecisionJournal(save_dir, session_ts)` caía nos campos `entries` e
`_lock` do dataclass, deixando `entries` como `str`. `save_dir`/`session_ts` passaram a
ser os dois primeiros campos.

**Atenção ao registrar timestamps:** os campos do journal são em **segundos** (unix),
mas o resto do sistema trabalha em **milissegundos**. Passar `trade.timestamp_ms` sem
dividir por 1000 faz `explain_decision()` estourar `OSError`.

### 8.2 `[ALTO]` `exposure/` é um stub que não implementa a especificação
Criado como shim de compatibilidade, o pacote diverge do contrato que os testes esperam:
- `Direction` é uma classe de constantes string (`Direction.WIN = "WIN"`), sem `.sign` (+1/-1).
- Não existe `AggregateResult` com campo `.metrics`.
- `portfolio.py`, `direction.py` e `formulas.py` são re-exports finos.

Responsável por 23 das 39 falhas. `docs/FORMULAS.md` descreve a especificação correta.

### 8.3 `[MÉDIO-ALTO]` `config.json` do repo neutraliza o ambiente pedido
`load_config(environment="PRODUCTION")` sem `path=` descobre `./config.json` na raiz e
deixa as chaves **P3** (`ml_required: false`, `require_replay_validated: false`) sobreporem
os defaults do ambiente. Resultado: pede-se PRODUÇÃO e recebe-se política de
desenvolvimento, silenciosamente.

É o tipo exato de divergência silenciosa que o projeto se propõe a eliminar — e com
consequência prática: em produção, desligaria `ml_required` e `require_replay_validated`.

### 8.4 `[MÉDIO]` Documento com valor de ponto errado por 100x
`docs/RISK_GOVERNANCE.md` afirma WDO = R$ 0,10 e DOL = R$ 0,10.
O correto (em `core/signal_engine.py::_get_valor_ponto`) é WDO = R$ 10,00 e DOL = R$ 50,00.
Quem implementar exposição lendo o doc errará o limite em duas ordens de grandeza.

### 8.5 `[MÉDIO]` Migração de risco planejada e nunca executada
`docs/RISK_GOVERNANCE.md` define 4 passos para unificar `RiskManager` → `RiskEngine`.
Hoje 8 regras estão duplicadas entre os dois e 3 também no `PositionManager`, que
ainda chama `self.risk.pode_abrir()` como fallback. `docs/OPEN_QUESTIONS.md` confirma:
"Migração RiskManager: planejada mas não executada".

### 8.6 `[BAIXO]` Higiene do repositório
- `MOTORCLAUDE.ZIP` (7 MB) solto na raiz.
- `.pyc` versionados no git (geram dezenas de entradas sujas no `git status`).
- Arquivos duplicados: `" - Copia.gitignore"`, `".watchdog - Copia.lock"`.
- ~62 arquivos modificados não commitados.
- `docs/FASE16_LEGACY分类.md` — nome com caracteres corrompidos.

### 8.7 `[BAIXO]` Documentação desatualizada
`README.md` descreve apenas `exposure/` + `mlgate/` + `replaygate/` (como se o projeto
fosse uma biblioteca de fórmulas), ignorando todo o motor de trading.
`docs/ESTADO_ATUAL.md` diz "v10.6" e "154 passed, 0 failed"; o `pyproject.toml` diz
versão 15.0.0. Nenhum dos dois reflete a realidade.

### 8.8 `[CORRIGIDO em 31/08/2026]` Suíte `testes/` morta por imports órfãos

Os 456 testes de `testes/` não executavam: 3 erros de coleta por
`ModuleNotFoundError: motor_web`. `motor_web.py` foi decomposto na refatoração v10.1
e só sobrevive em `docs/archive/motor_web_legacy.py`.

Reapontamentos feitos:

| Teste | De | Para |
|---|---|---|
| `test_book_writer.py` | `motor_web` | `adapters.rtd_writer` |
| `test_com_watchdog.py` | `motor_web` | `adapters.com_watchdog` |
| `test_tt_warmup.py` | `motor_web` | `adapters.rtd_connection` |
| `test_features.py` (3x, lazy) | `captura_eventos_ms` | `adapters.file_storage` |

**Armadilha:** um shim `from adapters.rtd_writer import *` **não funciona**. Os testes
monkeypatcheiam atributos no objeto módulo (`mw.write_parquet_part = ...`) e
`thread_escritora` resolve esses nomes nos seus **próprios globals**. O alias tem de
apontar para o módulo real, não para um re-export.

Dois bugs colaterais descobertos no processo:

- `test_book_writer` usava `time_ms=1724000000000` (2024). `_validar_timestamp_ms`
  (v11.2) rejeita timestamps com mais de 300s de atraso → a linha era descartada antes
  do buffer e o writer nunca gravava. Passou a usar `int(time.time()*1000)`.
- `rtd_writer` usa `log = logging.getLogger(__name__)`; o `motor_web` antigo chamava
  `logger`. `monkeypatch.setattr(mw, 'logger', ...)` falhava.

**4 testes continuam órfãos** (marcados `xfail` com motivo, não apagados): 1 em
`test_com_watchdog.py` e 3 em `test_tt_warmup.py`. Todos dependem de
`_thread_com_ciclo` (loop COM com filas), que **não tem sucessor** no código vivo.

Atenção: **a lógica que eles testavam NÃO foi perdida.** O fix R1 (warmup/baseline/dedup
de T&T) sobreviveu em `ProfitRTDAdapter.events()` — `adapters/profit_rtd.py`, linhas
181-222 e 297. Os testes precisam ser **reescritos** contra o adapter, não restaurados.

Ferramenta nova: `scripts/check_test_imports.py` — varredura AST que acha imports sem
resolução **dentro de funções**, que a coleta do pytest não pega. Hoje reporta zero.

### 8.9 `[MÉDIO]` A suíte grava no diretório de código e a contagem de falhas oscila

Execuções idênticas de `pytest testes/` já deram **24, 27, 28, 29, 30 e 31 falhas**.
Causas identificadas:

1. `test_dia_saudavel` e `test_dia_sem_arquivos` usam a **mesma chave de data**
   (`20990101`), e ambos gravam em `testes/` (o diretório de código), não em `tmp_path`.
   Um `raw_negocios_ms_20990101_TESTEVAL.jsonl` deixado para trás faz
   `test_dia_sem_arquivos` falhar na execução seguinte.
2. `test_dedup_aceita_duplicatas`, `test_rotacao_por_tamanho` e `test_meta_sessao`
   também gravam em `testes/` e já deixaram 30 arquivos `raw_*_TESTE_*` para trás.
3. `test_capture_overflow.py` passa 19/19 isolado e falha só em suíte.

Correção recomendada: migrar esses testes para a fixture `tmp_path` e dar chaves de data
únicas por teste. Enquanto isso, **limpar `testes/raw_*` antes de comparar medições.**

---

## 9. Convenções a respeitar

1. **Não-silêncio** — nunca esconder ML indisponível ou replay não validado.
2. **Causalidade** — feature nenhuma usa o futuro; PURGE/EMBARGO no dataset.
3. **Paridade treino ↔ produção** — `ml/feature_manifest.py` falha seguro se faltar feature.
4. **Honestidade de métricas** — PF/P&L fake de treino foram removidos de propósito
   (commits "ML pipeline honesto — sem PF fake"). Não reintroduzir.
5. **Dinheiro em `Decimal`** — exposição `E = N·P·V`, risco `R = d_stop·N·V`.
6. **Português** nos identificadores de domínio e em toda a documentação.
7. Código de produção (`adapters/`, `core/`, `features/`, `ml/`) **não** importa de
   `docs/archive/` nem de `scripts/experimental/` (regra da FASE 16).

---

## 10. Como rodar

```bash
# interpretador com as dependências
PY="C:/Python314/python.exe"

# suíte de testes (a que o pyproject declara)
$PY -m pytest tests -q

# suíte legado (ignorando os 3 módulos que importam motor_web)
$PY -m pytest testes -q \
  --ignore=testes/test_book_writer.py \
  --ignore=testes/test_com_watchdog.py \
  --ignore=testes/test_tt_warmup.py

# motor ao vivo (Windows + Profit aberto)
python run_motor.py                 # ou: python run_motor.py WINV26 WDOU26

# replay de 1 dia
python replay_engine_v13.py --modo paper --dia 2026-08-28

# validação go/no-go de 3 dias
python replay_engine_v13.py --modo validacao --dias 3

# verificação de integração ponta a ponta
python check_integration.py
```

`config.json` atual está com `ml_modelo: ""` — o motor sobe **sem ML**, operando apenas
com a heurística. Em `DEVELOPMENT` isso é permitido; em `PRODUCTION` o gate bloquearia.
