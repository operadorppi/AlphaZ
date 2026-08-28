# Componentes

> v10.0 — Referência por módulo (27/08/2026)

## core/ — Domínio do Motor (12 arquivos, 2.153 linhas)

### core/app.py (875 linhas) — Orquestrador + Loop RTD Completo

| Método | Descrição |
|--------|-----------|
| `__init__(config)` | Compõe: MarketState, SignalEngine, PositionManager, RiskManager, RegimeDetector, Learning, Persistence, Metrics, EventClock, FileStorage, ScorerML |
| `_carregar_scorer()` | Carrega modelo ML do config (opcional) |
| `_sync_estados()` | Cria EstadoAtivo por ativo descoberto no RTD |
| `_conectar_e_descobrir()` | Conecta ao ProfitChart via motor_web COM, descobre ativos |
| `_assinar_topicos(ativos_rtd)` | Assina tópicos BOOK + T&T no RTD |
| `_processar_dados(dados)` | Parse RefreshData, alimenta mercado + ML + salva bruto |
| `_loop()` | Loop principal: PumpEvents → RefreshData → processar (com watchdog COM) |
| `_reconectar()` | Reconexão automática: fecha COM, espera 2s, reconnecta |
| `_verificar_staleness_reconexao(cooldown)` | Detecta silêncio por ativo e reconecta (pregão, cooldown 30s) |
| `get_rtd_health()` | Status da conexão RTD por ativo |
| `get_contexto_mercado()` | VWAP, ajuste, distâncias para dashboard |
| `salvar_sessao(final)` | Flush tudo + checkpoint + aprendizado + padrões |
| `parar()` | Shutdown graceful |
| `_AnaliseShim` | Shim interno para compatibilidade com DashboardAPI |

### core/contracts.py (70 linhas) — Dataclasses

| Classe | Campos | Descrição |
|--------|--------|-----------|
| `Signal` | lado, score, confianca, motivos, contrib, horizonte | Saída do SignalEngine |
| `Action` | tipo, lado, preco, tp, sl, motivo | Decisão do PositionManager |
| `ExitSignal` | preco, motivo, pnl, holding_s | Sinal de saída |
| `RiskDecision` | permitido, motivo, cooldown_restante | Resposta do RiskManager |
| `Position` | lado, preco_entrada, tp, sl, aberta_em, regime_abertura, stop_preco, breakeven | Estado de posição aberta |

### core/event_clock.py (120 linhas) — Relógio Mestre

| Método | Descrição |
|--------|-----------|
| `parse_hms_ms(s)` | Parse HH:MM:SS.mmm → ms desde meia-noite |
| `tod_de_ts(ts_ms)` | Converte epoch → time-of-day (hora local B3) |
| `agora_tod_ms()` | TOD atual em ms |
| `agora_epoch_ms()` | Epoch atual em ms |
| `virou_dia()` | True se o dia mudou desde a última chamada |
| `reset_sessao()` | Reseta início de sessão |

### core/market_state.py (507 linhas) — Estado de Mercado

| Classe/Método | Descrição |
|---------------|-----------|
| `EstadoAtivo` | Estado bruto RTD: book_bid, book_ask, tt_rows, dedup |
| `MarketState.__init__` | historico, features_por_seg, stats, ohlc, trackers, padroes |
| `preco_plausivel(sym, preco)` | Sanity check: rejeita preço fora da faixa |
| `obter_ultimo_preco(ativo)` | Último preço conhecido |
| `alimentar_negocio(ativo, tms, preco, qtd, agr, comp, vend)` | Adiciona negócio ao estado |
| `alimentar_book(ativo, snap, bid_vol, ask_vol, estado)` | Processa snapshot do book |
| `get_historico(segundos)` | Série por segundo para dashboard |
| `get_book_level()` | Features de book + cross-asset |
| `get_book_stats()` | Estatísticas de book |
| `get_resumo(ativo)` | Resumo de negócios por ativo |
| `get_saldo_corretoras(ativo)` | Saldos por corretora |
| `get_memoria(...)` | Contadores globais |
| `extrair_book_snapshot(estado)` | Converte EstadoAtivo → dict para BookLevelFeatures |
| `comparar_books(snap_ant, snap_atu)` | Detecta retiradas, reposições, layering |

