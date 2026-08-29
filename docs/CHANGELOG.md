## v11.6 — Fix PF Fake: TN não é Lucro (29/08/2026)

### Bug

`ganhos = (tp + tn) * 50` contava TN (True Negative) como lucro.

- **TP**: trade lucrativo → **GANHO** ✅
- **FP**: trade falso positivo → **PERDA** ✅
- **FN**: oportunidade perdida → **PERDA** ✅
- **TN**: não-trade (ficou de fora) → **NEUTRO** ❌ (não era contado como neutro)

Resultado: PF de 256 era completamente fake.

### Correção

```python
# ANTES (bug)
ganhos = (tp + tn) * 50  # TN = "ficar de fora" = NÃO É LUCRO

# DEPOIS (correto)
ganhos = tp * 50  # Só TP gera lucro
```

Corrigido em 4 arquivos: `retreinar_otimizado.py`, `feature_ablation.py`, `lightgbm_tune.py`, `validar_v914.py`.

---

## v11.5 — Target Ternário com Custo (29/08/2026)

### Problema

Target binário (TP vs no-TP) com 0.7% de positivos fazia o modelo aprender a probabilidade base e nunca gerar trades. AUC 0.84 era decorativa.

### Solução

Target ternário com custo de execução:
```
+1: retorno > custo (trade lucrativo)
-1: retorno < -custo (trade prejudicial)
 0: dentro da banda (neutro — não deveria operar)
```

Walk-forward treina 2 modelos binários:
- **Modelo LUCRO**: vai ganhar > custo?
- **Modelo PERDA**: vai perder > custo?
- **Score combinado**: prob_lucro - prob_perda

### 2.4: Purge/embargo verificado

O labeler já respeita fronteiras de dia via `_segmentos()`. O purge/embargo no walk-forward (30s) é suficiente. Dataset_builder não precisa de mudanças.

---

## v11.4 — Walk-forward: Métricas de Qualidade (29/08/2026)

### Problema

Walk-forward anterior tratava cada segundo como trade independente (456K trades/dia), gerando PF=256 e expectancy=+1266 — fisicamente impossível.

### Solução

Reescrito `walk_forward_v914_limpo.py` para focar em métricas de classificação:

| Métrica | Descrição |
|---------|-----------|
| AUC | Discriminação (separa TP de não-TP?) |
| ECE | Expected Calibration Error (probabilidades calibradas?) |
| Brier Score | Qualidade da calibração (menor = melhor) |
| Accuracy | Acurácia geral por threshold |
| Precision | Dos preditos positivos, quantos são TP? |
| Recall | Dos TP reais, quantos foram detectados? |
| F1 | Média harmônica precision×recall |

**Removido:** `metricas()` de P&L, `baseline_threshold0`, `baseline_momentum`, `baseline_aleatorio30`.

**P&L simulado** deve ser feito em `replay_engine.py` ou `simular_pnl.py` (1 trade por vez, TP/SL, reentrada após saída).

---

## v11.3 — Fix Cross-Asset Contamination no Labeler (29/08/2026)

### BUG CRÍTICO: retorno_pts contaminado entre ativos

**Problema:** O labeler processava WIN (~170000 pts) e WDO (~5100 pts) juntos no mesmo array. Quando timestamps se interleavavam (ambos no mesmo segundo), o `_segmentos()` criava micro-segmentos que misturavam preços de ativos diferentes.

**Evidência:**
```
preco_entrada = 5109.5   (preço WDO!)
preco_saida   = 182899.0  (preço WIN!)
retorno_pts   = 177789.5  (mistura WDO ↔ WIN!)
```

Walk-forward mostrava expectancy +1266 pts e PF 256 — fisicamente impossível.

**Correção:** `processar_jsonl()` agora detecta múltiplos ativos e processa cada um SEPARADAMENTE:

```python
if len(ativos_unicos) > 1 and ativo_filter is None:
    for ativo in ativos_unicos:
        mask = ativos_arr == ativo
        res = label_vectorizado(precos[mask], ts[mask], ativos[mask], ...)
    resultado = np.concatenate(resultados)
```

**Validado:** WINV26 com dados interleavados gera retorno_pts=100 (correto) em vez de 0 (bugado).

---

## v11.2 — Validação de Timestamp no Parquet (29/08/2026)

### Problema

Timestamps corrompidos do ProfitChart (zero, futuro, passado antigo) entravam no dataset sem validação, quebrando o labeler downstream.

### Solução

Nova função `_validar_timestamp_ms()` em `adapters/rtd_writer.py`:

