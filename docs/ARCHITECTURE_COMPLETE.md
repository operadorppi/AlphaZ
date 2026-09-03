# Freebuff 交易引擎 — 完整架构文档

> 最后更新: 2026-08-29
> 版本: v12.0 (架构分层完成)

---

## 一、项目概述

Freebuff 是一个基于 Python 的 **实时期货量化交易系统**，针对巴西 B3 交易所（WIN/IND 股指期货、WDO/DOL 外汇期货）设计。系统从 ProfitChart RTD 实时接收市场数据，计算微结构特征，结合 ML 模型与启发式规则生成交易信号，执行风险管理，并通过 HTTP 仪表盘监控。

**核心技术栈:**
- 数据源: ProfitChart RTD (COM 接口, Windows only)
- 存储: JSONL (原始数据) + Parquet (结构化历史)
- ML: LightGBM (推理), sklearn (验证)
- 特征: 165+ 微结构特征 (VPIN, OFI, VP/POC, Kyle Lambda, Cross-Asset 等)
- 发布: 仪表盘 HTTP server (端口 5001)

**项目规模:** ~142 个 Python 文件, ~37,367 行代码, ~1,238 个函数, ~185 个类

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Task Scheduler (Windows)                      │
│                    08:45 启动 · 18:35 停止 · 18:35 后处理           │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                          watchdog.py                                 │
│           监控进程 · 自动重启 (max 10/hour) · 防重复锁               │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                          run_motor.py                                │
│                         统一入口点                                   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼──┐   ┌────────▼─────┐  ┌─────▼──────────┐
    │  adapters/  │   │   core/      │  │  features/     │
    │  数据接入层  │   │   领域核心    │  │  特征计算层     │
    └──────┬─────┘   └──────┬───────┘  └──────┬─────────┘
           │                │                 │
    ┌──────▼────────────────▼─────────────────▼──────────┐
    │              core/app.py (App 或questrator)          │
    │        主循环: COM → 特征 → 信号 → 决策 → 执行         │
    └──────┬─────────────────────────────────────┬────────┘
           │                                     │
    ┌──────▼──────┐                     ┌────────▼────────┐
    │  adapters/  │                     │    ml/          │
    │ dashboard/  │                     │   pipeline      │
    │  (HTTP)     │◄───────────────────►│ (offline batch) │
    └─────────────┘                     └─────────────────┘