### core/persistence.py (194 linhas) — I/O

| Método | Descrição |
|--------|-----------|
| `garantir_fp()` | Abre file handles JSONL |
| `gravar_trade(neg)` | Buffer de trades (flush a 200) |
| `gravar_decisao(dec)` | Buffer de decisões (flush a 50) |
| `_rotacionar(attr, prefix)` | Rotação por tamanho (100MB) |
| `_flush_trades()` | Flush + fsync periódico |
| `_flush_decisoes()` | Flush + fsync periódico |
| `carregar_checkpoint()` | Restaura posição do disco |
| `salvar_checkpoint(posicao)` | Salva posição em disco |
| `salvar_sessao(final, ...)` | Flush tudo + checkpoint + aprendizado |
| `close()` | Fecha file handles |

### core/metrics.py (74 linhas) — Métricas

| Método | Descrição |
|--------|-----------|
| `calcular()` | Acurácia, PF, Sharpe, MaxDD, expectancy |
| `get_estatisticas()` | Total, acertos, pesos, acuracia_por_feature |

### core/regime_detector.py (99 linhas) — Regime

| Método | Descrição |
|--------|-----------|
| `detectar(ativo, historico)` | Regime bidimensional: direção × vol (cache 5s) |
| `ajustar(ativo, score, motivos, ...)` | Ajusta score por regime, retorna estratégia |

### core/learning.py (165 linhas) — Aprendizado

| Método | Descrição |
|--------|-----------|
| `aprender_mfe_mae(contrib, acertou, mfe, mae, ...)` | Ajusta pesos por MFE/MAE com decay |
| `_recalc_acuracia()` | Recalcula acurácia por feature |
| `carregar(base_dir)` | Carrega learning_state.json (deque(maxlen=5000)) |
| `carregar_aprendizado(base_dir)` | Alias de compatibilidade |
| `salvar(base_dir)` | Salva pesos, feature_hits, resultados |

### core/risk_manager.py (178 linhas) — Risco

| Função/Classe | Descrição |
|----------------|-----------|
| `custo_execucao(ativo, config)` | Custo em pts (WIN=5, WDO=1) |
| `horario_permite_abrir(config)` | Verifica horário B3 |
| `RiskManager.pode_abrir(ativo, resultados)` | Circuit breaker, cooldown, horário, limite trades |
| `RiskManager.calcular_tp_sl(...)` | TP/SL por volatilidade + regime + confiança |
| `RiskManager.registrar_resultado(pnl, acertou, ativo)` | Atualiza circuit breaker |
| `RiskManager.reset_dia()` | Reset diário de contadores |

### core/position_manager.py (232 linhas) — Posições

| Método | Descrição |
|--------|-----------|
| `suavizar(lado_bruto, confirmacao_necessaria)` | Suavização de sinal com confirmação N segmentos |
| `gerenciar(ativo, sinal, preco, tp, sl, ...)` | Abrir/manter/fechar baseado no sinal |
| `checar_saidas(preco, max_holding_s)` | TP/SL/reversão/breakeven/trailing em tempo real |
| `_fechar(preco, motivo)` | Fecha posição, registra resultado, aprende |
| `get_posicao(ultimo_preco_fn)` | Posição atual com PnL em tempo real |

### core/signal_engine.py (346 linhas) — Scoring

| Método | Descrição |
|--------|-----------|
| `calcular(seg, skip_avaliar)` | Calcula features do segundo para todos os ativos |
| `avaliar(ativo, f)` | Scoring heurístico: ~30 features ponderadas + ML |
| `get_features()` | Features com regime e OHLC |
| `get_sinais()` | Sinais atuais |

---

## features/ — Microestrutura (17 arquivos, 1.876 linhas)

### features/utils.py (155 linhas) — Funções Puras