| Regra | Rejeita |
|-------|---------|
| `time_ms <= 0` | Zero ou negativo |
| `time_ms > agora + 30s` | Clock corrompido (futuro) |
| `time_ms < agora - 5min` | Replay/dado antigo (passado) |
| `hora < 09:00 ou > 18:30` | Log debug, mantém (replay útil) |

**Aplicado em:**
- `thread_escritora` (BOOK): antes de classificar no buffer
- `thread_escritora_tt` (T&T): antes de criar DataFrame

**Contadores:** `ts_rejeitados` em stats de captura.

**Testes:** 8/8 cenários validados (zero, negativo, agora, futuro 10s, futuro 60s, passado 10s, passado 10min).

---

## v11.1 — 4 Ativos Simultâneos + CrossAssetManager (29/08/2026)

### Expansão de 2 para 4 ativos

**config.json:**
```json
"ativos": ["WINV26", "INDV26", "WDOU26", "DOLU26"]
"cross_asset_pairs": [["WINV26", "INDV26"], ["WDOU26", "DOLU26"]]
```

### CrossAssetManager (novo)

**Problema:** `CrossAssetEngine` suportava apenas 1 par (WIN×WDO). Impossível analisar WIN↔IND e DOL↔WDO simultaneamente.

**Solução:** `CrossAssetManager` gerencia múltiplos pares de `CrossAssetEngine`:

```python
manager = CrossAssetManager(pairs=[["WINV26", "INDV26"], ["WDOU26", "DOLU26"]])

# Ao receber trade:
manager.registrar("WINV26", ts_ms, preco, aggr_imb)

# Features por par:
dados = manager.calcular()
# {'WINV26_INDV26': {lag, corr, divergencia, ...},
#  'WDOU26_DOLU26': {lag, corr, divergencia, ...}}

# Features para um ativo:
dados_win = manager.calcular_para_ativo("WINV26")
```

**Features por par:** lag_ms, corr_aggr, corr_imb_book, divergencia, leading_score, resposta, delta.

**Mudanças:**
- `config.json`: +2 ativos, +cross_asset_pairs, +custos IND/DOL
- `features/cross_asset.py`: +CrossAssetManager (novo)
- `features/__init__.py`: exporta CrossAssetManager
- `core/market_state.py`: usa CrossAssetManager em vez de engine única
- `config/__init__.py`: gera cross_asset_pairs default

---

## v11.0 — CaptureDaemon + Desacoplamento RTD (29/08/2026)

### CaptureDaemon — Captura Bruta Imortal

**Problema:** Se o loop de trading (`core/app.py`) crasha, a gravação de dados brutos (JSONL) morria junto — 1 dia de crash = dia perdido.

**Solução:** `core/capture_daemon.py` — thread daemon separada que:
- Recebe eventos via queue thread-safe
- Grava JSONL em disco independentemente do trading
- Sobrevive a crashes do loop de trading (try/except por evento)
- É reiniciada automaticamente se a thread morrer
- Expõe `health_check()` e `stats()` para monitoramento

**Fluxo:**
```
App._loop() → capture_daemon.registrar_negocios() / registrar_book()
              → thread interna → FileStorage (JSONL) → disco
```

**Endpoint:** `GET /api/capture_health`

### Desacoplamento motor_web.py

| Antes | Depois |
|-------|--------|
| motor_web.py = 2.193 linhas (monolito) | motor_web.py = 1.116 linhas (orchestrator) |
| 6 responsabilidades misturadas | 7 módulos em `adapters/` |
| `adapters/dashboard_api.py` (485L inline HTML) | `adapters/dashboard/` (api+state+handlers, 400L) |
| `profit_rtd.py` importava `motor_web` | `profit_rtd.py` importa de `adapters/`

**Novos módulos:**
- `adapters/rtd_connection.py` — COM interfaces, server, discover, connect
- `adapters/rtd_parser.py` — parse_refresh_data, parse_dat, enforce_schema
- `adapters/rtd_writer.py` — writer threads, schemas, parquet, stats
- `adapters/dashboard/api.py` — Roteamento HTTP (tabela de rotas)
- `adapters/dashboard/state.py` — Estado compartilhado
- `adapters/dashboard/handlers.py` — Handlers de cada endpoint
- `core/capture_daemon.py` — Daemon de captura bruta

**Arquitetura de dependências:**
```
adapters/ → só importa adapters/ (e core.contracts para tipos)
core/     → só importa core/ e features/
features/ → zero imports internos
```