```

---

## 三、模块详解

### 3.1 adapters/ — 外部 I/O 层

| 文件 | 行数 | 核心类/函数 | 职责 |
|------|------|-------------|------|
| `base.py` | 34 | `MarketDataSource` (ABC) | 数据源抽象接口 |
| `profit_rtd.py` | 276 | `ProfitRTDAdapter` | RTD COM 连接 + 事件流 |
| `rtd_connection.py` | 465 | `conectar_servidor()`, `descobrir_ativos_rtd()`, `_connect()`, `_refresh()` | COM 接口封装 |
| `rtd_parser.py` | 260 | `parse_refresh_data()`, `parse_dat()`, `enforce_schema()` | RTD 数据解析 |
| `rtd_writer.py` | 729 | `thread_escritora()`, `thread_escritora_tt()`, `write_parquet_part()`, `consolidar_*` | Parquet 写入 + 验证 |
| `file_storage.py` | 274 | `CapturaEventosMS`/`FileStorage` | JSONL 原始数据捕获 |
| `com_watchdog.py` | 103 | `COMHeartbeatMonitor` | COM 心跳监控 (60s timeout) |
| `dashboard_server.py` | 67 | `DashboardServer` | HTTP 服务器管理 |
| `dashboard/api.py` | 162 | `DashboardAPI` | HTTP 路由 (18 个 endpoint) |
| `dashboard/state.py` | 66 | `_SnapshotState` | 共享状态队列 |
| `dashboard/handlers.py` | 204 | `DashboardHandler` | 各 endpoint 处理 |
| `replay.py` | 71 | `ReplayDataSource` | 离线回放数据源 |

### 3.2 core/ — 领域核心层

| 文件 | 行数 | 核心类/函数 | 职责 |
|------|------|-------------|------|
| `app.py` | 678 | `App` | **主或questrator**, RTD 完整循环 |
| `contracts.py` | 257 | `Signal`, `Action`, `RiskDecision`, `TradeEvent`, `BookSnapshot` 等 | 类型契约 |
| `market_state.py` | 675 | `MarketState`, `EstadoAtivo` | 市场状态 (thread-safe, RLock) |
| `signal_engine.py` | 498 | `SignalEngine` | 特征计算 → 信号评分 |
| `position_manager.py` | 336 | `PositionManager` | 开仓/平仓/TP/SL/反转 |
| `risk_engine.py` | 584 | `RiskEngine` | **14 重风险保护** |
| `risk_manager.py` | 216 | `RiskManager`, `custo_execucao()`, `horario_permite_abrir()` | 风险决策辅助 |
| `regime_detector.py` | 130 | `RegimeDetector` | 双向度市场 regime 检测 |
| `learning.py` | 265 | `Learning` | MFE/MAE 权重学习 + feature death |
| `persistence.py` | 194 | `Persistence` | 交易/决策 JSONL 持久化 + checkpoint |
| `capture_daemon.py` | 328 | `CaptureDaemon` | **不朽线程**, 原始数据捕获 |
| `calibration.py` | 493 | `ProbabilityCalibrator`, `ModelDecisionSeparator` | ML 概率校准 |
| `metrics.py` | 74 | `Metrics` | PF, Sharpe, DD 计算 |
| `event_clock.py` | 66 | `EventClock` | 主时钟 + 日切 |
| `decision_journal.py` | 246 | `DecisionJournal`, `DecisionEntry` | 决策审计日志 |
| `leakage_test.py` | 112 | — | 前瞻性泄露测试 |
| `utils.py` | 48 | `fnum()`, `fint()`, `sstr()`, `parse_hms_ms()`, `tod_ms()` | 通用工具 |

### 3.3 features/ — 特征计算层

| 文件 | 行数 | 核心类/函数 | 职责 |
|------|------|-------------|------|
| `feature_engine.py` | 120 | `FeatureEngine` | 1s 窗口聚合 T&T 数据 |
| `feature_registry.py` | 1032 | `FeatureRegistry`, `FeatureDefinition`, `REGISTRY` | **165+ 特征注册表** |
| `trade_features.py` | 259 | `JanelaFeatures`, `GeradorJanelas` | T&T 滚动窗口特征 |
| `book_features.py` | 266 | `BookLevelFeatures`, `OFITracker` | Book 微结构 (30+ 特征) |
| `cross_asset.py` | 296 | `CrossAssetEngine`, `CrossAssetManager` | 跨资产 (WIN↔IND, WDO↔DOL) |
| `institutional_context.py` | 243 | `InstitutionalContext` | VWAP/开/高/低/结算距离 |
| `volume_profile.py` | 52 | `VolumeProfileTracker` | POC/VAH/VAL |
| `vpin.py` | 41 | `VPINTracker` | VPIN (知情交易概率) |
| `kyle_lambda.py` | 41 | `KyleLambdaTracker` | Kyle's Lambda (价格冲击) |
| `percentil.py` | 199 | `PercentilTracker`, `RangeTracker`, `AccumulationTracker` | 百分位/范围/累积 |
| `ewma_zscore.py` | 33 | `EWMAZScore` | EWMA Z-score |
| `patterns.py` | 256 | `PadroesMemoria` | 市场操纵模式 (spoof, stop-hunt) |
| `price_context.py` | 125 | `PrecoContextTracker` | 价格上下文 (OHLC, D-1) |
| `volatility.py` | 26 | `VolatilityTracker` | 多时间框架波动率 |
| `returns.py` | 26 | `ReturnsTracker` | 多时间框架收益 |
| `session_time.py` | 46 | `SessionTimeTracker` | 时段特征 (sin/cos) |
| `poc_migration.py` | 64 | `PocMigrationTracker` | POC 迁移 |
| `volume_relativo.py` | 64 | `VolumeRelativoTracker` | 相对成交量 |
| `vwap_tracker.py` | 72 | `VWAPTracker` | 日内 VWAP |
| `utils.py` | 155 | `ewma_update()`, `hhi()`, `entropia()`, `fase_sessao()` 等 | 纯函数工具 |

### 3.4 ml/ — 离线 ML Pipeline

| 文件 | 行数 | 核心类/函数 | 职责 |
|------|------|-------------|------|
| `labeler_vectorizado.py` | 383 | `label_vectorizado()`, `processar_jsonl()` | Triple Barrier 标签生成 |
| `dataset_builder.py` | 320 | `DatasetBuilder` | Parquet 数据集构建 |
| `scorer.py` | 363 | `ScorerML` | **ML 在线推理引擎** |
| `treino_lib.py` | 239 | `flatten_snapshot()`, `split_com_purge()`, `avaliar_modelo()` | 训练工具 |
| `walk_forward*.py` | ~1100 | `WalkForward`, `WalkForwardCompleto` | 时序交叉验证 |
| `retreinar_lgbm_limpo.py` | 354 | — | LightGBM 重训练 |
| `model_validation.py` | 317 | `ModelValidator` | 模型验证报告 |
| `validacao_rigorosa.py` | 611 | `auditar_leakage()`, `walk_forward_rigoroso()` | 严格验证 |
| `ablation_test.py` | 345 | `ablation_test()` | 特征消融实验 |
| `feature_manifest.py` | 234 | `FeatureManifest` | 特征清单 (train↔prod parity) |
| `features_contexto_avancado.py` | 503 | `adicionar_ajuste_oficial()`, `adicionar_vwap_causal()` | 进阶特征注入 |
| `features_contexto_preco.py` | 362 | `adicionar_contexto_preco()` | 价格上下文特征 |
| `features_expansao.py` | 139 | `adicionar_expansao()` | 扩展特征 (vol, returns, POC) |
| `calibrar_modelo.py` | 436 | `CalibrarModelo` | 概率校准 (Platt scaling) |
| `replay_temporal.py` | 819 | `ReplayTemporal` | 历史回放引擎 |
| `model_metadata.py` | 240 | `ModelMetadata`, `ModelMetrics` | 模型元数据 |
| `model_registry.py` | 253 | `ModelRegistry` | 模型版本管理 |
| `batch_processor.py` | 233 | `BatchProcessor` | 批量数据处理 |
| `lightgbm_tune.py` | 167 | — | 超参数调优 |

### 3.5 scripts/ — 自动化脚本

| 文件 | 职责 |
|------|------|
| `pipeline_diario.py` | 全日 pipeline: 报告 → features → labels → dataset → gate → 重训 |
| `relatorio_diario.py` | 每日绩效报告 |
| `observability.py` | Prometheus 指标 + 结构化日志 |
| `servidor_proxy_dashboard.py` | 仪表盘代理 |
| `atualizar_documentacao.py` | 自动更新文档 |
| `verificar_importancia.py` | 特征重要性检查 |
| `iniciar_motor.bat` | 启动入口 |
| `pipeline_after_market.bat` | 盘后处理 |
| `auto_sync.py` | 自动同步 |

### 3.6 根目录启动文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `run_motor.py` | 46 | **主入口**, 初始化 App + RTD |
| `watchdog.py` | 430 | 进程监控 + 自动重启 |
| `motor_rt_alphaz.py` | 25 | 兼容性 shim → core/app.py |
| `motor_web.py` | 1116 | RTD 连接编排器 (含 legacy 逻辑) |
| `replay_engine.py` | 451 | 离线回放引擎 (paper/validação 模式) |
| `run_all_tests.py` | 704 | 测试运行器 |
| `config.py` | 13 | 配置加载 (代理到 config/) |
| `dashboard_pro.html` | 865 | 专业仪表盘前端 |
| `feature_registry.json` | 1K | 注册的特征清单 (JSON) |
| `config.json` | 78 | 运行时配置 |
| `walk_forward_v950.json` | 807 | Walk-forward 结果 |

---

## 四、实时交易数据流 (Live Pipeline)

```
ProfitChart RTD (COM, Windows only)
    │
    ▼