| Função | Descrição |
|--------|-----------|
| `ewma_update(anterior, valor, alpha)` | EWMA incremental O(1) |
| `hhi(volumes)` | Herfindahl-Hirschman Index |
| `entropia(volumes)` | Entropia de Shannon |
| `idade_ms(ts_ref, ts_fonte)` | Diferença de timestamps (asof join) |
| `dias_ate_vencimento(simbolo)` | Dias até vencimento B3 |
| `fase_sessao(tod_ms, ...)` | Fase: abertura/meio/almoco/fechamento |
| `_tod_de_ts(ts_ms)` | Epoch → time-of-day (hora local B3) |
| `_sanitize(v)` | NaN/Inf → 0.0 |
| `classificar_corretora(broker)` | Classifica como 'inst' ou 'varejo' |
| `asof_join_linhas(principal, contexto, tol)` | Merge temporal WIN×WDO |

### features/vpin.py (41 linhas) — VPINTracker
| Classe | Descrição |
|--------|-----------|
| `VPINTracker` | VPIN clássico por bucket de volume |

### features/book_features.py (264 linhas) — Book + OFI
| Classe | Descrição |
|--------|-----------|
| `OFITracker` | Order Flow Imbalance alinhado por preço (Cont-Kukanov-Stoikov) |
| `BookLevelFeatures` | 30+ features de book: spread, mid, microprice, imbalance L1-L250, OFI, velocidade, HHI, slope, micro_drift |

### features/trade_features.py (231 linhas) — T&T + Gerador
| Classe | Descrição |
|--------|-----------|
| `JanelaFeatures` | Agregação T&T em janela deslizante 100ms: aggr_imb, EWMA, HHI, entropia, VPIN, CVD, volatilidade, range, fase |
| `GeradorJanelas` | Emite snapshots a cada passo de relógio (100ms), mantém book+VP+Kyle por ativo |

### features/volume_profile.py (52 linhas) — VP/POC
| Classe | Descrição |
|--------|-----------|
| `VolumeProfileTracker` | POC, VAH, VAL (value area 70%), distâncias |

### features/ewma_zscore.py (33 linhas)
| Classe | Descrição |
|--------|-----------|
| `EWMAZScore` | Z-score por EWMA (estacionaridade) |

### features/kyle_lambda.py (41 linhas)
| Classe | Descrição |
|--------|-----------|
| `KyleLambdaTracker` | Kyle's Lambda (regressão dP ~ lambda*V_signed) |

### features/patterns.py (256 linhas) — Padrões
| Classe | Descrição |
|--------|-----------|
| `PadroesMemoria` | Spoof, stop-hunt, absorção, perfil horário, persistência entre sessões |

### features/cross_asset.py (215 linhas) — WIN×WDO
| Classe | Descrição |
|--------|-----------|
| `CrossAssetEngine` | Liderança temporal WDO→WIN, correlação rolling, divergência de fluxo |

### features/percentil.py (199 linhas) — Percentis + Range + Acumulação
| Classe | Descrição |
|--------|-----------|
| `PercentilTracker` | Percentis em janela deslizante (bisect O(log n)) |
| `RangeTracker` | Range de varredura: topo/fundo/testes/expansão |
| `AccumulationTracker` | Acumulação por corretora no range, direção provável |

### Trackers de Contexto (6 arquivos, ~450 linhas)
| Arquivo | Classe | Features |
|---------|--------|----------|
| `volatility.py` | `VolatilityTracker` | 7 (vol_100ms a vol_5min) |
| `returns.py` | `ReturnsTracker` | 7 (retorno_100ms a retorno_5min) |
| `price_context.py` | `PrecoContextTracker` | 48 (OHLC, D-1, distâncias, gaps) |
| `session_time.py` | `SessionTimeTracker` | 4 (segundos, minutos, sin/cos) |
| `poc_migration.py` | `PocMigrationTracker` | 3 (delta, velocity, direction) |
| `volume_relativo.py` | `VolumeRelativoTracker` | 3 (acumulado, por_minuto, relativo) |

---

## adapters/ — I/O Externo (5 arquivos, 558 linhas)

### adapters/file_storage.py (234 linhas) — Captura Bruta
| Classe/Método | Descrição |
|---------------|-----------|
| `CapturaEventosMS` / `FileStorage` | Buffer thread-safe para JSONL bruto |
| `registrar_negocios(novos)` | Blindagens: ts futuro/antigo, qtd, preco |
| `registrar_book(ativo, ts_ms, snap, ...)` | Snapshot de book com levels |
| `_flush_neg/book()` | Flush + fsync + rotação 100MB |
| `fechar()` | Fecha + grava metadados da sessão |