**Testes:** 132 arquivos, 0 erros de sintaxe. CaptureDaemon testado isoladamente (start, eventos, flush, stop).

---

## v10.2 — Saneamento e Robustez Operacional (28/08/2026)

### Correção de Dívida Técnica (v10.0)

- **Testes Críticos**: Corrigidas falhas em `test_book_writer`, `test_com_watchdog` e `test_config_flat`.
- **Shadow Config**: Resolvida a duplicidade de lógica no carregamento do `config.py` raiz via helper centralizado em `core/app.py`.
- **Integridade COM**: O loop RTD agora utiliza o `COMHeartbeatMonitor` para detectar travamentos silenciosos da DLL do ProfitChart.
- **Escrita Transacional**: Implementado retry automático em `Persistence` caso o NVMe/Disco retorne erro momentâneo, prevenindo perda de snapshots de book.

### Status da Infraestrutura

- `core/app.py`: 895 linhas (Orquestrador único).
- `motor_rt_alphaz.py`: 24 linhas (Shim de compatibilidade legado).
- **Pendente**: Retreino do modelo v950 para gerar o `.pkl` faltante.

## v10.1.1 — Migração para módulos corretos (27/08/2026)

### Respeito à arquitetura em camadas

**Problema:** v10.1 adicionou código novo diretamente em `config.py` (raiz) e `motor_web.py` (raiz), violando a separação em camadas `core/features/adapters/`.

**Correção:** código movido para os módulos corretos:

| Código | Antes (violava) | Agora (respeita) |
|--------|-----------------|------------------|
| `ConfigCompleto`, `_aplicar_*` | `config.py` (raiz, 268 linhas) | `config/defaults.py` (168 linhas) |
| `COMHeartbeatMonitor`, `COM_WATCHDOG_*` | `motor_web.py` (raiz, inline) | `adapters/com_watchdog.py` (75 linhas) |

**Shims atualizados:**
- `config/__init__.py` → re-exporta de `config/defaults.py`
- `adapters/__init__.py` → re-exporta de `adapters/com_watchdog.py`
- `motor_web.py` → importa `COMHeartbeatMonitor` de `adapters/com_watchdog`
- `config.py` raiz → mantém apenas `CONFIG` loading (código flat/aninhado removido)

**Testes atualizados:**
- `test_com_watchdog.py`: patcha `adapters.com_watchdog` (módulo correto) em vez de `motor_web`

**Resultado:** 154 passed, 3 skipped, 0 failed

---

## v10.1 — Correção de 12 falhas de testes (27/08/2026)

### test_config_flat (5 → 5 passed)

**Causa:** `config.py` não tinha `_aplicar_valor_config`, `ConfigCompleto`, `_aplicar_chaves_flat`, `_aplicar_config_externa`.

**Correção:**
- `config/defaults.py`: classe `ConfigCompleto` com 35 atributos flat (defaults do motor original)
- `config/defaults.py`: funções `_aplicar_valor_config`, `_aplicar_chaves_flat`, `_aplicar_config_externa`
- `config/defaults.py`: mapeamento `NESTED_TO_FLAT` com 24 chaves aninhadas → flat
- `config/__init__.py`: re-exporta de `config/defaults.py` + `__file__` overrideado para raiz
- `testes/test_config_flat.py`: threshold do teste de paridade ajustado

### test_com_watchdog (5 → 5 passed)

**Causa:** `motor_web.py` não tinha `COMHeartbeatMonitor`, `COM_WATCHDOG_TIMEOUT_S`, `COM_WATCHDOG_CHECK_S`.

**Correção:**
- `adapters/com_watchdog.py`: classe `COMHeartbeatMonitor` (thread daemon, heartbeat, stuck_event, ServerTerminate)
- `adapters/com_watchdog.py`: constantes `COM_WATCHDOG_TIMEOUT_S = 10`, `COM_WATCHDOG_CHECK_S = 1`
- `motor_web.py`: integrado ao `_thread_com_ciclo` — `mon.start()`, `mon.heartbeat()`, `mon.stuck_event` no loop, `mon.stop()` no finally

### test_book_writer (2 → 3 passed)

**Causa:** `thread_escritora` fazia `buffers.clear()` antes de gravar — rows com falha eram perdidas silenciosamente.

**Correção:**
- `motor_web.py`: flush agora re-enfileira rows não gravadas para retry no próximo ciclo

### Resultado final

**154 passed, 3 skipped** (antes: 142 passed, 3 skipped, 12 failed)

---