adapters/profit_rtd.py — ProfitRTDAdapter
    │  events() → Iterator[MarketEvent]
    │  类型: TRADE (T&T) / BOOK (250ms 快照)
    │
    ├─→ core/capture_daemon.py (不朽线程)
    │       │  JSONL 原始数据 → D:/MarketData/mimo/raw_*.jsonl
    │       │  独立于交易循环, 即使 crash 也不丢数据
    │       │
    │       └─→ adapters/file_storage.py (FileStorage)
    │               └─→ JSONL 滚动写入 (每 100MB 旋转)
    │
    ├─→ adapters/rtd_writer.py (并行多进程)
    │       │  thread_escritora() → Parquet (BOOK)
    │       │  thread_escritora_tt() → Parquet (T&T)
    │       │  分区: RAW/ano=2026/mes=08/dia=29/sym=WINV26/tipo=BOOK/
    │       └─→ 写后每小时 consolidar_book_parquet / consolidar_tt_parquet
    │
    ▼
core/market_state.py — MarketState (RLock 线程安全)
    │  更新: historico (deque), stats, OHLC, book_snap_ant, trackers
    │  验证: preco_plausibil() (sanity check, rollover 自动适应)
    │
    ├─→ features/ 各 Tracker 更新
    │       VPIN, OFI, BookLevel, VolumeProfile, Kyle, CrossAsset,
    │       PriceContext, Volatility, Returns, SessionTime, ...
    │
    ▼