### adapters/profit_rtd.py (79 linhas) — Conexão COM
| Função | Descrição |
|--------|-----------|
| `conectar_servidor()` | Conecta ao ProfitChart RTD via COM |
| `descobrir_ativos_rtd()` | Descobre janelas BOOK/T&T |
| `preparar_ativos()` | Prepara ativos para assinatura |
| `_connect(srv, strings)` | Assina tópico RTD |
| `_refresh(srv)` | RefreshData → dados |
| `parse_refresh_data(data)` | Parse do RefreshData |
| `parse_dat(s, dia_ref)` | Parse timestamp RTD |
| `thread_com(...)` | Thread COM com watchdog |
| `thread_escritora(...)` | Writer book Parquet |
| `thread_escritora_tt(...)` | Writer T&T Parquet |
| `enforce_schema(df, schema)` | Validação de schema |
| `write_parquet_part(...)` | Grava partição por hora |
| `_diag()` | Diagnóstico RTD |

### adapters/com_watchdog.py (75 linhas) — Watchdog COM
| Classe/Constante | Descrição |
|------------------|-----------|
| `COMHeartbeatMonitor` | Thread daemon que monitora heartbeat do loop COM |
| `COMHeartbeatMonitor.heartbeat()` | Registra heartbeat (chamar após cada RefreshData ok) |
| `COMHeartbeatMonitor.start()` | Inicia thread monitora |
| `COMHeartbeatMonitor.stop()` | Para thread monitora |
| `COMHeartbeatMonitor.stuck_event` | Event setado quando COM travado (timeout) |
| `COMHeartbeatMonitor.stuck_count` | Contagem de detecções (só incrementa 1x por stuck) |
| `COM_WATCHDOG_TIMEOUT_S` | Timeout padrão: 10s |
| `COM_WATCHDOG_CHECK_S` | Intervalo de checagem: 1s |

### adapters/dashboard_api.py (154 linhas) — HTTP
| Classe | Descrição |
|--------|-----------|
| `DashboardAPI` | Handler HTTP com 16 endpoints |

**Endpoints:**

| Path | Descrição |
|------|-----------|
| `/` | Dashboard HTML (dashboard_pro.html, cache por mtime) |
| `/api/features` | Features com regime e OHLC |
| `/api/sinais` | Sinais atuais |
| `/api/posicao` | Posição aberta |
| `/api/learning` | Estatísticas de aprendizado |
| `/api/memoria` | Contadores globais |
| `/api/book` | Estatísticas de book |
| `/api/book_level` | Features de book + cross-asset |
| `/api/metricas` | Métricas (PF, Sharpe, DD) |
| `/api/resumo` | Resumo por ativo |
| `/api/padroes` | Padrões (spoof, stop-hunt) |
| `/api/rtd_health` | Status RTD |
| `/api/saldo_corretoras` | Saldos por corretora |
| `/api/contexto` | VWAP, ajuste, distâncias |
| `/api/historico` | Série temporal por segundo |
| `/api/all` | Agregação de tudo |
| `/health` | Status + uptime |

---

## motor_rt_alphaz.py (25 linhas) — Shim de Compatibilidade

O motor original (4.154 linhas) foi arquivado em:
`docs/archive/motor_rt_alphaz_v9_legacy.py`

Este shim re-exporta de `core/`:

| Re-export | Origem |
|-----------|--------|
| `App` | `core.app.App` |
| `Analise` | `core.app._AnaliseShim` |
| `parse_hms_ms` | `core.event_clock.parse_hms_ms` |
| `_sem_dados_por_ativo` | `core.app._sem_dados_por_ativo` |
| `datetime` | `datetime.datetime` (monkeypatchável nos testes) |

### motor_web.py (2.585 linhas) — Conexão COM Original

Contém: _carregar_interfaces, _criar_callback, _connect, _refresh,
parse_dat, parse_refresh_data, thread_com, thread_escritora,
thread_escritora_tt, enforce_schema, write_parquet_part,
consolidar_book/tt_parquet, _diag, _DashboardState.

`adapters/profit_rtd.py` é um shim que re-exporta tudo daqui.