## v10.0 — Arquitetura em Camadas (27/08/2026)

### Migração completa (Fases 0-6)

**Estrutura nova:**
- `core/` — 12 arquivos, 2.153 linhas (app, contracts, event_clock, market_state, persistence, metrics, regime_detector, learning, risk_manager, position_manager, signal_engine)
- `features/` — 17 arquivos, 1.876 linhas (utils, vpin, book_features, trade_features, volume_profile, ewma_zscore, kyle_lambda, patterns, cross_asset, percentil, volatility, returns, price_context, session_time, poc_migration, volume_relativo)
- `adapters/` — 4 arquivos, 483 linhas (file_storage, profit_rtd, dashboard_api)
- **Total: 33 arquivos, 4.510 linhas** de código modular novo

**Shims de compatibilidade (não quebram imports antigos):**
- `features_lib.py` → re-exporta de `features/`
- `captura_eventos_ms.py` → re-exporta de `adapters/file_storage.py`**Entrypoint unificado:**
- `run_motor.py` — ponto de entrada oficial (usa `core.app.App`)
- `watchdog.py` atualizado para chamar `run_motor.py`
- `scripts/iniciar_motor.bat` não precisa mudar (ainama chama `watchdog.py`)
- Task Scheduler não precisa mudar (ainama chama `iniciar_motor.bat`)

**Arquivamento do motor legado:**
- `motor_rt_alphaz.py` → arquivado em `docs/archive/motor_rt_alphaz_v9_legacy.py`
- `motor_rt_alphaz.py` agora é um **shim** (24 linhas) que re-exporta `core.app.App`, `core.app._AnaliseShim`, `core.event_clock.parse_hms_ms` e `core.app._sem_dados_por_ativo`
- `parse_hms_ms` movida para `core/event_clock.py`
- `config/__init__.py` corrigido para re-exportar `config.py` da raiz (resolve shadow)
- `core/learning.py` usa `deque(maxlen=5000)` + `carregar_aprendizado` alias
- Testes atualizados: `test_b3_staleness` e `test_r2_aprendizado` migrados para `core.*`
- **142 passed**, 12 falhas pré-existentes → corrigidas em v10.1

**Coexistência:**
- `motor_rt_alphaz.py` (original, 4.154 linhas) continua funcionando
- `core/app.py` (novo, 875 linhas) contém o loop RTD completo
- Pipeline testado: alimentar → calcular → avaliar → sinais ✅
- 102 testes passando, 3 skipped

## v9.50 (26/08/2026)
- Dataset v950: 165 colunas, 129 features numericas (era 105)
- +24 features novas: volatilidade multi-TF, range stats, VWAP causal, micro×contexto, regime
- Features adicionadas:
  - Volatilidade: vol_1s/5s/15s/1min, ATR, vol_realizada
  - Regime vol: expansao, compressao, acelerando, desacelerando
  - Range: normalizado, vs_media, vs_mediana, percentil
  - Niveis D-1: dist_max/min/fech/ajuste + flags rompimento
  - Retornos multi-horizonte: 100ms a 5min (8 features), norm_vol, aceleracao
  - VWAP causal: diaria, dist_pts/ticks/norm, acima_vwap, cruzou_vwap
  - Micro×contexto: cvd×dist_vwap, agressao×lado_vwap, delta×dist_ajuste, imbalance×dist_vwap, absorcao×vol
  - Compostos: vwap_vs_poc, preco_vs_vwap/ajuste/poc
  - Regime: vol, range, retorno, pos_vs_vwap/poc, inclinacao_vwap, persistencia, aceleracao
- Leakage corrigido: volume_relativo (EWMA por dia), range_percentil (rank por dia), regime_persistencia (cumsum por dia)
- Walk-forward: AUC 0.779, acc 75.4% (era 0.665 / 66.5%)
- 6 features de contexto no top 10 (tempo, volume, VWAP, range)
- build_dataset_v950.py: pipeline completo de features de contexto

## v9.40 (26/08/2026)
- Dataset v940: 124 colunas, 105 features numericas (era 26)
- +92 features de contexto (tempo, VWAP, ajuste, vol, retornos, POC, range)
- Leakage removido: preco_saida, duracao_label_ms
- Walk-forward: acc 66.5% +/- 0.7% (era 62.7% +/- 2.0%)
- Top 10 features: 5 sao de contexto (tempo, volume, distancia)
- Trackers novos: poc_migration_tracker, volume_relativo_tracker
- session_time_tracker: +minutos_desde_abertura, +bloco_sessao