core/signal_engine.py — SignalEngine
    │  1. FeatureEngine.processar_lote() → 1s 窗口特征
    │  2. RegimeDetector.detectar() → 双向度 regime (方向×波动)
    │  3. ScorerML._prever() → ML 概率 (if 模型可用)
    │  4. 启发式 scoring → score, confiança, motivos
    │  5. 组合: ML gate + heurística → sinal (C/V/空)
    │  6. 返回 Signal(dataclass)
    │
    ▼
core/risk_engine.py — RiskEngine (14 重保护)
    │  1. Kill Switch  2. Circuit Breaker  3. Daily Loss Limit
    │  4. Max Trades   5. Cooldown         6. Consecutive Loss
    │  7. Stale Data   8. Spread Protection 9. Volatility Protection
    │  10. Model Unavailable  11. Confidence Protection
    │  12. Session Protection  13. (Kill Switch 重复)  14. Circuit Breaker
    │
    ▼
core/position_manager.py — PositionManager
    │  决策: ABRIR / MANTER / FECHAR / COOLDOWN / REJEITADO
    │  TP/SL: 动态计算 (基于波动率 + regime + confiança)
    │  Trailing Stop: 50%/75%/90% MFE 阶梯锁定
    │  金字塔: ML prob > 0.65 + confiança > 0.85 → 加仓
    │  反转: 信号反向 + confiança > 0.75 → 平仓
    │
    ▼
adapters/dashboard/ — HTTP API
    │  端口 5001, 18 个 endpoint
    │  每秒 snapshot 更新 (独立线程)
    │
    ▼
dashboard_pro.html — 专业仪表盘 (浏览器)
```

---

## 五、离线 ML Pipeline (盘后)

```
D:/MarketData/mimo/raw_negocios_ms_*.jsonl
    │
    ▼
ml/batch_processor.py — 100ms 特征批量计算
    │
    ▼
ml/labeler_vectorizado.py — Triple Barrier 标签
    │  TP=100pts (WIN), SL=50pts, 窗口=30s
    │  标签: +1(TP), -1(SL), 0(TIMEOUT), -99(AMBIGUOUS)
    │
    ▼
ml/dataset_builder.py — 数据集构建
    │  asof join WIN × WDO
    │  添加: 价格上下文 + VP/POC + 波动率 + 收益 + 时段
    │  VWAP + 官方结算价
    │
    ▼
Parquet (dataset_final.parquet) — ~340 万行, ~165 列
    │
    ├─→ ml/walk_forward*.py — 时序交叉验证 (7d 训练 / 1d 测试)
    │     AUC 0.779, accuracy 75.4% (v950)
    │
    ├─→ ml/ablation_test.py — 特征消融
    │     8 feature 子集 (VP+流) PF 2.79 > 全部 29: PF 2.63
    │
    ├─→ ml/validacao_rigorosa.py — 严格验证
    │     Leakage 审计 + 标签审计 + Walk-forward + 消融 + 鲁棒性
    │
    └─→ ml/retreinar_lgbm_limpo.py — LightGBM 重训练
          每日自动 (通过 pipeline_diario.py)
```

---

## 六、数据格式

### 6.1 Raw 数据 (JSONL)

```json
// raw_negocios_ms_<session>.jsonl — 每行一个交易
{
    "ts_ms": 1724944821410,    // epoch ms
    "ativo": "WINV26",
    "preco": 178250,
    "qtd": 5,
    "agressor": "Comprador",
    "compradora": "BTG",
    "vendedora": "XP"
}