### scorer.py (314 linhas) — ScorerML Live
| Classe | Descrição |
|--------|-----------|
| `VWAPTracker` | VWAP intraday incremental |
| `ScorerML` | ML live: carrega modelo, alimenta trackers, prevê |
| `ScorerML.evento()` | Alimenta trackers com negócio |
| `ScorerML.book()` | Alimenta trackers com book |
| `ScorerML._prever()` | Predição ML → probabilidade |
| `ScorerML.estado_salud()` | Status do scorer (falhas, erro) |

### config.py (105 linhas) — Config Central
| Elemento | Descrição |
|----------|-----------|
| `_carregar()` | Lê config.json, sobrescreve defaults |
| `_validar()` | Validação pydantic (TP, SL, threshold) |
| `CONFIG` | Dict global de configuração |
| `ATIVO_PRINCIPAL` / `ATIVO_CONTEXTO` | Ativos do pregão |
| `SAVE_DIR` | Diretório de dados |

---

## config/ — Configuração (2 arquivos)

### config/defaults.py (168 linhas) — ConfigCompleto + Flat/Aninhado
| Classe/Função | Descrição |
|---------------|-----------|
| `ConfigCompleto` | Objeto com 35 atributos flat (defaults do motor original) |
| `NESTED_TO_FLAT` | Mapeamento de 24 chaves aninhadas → flat |
| `_aplicar_valor_config(atual, novo)` | Conversão de tipo (int/float/tuple/bool/dict) |
| `_aplicar_chaves_flat(dados, cfg_obj)` | Aplica chaves flat ao ConfigCompleto |
| `_aplicar_config_externa(ext, cfg_obj)` | Aplica config.json completo (flat + aninhado) |

### config/__init__.py (32 linhas) — Re-export
Re-exporta `CONFIG`, `ATIVO_PRINCIPAL`, `SAVE_DIR` de `config.py` raiz
+ `ConfigCompleto`, `_aplicar_*` de `config/defaults.py`.
`__file__` overrideado para raiz (resolve shadow do pacote).

---

## Shims de Compatibilidade

| Arquivo | Linhas | Delega para |
|---------|--------|-------------|
| `features_lib.py` | 23 | `features/` (re-exporta tudo) |
| `captura_eventos_ms.py` | 9 | `adapters/file_storage.py` |
| `motor_rt_alphaz.py` | 25 | `core/app.py` (App, _AnaliseShim, etc) |

---

## ml/ — Pipeline ML (29 arquivos)

| Arquivo | Descrição |
|---------|-----------|
| `labeler_vectorizado.py` | Labels TP=100/SL=50 (NumPy, ~180x mais rápido) |
| `dataset_builder.py` | Constrói dataset parquet (v939, 26 features) |
| `build_dataset_v940.py` | Dataset v940 (105 features, com contexto) |
| `build_dataset_v950.py` | Dataset v950 (129 features, +24 contexto avançado) |
| `walk_forward.py` | Walk-forward temporal (7d treino / 1d teste) |
| `walk_forward_otimizado.py` | Walk-forward com n_jobs=-1 + feature cache |
| `features_expansao.py` | 33 features batch (vol, retornos, sessão) |
| `features_contexto_preco.py` | Features de preço batch (OHLC, D-1, distâncias) |
| `features_contexto_avancado.py` | VWAP, POC, compostos batch |
| `treino_lib.py` | Utilitários: flatten, split_com_purge, avaliar |
| `validacao_rigorosa.py` | Validação com purge/embargo |
| `ablation_test.py` | Ablation por grupo de features |
| `lightgbm_tune.py` | Tuning de hiperparâmetros |
| `calibrar_modelo.py` | Calibração Platt |

---

## scripts/ — Automação (9 arquivos)

| Arquivo | Descrição |
|---------|-----------|
| `iniciar_motor.bat` | Inicia watchdog → run_motor.py |
| `pipeline_after_market.bat` | Pipeline pós-pregão (18:35) |
| `pipeline_diario.py` | Orquestra labeler → dataset → treino → walk-forward |
| `relatorio_diario.py` | Relatório de desempenho do dia |
| `atualizar_documentacao.py` | Atualiza docs automaticamente |
| `servidor_proxy_dashboard.py` | Proxy para dashboard remoto |
| `observability.py` | Métricas de observabilidade |