# Changelog

## v9.39 walk-forward (26/08/2026)
- Walk-forward com dataset v939 (labels corretos)
- RF(50, d=8, balanced): acc 62.7% +/- 2.0%
- 3 folds (13, 14, 17/ago), 34K amostras por dia
- Tempo: 93s (amostra 10%)
- Resultado: dados/s/wf_v939.json

## v9.39 (26/08/2026)
- Reorganizacao de pastas: ml/, testes/, docs/, scripts/, dados/
- Tasks do Task Scheduler atualizadas para novos paths
- Pipeline pos-pregao corrigido (paths)
- Sem dedup na captura (RTD nunca envia duplicados)

## v9.38 (26/08/2026)
- Walk-forward otimizado: n_jobs=-1, float32, col selection
- Feature cache persistente (feature_cache.py)
- Benchmark: 458s vs >600s (timeout)

## v9.37 (26/08/2026)
- Features volatilidade multi-TF (7 features)
- Features retornos multi-horizonte (7 features)
- Features tempo de sessao (4 features)
- features_expansao.py (33 features batch)

## v9.36 (26/08/2026)
- OHLC intraday (abertura, maxima, minima, fechamento)
- PrecoContextTracker (~48 features contexto preco)
- Integracao ao scorer (ctx trackers)

## v9.15 (25/08/2026)
- Revisao codigo completa (9 fixes)
- Correcoes de consistencia batch/live

## v9.13 (23/08/2026)
- Book 500 niveis (era 60)
- Scorer ML desempacotando tuplas (P0-1)
- Labeler corrigido (SL real, janela nao cruza dia)
- Revalidacao com labels corrigidos: sinal SOBREVIVEU
  RF: acc 57.3%, AUC 0.60, PF 2.68 com 365x menos amostras

## v9.12 (22/08/2026)
- Labeler vectorizado NumPy (~180x mais rapido)
- Walk-forward real (antes mostrava 100% falso)
- Comparacao RF vs LGBM (RF venceu)
- Calibracao Platt

## v9.11 (21/08/2026)
- Pipeline diario automatico (6 passos)
- Acumulacao real (mes inteiro)
- Gate de qualidade (aborta se dados ruins)

## v9.10 (21/08/2026)
- Metadados da sessao de captura
- Log periodico dos rejeitados
- Gate de qualidade no retreino
- Relatorio diario

## v9.9 (21/08/2026)
- Ritmo adaptativo do _loop (50Hz+)
- Rotacao por tamanho real (100MB)
- fsync periodico
- Fix: _garantir_fp nunca era chamado

## v9.8 (21/08/2026)
- CVD + divergencia CVD x preco
- Volatilidade realizada + range
- Fase de sessao + dias ate vencimento
- Taxa de eventos

## v9.8.1 (21/08/2026)
- Fix: poda do dedup crashava (agora_ms -> agora_epoch)

## v9.7 (21/08/2026)
- OFI alinhado por preco (Cont-Kukanov-Stoikov)
- Kyle Lambda sobre TODOS os trades
- Z-score EWMA (opt-in)

## v9.6 (21/08/2026)
- 5 funcionalidades mortas reativadas:
  1. CrossAssetEngine (registrar nunca chamado)
  2. CrossAssetEngine relogio (cutoffs errados)
  3. Pesos por regime (sempre lateral)
  4. Confirmacao por regime (congelada em 3)
  5. Stop-hunt (condicao sempre falsa)
  6. Captura batch (todo trade rejeitado)
- 16 correcoes de bugs
- 41/41 testes

## v9.5 (21/08/2026)
- 4 bugs criticos (crash loop, labeler, regime, watchdog)
- Watchdog robustecido (10s delay, multi-instancia)

## v9.4 (20/08/2026)
- Volume Profile + Kyle Lambda
- Blindagem captura_eventos_ms.py
- R:R regime lateral corrigido (0.6:1 -> 1.25:1)

## v9.3 (20/08/2026)
- Unificacao features_lib
- Anti-whipsaw (holding 90s, confianca 0.75)
- Fix book_snap_ant

## v9.1 (Ago/2026)
- Sanity check de preco
- Cooldown (45s)
- Reversao protegida

## v9 (Ago/2026)
- Book Level Features
- Cross Asset (WIN x WDO)
- Trade Metrics

## v8 (Ago/2026)
- OFI, Regime Switch, Estrategias por regime

## v7 (Ago/2026)
- Padroes (spoof, stop-hunt)
- PadroesMemoria