// raw_book_ms_<session>.jsonl — 每 250ms 一个快照
{
    "ts_ms": 1724944821500,
    "ativo": "WINV26",
    "bid_vol": 1250,
    "ask_vol": 980,
    "por_corretora": {"BTG": {"bid_vol": 500, "ask_vol": 300}, ...},
    "levels": {"bid_preco": [...], "bid_vol": [...], ...}
}
```

### 6.2 Parquet (结构化历史)

```
D:/MarketData/mimo/RAW/
├── ano=2026/mes=08/dia=29/
│   ├── sym=WINV26/tipo=TT/
│   │   ├── 09.part_1724943200000_1234_5678.parquet
│   │   ├── 10.parquet          ← 每小时 consolidation
│   │   └── _stats_captura.json
│   └── sym=WINV26/tipo=BOOK/
│       ├── 09.part_*.parquet
│       └── 10.parquet
```

### 6.3 Parquet Schema (TT)

```
capture_sequence: int64
event_id: int64
time_ms: int64
timestamp_recebimento_python: int64
timestamp_brt: datetime64[ns]
simbolo: string
origem: string
compradora: string
preco: float64
quantidade: int64
vendedora: string
agressor: string
agente_agressor: string
direcao: int8
```

---

## 七、特征清单 (165+ features)

### 7.1 T&T 特征 (trade_features)

| 特征 | 说明 |
|------|------|
| `aggr_imb` | 主动买卖 imbalance: (Vc-Vv)/(Vc+Vv) |
| `cvd_total` | 累计成交量差 |
| `cvd_div` | CVD-价格 divergent (divergence signal) |
| `ewma_imb_curta/media/longa` | 多时间尺度 EWMA imbalance |
| `delta_preco_janela` | 窗口内价格变动 |
| `hhi_compra/venda` | 机构集中度 (HHI) |
| `entropy_compra/venda` | 经纪人熵 |
| `vpin` | VPIN (知情交易概率) |
| `realized_vol_bps` | 已实现波动率 (bps) |
| `range_vol_bps` | Range 波动率 (bps) |
| `absorcao_ratio` | 吸收比 |
| `fluxo_persist` | 流量持续性 |
| `taxa_eventos` | 事件速率 (events/sec) |
| `fase_sessao` | 时段分类 |
| `dias_ate_venc` | 距到期天数 |

### 7.2 Book 特征 (book_features)

| 特征 | 说明 |
|------|------|
| `spread` | Bid-Ask 价差 |
| `microprice` | 加权微价格 (Stoikov) |
| `microprice_vs_mid` | 微价格偏离中点 |
| `imb_L1/L3/L5/L10/L20/L30/L50/L100/L200/L250/L500` | 各深度 imbalance |
| `hhi_book` | Book 集中度 |
| `ofi_total/ofi_ewma` | Order Flow Imbalance (Cont-Kukanov-Stoikov) |
| `micro_drift_bps/ewma` | 微价格漂移 |
| `imb_ponderado` | 深度加权 imbalance |
| `slope_bid/ask` | Book 斜率 (墙 vs 坡) |
| `vel_bid/ask_ewma` | 成交量变化速度 |
| `liq_dist_bid/ask` | 流动性距离 |
| `n_bid/ask_levels` | 有效挂单层数 |

### 7.3 跨资产特征 (cross_asset)

| 特征 | 说明 |
|------|------|
| `lag_ms` | WIN/WDO 时间滞后 (ms) |
| `corr_aggr` | 主动买卖相关性 |
| `corr_imb_book` | Book imbalance 相关性 |
| `divergencia` | 价格发散度 |
| `wdo_leading` | WDO 领先分数 |
| `resposta_win` | WIN 对 WDO 响应 |
| `wdo_delta` | WDO 瞬时变化率 |

### 7.4 机构上下文 (institutional_context)

| 特征 | 说明 |
|------|------|
| `dist_vwap_pts/norm` | 距 VWAP 距离 |
| `dist_abertura_pts/norm` | 距开盘价距离 |
| `dist_maxima/minima_pts/norm` | 距日高低点距离 |
| `dist_ajuste_pts/norm` | 距前日结算价距离 |
| `zona_vwap/abertura/maxima/minima/ajuste` | 区域 (0=远 1=近 2=在) |
| `posicao_relativa` | 日内相对位置 (0-1) |
| `amplitude_dia_pts` | 日内振幅 |
| `bounces_vwap/ajuste_norm` | 反弹计数 |
| `reversao_perto_*` | 临近反转信号 |

### 7.5 其他特征

| 特征 | 说明 |
|------|------|
| `kyle_kyle_lambda` | Kyle's Lambda (价格冲击) |
| `vp_poc/vah/val_dist` | Volume Profile 距离 |
| `vp_vp_total` | VP 总量 |
| `vpin` | VPIN |
| `vol_*` / `ret_*` | 多时间框架波动率和收益 |
| `session_*` | 时段正弦/余弦编码 |

---

## 八、ML 模型

### 8.1 模型架构

- **模型**: LightGBM (sklearn wrapper)
- **输入**: ~165 特征 (含交叉项)
- **输出**: 概率 P(下一30秒上涨)
- **训练数据**: 全历史 Parquet (~340万行)
- **验证**: Walk-forward 7d训练/1d测试

### 8.2 模型验证指标 (v950)

| 指标 | 值 |
|------|-----|
| Walk-forward AUC | 0.779 |
| Walk-forward Accuracy | 75.4% |
| Top feature | vp_vp_total (682) |
| Second | vpin (510) |

### 8.3 ML vs Heuristics 融合 (v11.13)

```
ML gate (阈值由 regime 和 ECE 校准):
  ├─ ML prob ≥ threshold → "有 edge", 进入评分
  └─ ML prob < threshold → "不确定区", 跳过

评分融合:
  ECE < 0.05 (ML 准): 权重 ML=0.7, 启发式=0.3
  ECE 0.05-0.15:       权重 ML=0.5, 启发式=0.5
  ECE > 0.15 (ML 不准): 权重 ML=0.3, 启发式=0.7
```

### 8.4 概率校准

- 每个 regime 独立阈值 (tendencia_alta: 0.65, lateral: 0.55, vol_alta: 0.70)
- ECE (Expected Calibration Error) 衡量校准质量
- Brier Score 衡量概率准确性
- 在线学习: 每个 trade 结果更新校准器

---

## 九、风险管理 (14 重保护)

| # | 保护 | 配置项 | 说明 |
|---|------|--------|------|
| 1 | Daily Loss Limit | `cb_nivel3_pnl` (-500) | 日亏损 >500pts 停止 |
| 2 | Max Exposure | `max_exposure_pts` (1000) | 最大敞口 |
| 3 | Max Position | `max_position_size` (5) | 最大持仓数 |
| 4 | Max Trades | `max_trades_dia` (15) | 日最大交易数 |
| 5 | Cooldown | `cooldown_entre_trades_s` (45) | 交易间隔 |
| 6 | Consecutive Loss | `cb_nivel1_perdas` (3) | 连续亏损保护 |
| 7 | Stale Data | `max_stale_data_s` (30) | 数据过时保护 |
| 8 | Spread Protection | `max_spread_pts` (WIN:30, WDO:3) | 价差保护 |
| 9 | Volatility Protection | `max_volatility_bps` (100) | 极端波动保护 |
| 10 | Model Unavailable | `tolerancia_sem_ml_s` (300) | ML 不可用容忍 |
| 11 | Confidence | `limiar_confirmacao` (0.50) | 最低置信度 |
| 12 | Session Protection | `horario_*` | 交易时段限制 |
| 13 | Kill Switch | `kill_switch_ativo` | 紧急停止 |
| 14 | Circuit Breaker | `cb_nivel*` | 三级断路器 (1/2/3级) |

---

## 十、动态 TP/SL 计算

```python
# 基于波动率 + regime + 置信度的动态调整
vol_adj_mult = vol_bps / 20.0  # 基准 20bps
tp = vol_p * 0.6 * tp_mult * vol_adj_mult
sl = vol_p * 0.4 * sl_mult * vol_adj_mult

# 置信度缩放
if confianca >= 0.8:  tp *= 1.2, sl *= 0.85
elif confianca >= 0.5:  # 不变
else:                 tp *= 0.8, sl *= 1.15

# 最小保护
tp = max(tp, 200 * tp_mult)
sl = max(sl, 150 * sl_mult)

# 扣除成本
tp -= custo_execucao
sl += custo_execucao
```

---

## 十一、学习系统

### 11.1 MFE/MAE 学习

每次交易完成后:
- 计算 MFE (最大有利变动) 和 MAE (最大不利变动)
- 对参与特征执行 **decay**: `新权重 = 旧权重 × 0.95` (floor = 30% 初始)
- 记录 acertos/erros 用于 feature death

### 11.2 Feature Death

- 任何特征在 40+ 样本后准确率 < 40% → 权重归零
- 支持按 regime 单独管理 (同一特征在 trend 中好但在 lateral 中差)

### 11.3 决策日志

每笔交易记录到 `decisoes_{session}.jsonl`:
- 时间戳、资产、操作类型、价格、score、confidence、ML probability
-  regime、TP/SL、原因、相关特征、模型版本

---

## 十二、配置文件

### 12.1 config.json (运行时)

```json
{
  "base_dir": "D:\\MarketData\\Profit",
  "save_dir": "D:\\MarketData\\mimo",
  "ml_modelo": "...modelo_lgbm_v5_otimizado.pkl",
  "ml_threshold": 0.6,
  "ativos": ["WINV26", "INDV26", "WDOU26", "DOLU26"],
  "ativo_principal": "WINV26",
  "ativo_contexto": "WDOU26",
  "cross_asset_pairs": [["WINV26", "INDV26"], ["WDOU26", "DOLU26"]],
  "rtd": {"book_linhas": 500, "tt_linhas": 500, "poll_s": 0.02, "max_janelas": 12},
  "trading": {"tp_pts": 100, "sl_pts": 50, "max_holding_s": 30, ...},
  "circuit_breaker": {"nivel1_perdas": 3, "nivel1_pnl": -100, ...},
  "position_sizing": {"target_risk_per_trade": 60, "max_position_size": 10},
  "exigir_replay_validado": false,
  "replay_min_pf": 1.2,
  "replay_min_wr": 0.45,
  "replay_max_dd_dia": 200.0
}
```

### 12.2 Config 优先级

```
环境变量的 FLAT_ 前缀 > config.json (嵌套/扁平) > ConfigCompleto defaults
```

---

## 十三、仪表盘 (Dashboard)

**HTTP Server**: `http://127.0.0.1:5001/`

| Endpoint | 内容 |
|----------|------|
| `/` | 仪表盘 HTML (dashboard_pro.html) |
| `/api/features` | 所有资产特征 + regime + OHLC |
| `/api/sinais` | 当前信号 (score/confianca/tp/sl/ml_prob) |
| `/api/posicao` | 当前持仓 + PnL + MFE/MAE |
| `/api/learning` | 学习统计 (PF, Sharpe, 权重) |
| `/api/memoria` | 全局计数器 (trades, CB 级别, 异常) |
| `/api/book` | Book 统计 (imbalance, thinning, absorbers) |
| `/api/book_level` | Book Level 特征 + 跨资产 |
| `/api/metricas` | 性能指标 |
| `/api/resumo` | 每资产摘要 |
| `/api/padroes` | 模式检测 (spoof, stop-hunt) |
| `/api/rtd_health` | RTD 连接状态 |
| `/api/capture_health` | 捕获守护进程健康状态 |
| `/api/saldo_corretoras` | 经纪商余额 |
| `/api/contexto` | VWAP, 结算价, 距离 |
| `/api/historico` | 时序数据 (最近 30 分钟) |
| `/api/all` | 所有数据聚合 |
| `/health` | 系统健康 |

---

## 十四、目录结构

```
C:/Freebuff/
├── run_motor.py                  # 主入口 (46L)
├── watchdog.py                   # 进程监控 (430L)
├── replay_engine.py              # 离线回放引擎 (451L)
├── motor_web.py                  # RTD 编排器 (1116L)
├── motor_rt_alphaz.py            # 兼容 shim (25L)
├── config.py                     # 配置代理 (13L)
├── config.json                   # 运行时配置
├── dashboard_pro.html            # 仪表盘前端
├── feature_registry.json         # 特征清单
├── run_all_tests.py              # 测试运行器 (704L)
│
├── core/                         # 领域核心 (16 文件, ~2800L)
│   ├── app.py                    # 或questrator + RTD 循环
│   ├── contracts.py              # 类型契约
│   ├── market_state.py           # 市场状态 (thread-safe)
│   ├── signal_engine.py          # 信号引擎
│   ├── position_manager.py       # 仓位管理
│   ├── risk_engine.py            # 14重风险管理
│   ├── risk_manager.py           # 风险管理辅助
│   ├── regime_detector.py        # 市场 regime 检测
│   ├── learning.py               # MFE/MAE 学习
│   ├── persistence.py            # 持久化 (JSONL)
│   ├── capture_daemon.py         # 不朽捕获守护进程
│   ├── calibration.py            # ML 概率校准
│   ├── metrics.py                # 绩效指标
│   ├── event_clock.py            # 主时钟
│   ├── decision_journal.py       # 决策日志
│   ├── leakage_test.py           # 泄露测试
│   └── utils.py                  # 工具函数
│
├── features/                     # 特征层 (20 文件, ~2000L)
│   ├── __init__.py
│   ├── feature_engine.py         # 1s 窗口聚合
│   ├── feature_registry.py       # 特征注册表 (165+ features)
│   ├── trade_features.py         # T&T 滚动窗口
│   ├── book_features.py          # Book 微结构
│   ├── cross_asset.py            # 跨资产引擎
│   ├── institutional_context.py  # 机构上下文
│   ├── volume_profile.py         # VP/POC/VAL/VAH
│   ├── vpin.py                   # VPIN
│   ├── kyle_lambda.py            # Kyle's Lambda
│   ├── percentil.py              # 百分位/范围/累积
│   ├── ewma_zscore.py            # EWMA Z-score
│   ├── patterns.py               # 市场操纵模式
│   ├── volatility.py             # 多时间框架波动率
│   ├── returns.py                # 多时间框架收益
│   ├── price_context.py          # 价格上下文
│   ├── session_time.py           # 时段编码
│   ├── poc_migration.py          # POC 迁移
│   ├── volume_relativo.py        # 相对成交量
│   ├── vwap_tracker.py           # 日内 VWAP
│   └── utils.py                  # 纯函数工具
│
├── adapters/                     # I/O 层 (12 文件, ~1600L)
│   ├── base.py                   # 抽象基类
│   ├── profit_rtd.py             # RTD 适配器
│   ├── rtd_connection.py         # COM 连接
│   ├── rtd_parser.py             # RTD 解析
│   ├── rtd_writer.py             # Parquet 写入
│   ├── file_storage.py           # JSONL 存储
│   ├── com_watchdog.py           # COM 心跳
│   ├── dashboard_server.py       # HTTP 服务器管理
│   └── dashboard/
│       ├── api.py                # HTTP 路由
│       ├── state.py              # 共享状态
│       └── handlers.py           # 请求处理
│
├── ml/                           # ML Pipeline (30 文件, ~8000L)
│   ├── labeler_vectorizado.py    # 标签生成
│   ├── dataset_builder.py        # 数据集构建
│   ├── scorer.py                 # 在线推理
│   ├── treino_lib.py             # 训练工具
│   ├── walk_forward*.py          # 时序验证
│   ├── retreinar_lgbm_limpo.py   # 模型重训练
│   ├── model_validation.py       # 模型验证
│   ├── validacao_rigorosa.py     # 严格验证
│   ├── ablation_test.py          # 消融实验
│   ├── feature_manifest.py       # 特征清单
│   ├── features_*.py             # 批量特征计算
│   ├── calibrar_modelo.py        # 概率校准
│   ├── replay_temporal.py        # 历史回放
│   └── model_*.py                # 模型元数据/注册
│
├── scripts/                      # 自动化 (9 文件)
├── testes/                       # 测试 (17 文件, ~4000L)
├── tests/                        # 额外测试 (1 文件)
├── docs/                         # 文档 (22+ 文件)
│   ├── ARCHITECTURE.md           # 架构文档
│   ├── COMPONENTS.md             # 组件参考
│   ├── DATA_PIPELINE.md          # 数据管道
│   ├── DATA_CONTRACTS.md         # 数据契约
│   ├── MIGRATION_PLAN.md         # 迁移计划
│   ├── ARCHITECTURE_ML_DECISION.md # ML 决策架构
│   ├── MACHINE_LEARNING.md       # ML 指标
│   └── archive/                  # 归档的旧版本
│
└── dados/                        # 数据结果目录
```

---

## 十五、版本演进

| 版本 | 日期 | 重大变更 |
|------|------|----------|
| v9.x | 2026-07 | 原始单体架构 (motor_rt_alphaz.py, 4154行) |
| v10.0 | 2026-08-26 | 架构分层完成 (core/features/adapters/ml) |
| v10.1 | 2026-08-26 | COM Watchdog 独立化 |
| v10.5 | 2026-08-27 | NumPy 向量化 (500级 book 加速) |
| v10.6 | 2026-08-27 | CVD 发散检测 |
| v11.0 | 2026-08-28 | CaptureDaemon (不朽线程) + CrossAssetManager |
| v11.10 | 2026-08-28 | 校准系统 (Calibration + Decision Separator) |
| v11.11 | 2026-08-28 | FeatureManifest (train↔prod parity) |
| v11.13 | 2026-08-28 | ML/Heuristics 动态融合 (ECE 自适应权重) |
| v11.14 | 2026-08-28 | Feature Death (只衰减不增强) |
| v11.15 | 2026-08-28 | ML 金字塔加仓 |
| v11.16 | 2026-08-28 | 交易后冷却期 |
| v12.0 | 2026-08-29 | RiskEngine v2 (14重保护) + Replay Gate |

---

## 十六、关键设计原则

1. **因果关系**: 所有特征从 ≤t 时刻计算, 无前向泄露
2. **线程安全**: MarketState 用 RLock, CaptureDaemon 用 Queue
3. **容错**: CaptureDaemon 不朽线程, RTD 自动重连, 异常不中断
4. **可审计性**: 决策日志 + 模型注册表 + 特征清单
5. **模块化**: 每个模块有明确职责, 通过 dataclass 契约通信
6. **离线优先**: 实时和批量使用相同特征计算逻辑
7. **ML 作为 gate**: ML 决定何时操作, 启发式决定方向
8. **动态 TP/SL**: 根据波动率和 regime 自适应调整
9. **Feature Death**: 持续衰减无效特征, 不创建新权重
