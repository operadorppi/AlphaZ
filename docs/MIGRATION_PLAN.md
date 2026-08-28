# Plano de Migração: Arquitetura em Camadas

> **Status**: Planejamento (não iniciado)
> **Data**: 26/08/2026
> **Pré-requisito**: Motor v9.50 funcional, 98 testes passando, pipeline v950 operacional
> **Princípio**: Migrar sem quebrar produção — cada fase é independente e reversível

---

## A. MAPA DA ARQUITETURA ATUAL

### A.1 Arquivos e tamanhos

| Arquivo | Linhas | Classes | Responsabilidade |
|---------|--------|---------|------------------|
| `motor_rt_alphaz.py` | 4.154 | 9 | Tudo: estado, features, scoring, trading, persistência, API, loop RTD |
| `motor_web.py` | 2.585 | 2 | Captura RTD (book+T&T), escritores Parquet, threads COM |
| `features_lib.py` | 982 | 8 | Trackers de microestrutura (VPIN, OFI, BookLevel, VP, Kyle) |
| `scorer.py` | 314 | 2 | ScorerML + VWAPTracker |
| `config.py` | 105 | 0 | Config flat |
| `treino_lib.py` | — | — | Utilitários de treino |
| **Trackers** (6 arquivos) | ~1.200 | 6 | preco_context, volatility, returns, session_time, poc_migration, volume_relativo |
| **ML pipeline** (29 arquivos) | ~8.000 | — | Labeler, dataset_builder, walk_forward, ablation, etc |
| **TOTAL** | ~18.400 | 27+ | |

### A.2 A classe `Analise` (linhas 1446-3317 = 1.871 linhas)

Esta classe é o "coração monolítico" — faz tudo:

```
Analise
├── Estado de mercado (historico, book, estados)
├── Features (_calcular, _calcular_sequencia)
├── Scoring (_avaliar, _suavizar_sinal)
├── Trading (gerenciar_posicao, _checar_saidas, _fechar_posicao)
├── Aprendizado (aprender_mfe_mae, _recalc_acuracia, carregar/salvar_aprendizado)
├── Regime (detectar_regime, ajustar_por_regime)
├── Persistência (_gravar_trade, _gravar_decisao, _flush_*, salvar_sessao)
├── API/Getters (get_features, get_sinais, get_posicao, get_estatisticas, etc)
├── Checkpoint (_carregar/salvar_posicao_checkpoint)
├── Book (alimentar_book, get_book_level, get_book_stats)
└── Métricas (calcular_metricas, get_resumo)
```

### A.3 A classe `App` (linhas 3427-4154 = 727 linhas)

```
App
├── Orquestração (run, _loop, _com_watchdog)
├── Conexão RTD (_reconectar, _sync_estados)
├── Dashboard HTML inline (html — 360 linhas de HTML/CSS)
├── Health check (get_rtd_health)
├── Contexto mercado (get_contexto_mercado)
└── Shutdown (parar)
```

### A.4 A classe `Handler` (linhas 3318-3426 = 108 linhas)

Roteamento HTTP — 15+ endpoints.

### A.5 `motor_web.py` (2.585 linhas)

```
motor_web
├── Conexão COM (_connect, _refresh, _criar_callback)
├── Parsing RTD (parse_dat, parse_refresh_data, _normalizar_simbolo)
├── Descoberta de ativos (descobrir_ativos_rtd, preparar_ativos)
├── Threads COM (thread_com, _thread_com_ciclo, _com_watchdog_writer)
├── Escritores Parquet (thread_escritora, thread_escritora_tt)
├── Schemas (enforce_schema, write_parquet_part)
├── Consolidação (consolidar_book_parquet, consolidar_tt_parquet)
├── Dashboard proxy (_DashboardState, _WebDashboardHandler)
├── Diagnóstico (_diag)
└── Cleanup (limpar_pasta)
```

### A.6 Dependências circulares conhecidas

```
motor_rt_alphaz.py
  → importa: scorer, features_lib, config, captura_eventos_ms
  → usa: EstadoAtivo, Analise, App, Handler (todas no mesmo arquivo)

motor_web.py
  → importa: config, features_lib (indireto)
  → comunicação com App via filas (Queue)

scorer.py
  → importa: config
  → usado por: App.__init__

features_lib.py
  → independente (baixo acoplamento) ✅

trackers (preco_context, volatility, etc)
  → independentes (baixo acoplamento) ✅
```

---

## B. ESTRUTURA ALVO

```
C:/Freebuff/
├── core/                        # Domínio do motor de trading
│   ├── __init__.py
│   ├── market_state.py          # EstadoAtivo + historico + book state
│   ├── event_clock.py           # Master clock, timestamps, virada de dia
│   ├── signal_engine.py         # _avaliar + _suavizar_sinal + scoring
│   ├── position_manager.py      # gerenciar_posicao + _fechar_posicao + _checar_saidas
│   ├── risk_manager.py          # Circuit breaker, cooldown, horário, TP/SL
│   ├── regime_detector.py       # detectar_regime + ajustar_por_regime
│   ├── learning.py              # aprender_mfe_mae + _recalc_acuracia + carregar/salvar
│   ├── persistence.py           # _gravar_trade + _gravar_decisao + _flush + checkpoint
│   ├── metrics.py               # calcular_metricas + get_estatisticas + get_resumo
│   └── app.py                   # App orquestrador (apenas composição, sem lógica)
│
├── features/                    # Cálculo de features (causal, sem estado de trading)
│   ├── __init__.py
│   ├── book_features.py         # BookLevelFeatures + OFITracker (de features_lib)
│   ├── trade_features.py        # JanelaFeatures + GeradorJanelas + VPIN
│   ├── volume_profile.py        # VolumeProfileTracker + POC + VAH/VAL
│   ├── cross_asset.py           # CrossAssetEngine (WIN×WDO)
│   ├── volatility.py            # volatility_tracker.py
│   ├── returns.py               # returns_tracker.py
│   ├── price_context.py         # preco_context_tracker.py
│   ├── session_time.py          # session_time_tracker.py
│   ├── poc_migration.py         # poc_migration_tracker.py
│   ├── volume_relativo.py       # volume_relativo_tracker.py
│   ├── kyle_lambda.py           # KyleLambdaTracker
│   ├── patterns.py              # PadroesMemoria + AccumulationTracker
│   ├── percentil.py             # PercentilTracker + RangeTracker
│   └── ewma_zscore.py           # EWMAZScore
│
├── adapters/                    # I/O e integração externa
│   ├── __init__.py
│   ├── profit_rtd.py            # motor_web.py (conexão COM, parsing, threads)
│   ├── file_storage.py          # captura_eventos_ms.py (escrita JSONL/Parquet)
│   ├── dashboard_api.py         # Handler (roteamento HTTP) — sem HTML inline
│   └── dashboard_html.py        # dashboard_pro.html (servido como arquivo estático)
│
├── ml/                          # Pipeline ML (já parcialmente organizado)
│   ├── __init__.py
│   ├── dataset.py               # dataset_builder + build_dataset_v950
│   ├── labeling.py              # labeler_vectorizado
│   ├── training.py              # treino_lib + lightgbm_tune
│   ├── validation.py            # walk_forward + validacao_rigorosa
│   ├── inference.py             # scorer.py (ScorerML)
│   ├── ablation.py              # ablation_test
│   ├── features_batch.py        # features_expansao + features_contexto_*
│   └── calibrar.py              # calibrar_modelo
│
├── config/                      # Configuração centralizada
│   ├── __init__.py
│   ├── settings.py              # config.py (flat → dataclass/pydantic)
│   └── config.json              # config.json
│
├── scripts/                     # Automação
├── testes/                      # Testes
├── dados/                       # Dados e resultados
└── docs/                        # Documentação
```

---

## C. CONTRATOS (INTERFACES) ENTRE CAMADAS

### C.1 Princípio fundamental

Cada camada conhece apenas a camada imediatamente abaixo. Nenhuma camada importa de `core/` exceto `app.py`.

```
app.py
  → core.market_state, core.signal_engine, core.position_manager, ...
  → adapters.profit_rtd, adapters.dashboard_api
  → features.* (via signal_engine)
  → ml.inference (via signal_engine)

signal_engine.py
  → features.* (recebe snapshot de features)
  → ml.inference (opcional, se modelo carregado)
  → core.regime_detector (para ajustar score)

position_manager.py
  → core.risk_manager (pergunta: pode abrir?)
  → core.persistence (grava trade)
  → core.learning (registra resultado)

adapters.profit_rtd
  → core.market_state (alimenta via filas)
  → adapters.file_storage (grava Parquet bruto)
```

### C.2 Contrato: `MarketState` (core/market_state.py)

```python
class MarketState:
    """Estado de mercado por ativo. Thread-safe via RLock."""

    def alimentar_negocio(self, ativo: str, neg: dict) -> None:
        """Adiciona um negócio ao histórico."""

    def alimentar_book(self, ativo: str, snap: dict) -> None:
        """Atualiza snapshot do book."""

    def get_historico(self, ativo: str, segundos: int = 1800) -> list:
        """Retorna negócios dos últimos N segundos."""

    def get_ultimo_preco(self, ativo: str) -> float:
        """Último preço negociado."""

    def get_book(self, ativo: str) -> dict:
        """Snapshot atual do book."""

    def snapshot(self, ativo: str) -> dict:
        """Snapshot completo para cálculo de features."""
```

### C.3 Contrato: `SignalEngine` (core/signal_engine.py)

```python
class SignalEngine:
    """Recebe features, produz sinais. Não conhece posição nem risco."""

    def avaliar(self, ativo: str, features: dict) -> Signal:
        """Retorna (lado, score, confianca, motivos, contrib)."""

    def suavizar(self, lado_bruto: str) -> str:
        """Aplica EWMA/suavização no sinal."""

@dataclass
class Signal:
    lado: str           # 'C', 'V', ''
    score: float         # 0.0 a 1.0
    confianca: float     # EWMA do score
    motivos: list[str]   # Razões do sinal
    contrib: dict        # Contribuição por feature
    horizonte: int      # Segundos
```

### C.4 Contrato: `PositionManager` (core/position_manager.py)

```python
class PositionManager:
    """Gere posições abertas. Não decide sinais nem risco."""

    def gerenciar(self, ativo: str, sinal: Signal, preco: float) -> Action | None:
        """Decide abrir/manter/fechar baseado no sinal + estado atual."""

    def checar_saidas(self, preco: float) -> ExitSignal | None:
        """Verifica TP/SL/reversão em tempo real."""

    def get_posicao(self) -> Position | None:
        """Posição atual ou None."""

@dataclass
class Action:
    tipo: str           # 'ABRIR', 'FECHAR', 'MANTER'
    lado: str           # 'C', 'V'
    preco: float
    tp: float
    sl: float
    motivo: str

@dataclass
class ExitSignal:
    preco: float
    motivo: str         # 'TP', 'SL', 'REVERSAO', 'TEMPO'
    pnl: float
```

### C.5 Contrato: `RiskManager` (core/risk_manager.py)

```python
class RiskManager:
    """Gate de risco. Responde apenas: pode? qual tamanho?"""

    def pode_abrir(self, ativo: str, lado: str) -> RiskDecision:
        """Verifica circuit breaker, cooldown, horário, drawdown."""

    def calcular_tp_sl(self, ativo: str, preco: float, lado: str,
                       vol_p: float, regime: str) -> tuple[float, float]:
        """Retorna (tp, sl) baseado em volatilidade e regime."""

    def registrar_resultado(self, pnl: float, motivo: str) -> None:
        """Atualiza contadores de circuit breaker."""

@dataclass
class RiskDecision:
    permitido: bool
    motivo: str         # 'OK', 'COOLDOWN', 'CIRCUIT_BREAKER', 'FORA_HORARIO'
    cooldown_restante: float
```

### C.6 Contrato: `Persistence` (core/persistence.py)

```python
class Persistence:
    """Gravação de trades, decisões e checkpoints."""

    def gravar_trade(self, neg: dict) -> None:
        """Adiciona ao buffer de trades (flush a 100MB)."""

    def gravar_decisao(self, dec: dict) -> None:
        """Adiciona ao buffer de decisões."""

    def salvar_checkpoint(self, posicao: Position | None) -> None:
        """Salva posição atual em disco."""

    def carregar_checkpoint(self) -> Position | None:
        """Restaura posição do último checkpoint."""

    def flush(self) -> None:
        """Força flush de todos os buffers."""
```

### C.7 Contrato: `ProfitRTD` (adapters/profit_rtd.py)

```python
class ProfitRTD:
    """Adaptador para conexão COM com ProfitChart RTD."""

    def conectar(self) -> bool:
        """Conecta ao servidor RTD."""

    def reconectar(self) -> bool:
        """Reconecta após queda."""

    def run(self, market_state: MarketState, storage: FileStorage,
            shutdown_event: threading.Event) -> None:
        """Loop principal: PumpEvents → RefreshData → alimentar market_state."""

    def get_health(self) -> dict:
        """Status da conexão RTD."""
```

### C.8 Contrato: `DashboardAPI` (adapters/dashboard_api.py)

```python
class DashboardAPI:
    """Roteamento HTTP. Sem HTML inline."""

    def handle(self, path: str, params: dict) -> tuple[str, bytes]:
        """Retorna (content_type, body) para o path dado."""

    # Endpoints:
    # /api/features → signal_engine snapshot
    # /api/posicao → position_manager.get_posicao()
    # /api/sinais → signal_engine.get_sinais()
    # /api/metricas → metrics.calcular()
    # /api/contexto → market_state.get_contexto()
    # /api/rtd_health → profit_rtd.get_health()
    # / → serve dashboard_pro.html estático
```

---

## D. CRONOGRAMA DE MIGRAÇÃO

### D.1 Princípios da migração

1. **Cada fase é independente** — pode parar após qualquer fase
2. **Nenhuma fase quebra o motor em produção** — versão atual continua rodando
3. **Testes após cada fase** — 98 testes devem passar
4. **Branch separada** — `refactor/layered-architecture`
5. **Coexistência** — arquivos novos e velhos coexistem até migração completa

### D.2 Fases

| Fase | Duração estimada | Risco | Reversível? |
|------|-------------------|-------|-------------|
| 0 — Preparação | 2h | Nenhum | Sim |
| 1 — Extrair features/ | 4h | Baixo | Sim |
| 2 — Extrair core/ (parte 1) | 8h | Médio | Sim |
| 3 — Extrair core/ (parte 2) | 8h | Médio | Sim |
| 4 — Extrair adapters/ | 6h | Médio | Sim |
| 5 — Refatorar App | 4h | Alto | Parcial |
| 6 — Cleanup e deprecação | 2h | Baixo | Sim |
| **Total** | **~34h (4-5 dias)** | | |

---

## E. PLANO PONTO-A-PONTO

### Fase 0 — Preparação (2h)

**Objetivo**: Criar estrutura de diretórios e branch sem tocar no código existente.

**Passos**:

1. Criar branch: `git checkout -b refactor/layered-architecture`
2. Criar diretórios:
   ```bash
   mkdir -p core features adapters config
   touch core/__init__.py features/__init__.py adapters/__init__.py config/__init__.py
   ```
3. Criar arquivo `core/contracts.py` com os dataclasses `Signal`, `Action`, `ExitSignal`, `RiskDecision`, `Position` (vazios, só tipos)
4. Rodar testes: `python -m pytest testes/ -q` → deve passar 98/98
5. Commit: `git commit -m "refactor: prepara estrutura de diretórios (fase 0)"`

**Checkpoint**: Estrutura criada, nada quebrado.

---

### Fase 1 — Extrair features/ (4h, risco baixo)

**Objetivo**: Mover classes de features para `features/`. Estas já são quase independentes.

**Passos**:

1. **`features/book_features.py`**: Mover `BookLevelFeatures` + `OFITracker` de `features_lib.py`
   ```python
   # features/book_features.py
   from features_lib import BookLevelFeatures, OFITracker  # re-export temporário
   ```
   Depois mover o código fisicamente.

2. **`features/trade_features.py`**: Mover `JanelaFeatures` + `GeradorJanelas` + `VPINTracker`

3. **`features/volume_profile.py`**: Mover `VolumeProfileTracker`

4. **`features/kyle_lambda.py`**: Mover `KyleLambdaTracker`

5. **`features/ewma_zscore.py`**: Mover `EWMAZScore`

6. **`features/volatility.py`**: Renomear `volatility_tracker.py` → `features/volatility.py`

7. **`features/returns.py`**: Renomear `returns_tracker.py` → `features/returns.py`

8. **`features/price_context.py`**: Renomear `preco_context_tracker.py` → `features/price_context.py`

9. **`features/session_time.py`**: Renomear `session_time_tracker.py` → `features/session_time.py`

10. **`features/poc_migration.py`**: Renomear `poc_migration_tracker.py`

11. **`features/volume_relativo.py`**: Renomear `volume_relativo_tracker.py`

12. **`features/cross_asset.py`**: Mover `CrossAssetEngine` de `motor_rt_alphaz.py` (linhas 1204-1445)

13. **`features/patterns.py`**: Mover `PadroesMemoria` de `motor_rt_alphaz.py` (linhas 349-697)

14. **`features/percentil.py`**: Mover `PercentilTracker` + `RangeTracker` de `motor_rt_alphaz.py` (linhas 956-1046)

15. Manter `features_lib.py` como shim: `from features.* import *` (compatibilidade)

16. Atualizar imports em: `scorer.py`, `motor_rt_alphaz.py`, `ml/*.py`

17. Rodar testes: `python -m pytest testes/ -q` → 98/98

**Checkpoint**: Features isoladas. `features_lib.py` vira compatibilidade.

**Como validar**: `python -c "from features.volatility import VolatilityTracker; print('OK')"`

---

### Fase 2 — Extrair core/ Parte 1: Estado + Persistência + Métricas (8h, risco médio)

**Objetivo**: Separar estado de mercado, persistência e métricas da classe `Analise`.

**Passos**:

1. **`core/market_state.py`**: Extrair de `Analise`:
   - `EstadoAtivo` (linhas 698-955 de motor_rt_alphaz)
   - `historico`, `estados`, `_book_map`
   - Métodos: `alimentar_lote`, `alimentar_book`, `get_historico`, `get_ultimo_preco`, `get_book_level`, `get_book_stats`
   - Adicionar `RLock` em todas as operações de `historico` e `features_por_seg`

   ```python
   # core/market_state.py
   class MarketState:
       def __init__(self):
           self._lock = threading.RLock()
           self.estados: dict[str, EstadoAtivo] = {}
           self.features_por_seg = OrderedDict()  # maxlen=7200

       def alimentar_lote(self, ativo, negocios, replay=False): ...
       def alimentar_book(self, ativo, snap, ...): ...
       def get_historico(self, ativo, segundos=1800): ...
       def get_ultimo_preco(self, ativo): ...
       def get_features_snapshot(self): ...
   ```

2. **`core/persistence.py`**: Extrair de `Analise`:
   - `_garantir_fp`, `_gravar_trade`, `_gravar_decisao`
   - `_rotacionar`, `_flush_trades`, `_flush_decisoes`
   - `_carregar_posicao_checkpoint`, `_salvar_posicao_checkpoint`
   - `salvar_sessao`

   ```python
   # core/persistence.py
   class Persistence:
       def __init__(self, save_dir: str, ativo: str):
           self._buf_trades = deque()
           self._buf_decisoes = deque()
           self._fp = None
           # ...

       def gravar_trade(self, neg): ...
       def gravar_decisao(self, dec): ...
       def salvar_checkpoint(self, posicao): ...
       def carregar_checkpoint(self) -> Position | None: ...
       def flush(self): ...
   ```

3. **`core/metrics.py`**: Extrair de `Analise`:
   - `calcular_metricas`, `get_estatisticas`, `get_resumo`

4. **`core/event_clock.py`**: Extrair lógica de master clock:
   - Virada de dia por ativo
   - Timestamps TOD → epoch
   - Reset de sessão

5. Manter `Analise` como façade: delega para `MarketState`, `Persistence`, `Metrics`

6. Rodar testes

**Checkpoint**: Estado, persistência e métricas isolados.

**Como validar**:
```python
from core.market_state import MarketState
from core.persistence import Persistence
ms = MarketState()
p = Persistence("D:/MarketData/mimo/26", "WINV26")
```

---

### Fase 3 — Extrair core/ Parte 2: Signal + Risk + Position + Regime + Learning (8h, risco médio)

**Objetivo**: Separar a lógica de decisão da classe `Analise`.

**Passos**:

1. **`core/regime_detector.py`**: Extrair de `Analise`:
   - `detectar_regime` (linha 1478)
   - `ajustar_por_regime` (linha 1533)
   - Cache de regime por N segundos (evita recomputação a cada tick)

2. **`core/signal_engine.py`**: Extrair de `Analise`:
   - `_calcular` (linha 423) — prepara features
   - `_calcular_sequencia` (linha 577)
   - `_avaliar` (linha 599) — scoring + pesos
   - `_suavizar_sinal` (linha 1180)
   - PESOS_INICIAIS (linhas 1294-1317)
   - `get_features`, `get_sinais`

   ```python
   # core/signal_engine.py
   class SignalEngine:
       def __init__(self, market_state: MarketState, regime: RegimeDetector,
                    scorer: ScorerML | None = None):
           self.state = market_state
           self.regime = regime
           self.scorer = scorer
           self.confianca_ewma = 0.0
           self.pesos = PESOS_INICIAIS.copy()

       def avaliar(self, ativo: str) -> Signal: ...
       def get_features(self) -> dict: ...
   ```

3. **`core/risk_manager.py`**: Extrair de `Analise`:
   - Circuit breaker (perdas consecutivas, drawdown, trades/dia)
   - `horario_permitido` (linha 1558)
   - Cooldown
   - Cálculo de TP/SL (linhas 2358-2366)
   - `_preco_plausivel` (linha 1210)

4. **`core/position_manager.py`**: Extrair de `Analise`:
   - `gerenciar_posicao` (linha 1313)
   - `_checar_saidas` (linha 1254)
   - `_fechar_posicao` (linha 1377)
   - `verificar_saidas_tempo_real` (linha 1201)
   - `get_posicao` (linha 1702)

   ```python
   # core/position_manager.py
   class PositionManager:
       def __init__(self, risk: RiskManager, persistence: Persistence,
                    learning: Learning):
           self.risk = risk
           self.persistence = persistence
           self.learning = learning
           self.posicao: Position | None = None

       def gerenciar(self, ativo, signal: Signal, preco) -> Action | None: ...
       def checar_saidas(self, preco) -> ExitSignal | None: ...
       def get_posicao(self) -> Position | None: ...
   ```

5. **`core/learning.py`**: Extrair de `Analise`:
   - `aprender_mfe_mae` (linha 1431)
   - `_recalc_acuracia` (linha 1473)
   - `carregar_aprendizado`, `salvar_aprendizado`
   - `resultados`, `previsoes`, `_zscore_trackers`

6. **`core/app.py`**: Refatorar `App` (linhas 3427-4154):
   - Compor: `MarketState`, `SignalEngine`, `PositionManager`, `RiskManager`, `RegimeDetector`, `Learning`, `Persistence`, `Metrics`
   - Loop RTD delega para `adapters.profit_rtd`
   - Dashboard delega para `adapters.dashboard_api`
   - Sem HTML inline

   ```python
   # core/app.py
   class App:
       def __init__(self, config: Config):
           self.state = MarketState()
           self.regime = RegimeDetector()
           self.learning = Learning()
           self.persistence = Persistence(config.save_dir, config.ativo)
           self.metrics = Metrics(self.state, self.learning)
           self.signal = SignalEngine(self.state, self.regime, scorer=config.scorer)
           self.risk = RiskManager(config)
           self.position = PositionManager(self.risk, self.persistence, self.learning)
           self.rtd = ProfitRTD(config)
           self.dashboard = DashboardAPI(self)

       def run(self):
           self.rtd.run(self.state, self.persistence, self._shutdown)
   ```

7. Rodar testes

**Checkpoint**: Toda a lógica de decisão está em `core/`. `Analise` vira façade vazia.

---

### Fase 4 — Extrair adapters/ (6h, risco médio)

**Objetivo**: Isolar I/O externo (RTD, arquivos, HTTP).

**Passos**:

1. **`adapters/profit_rtd.py`**: Mover `motor_web.py`:
   - `_connect`, `_refresh`, `_criar_callback`, `conectar_servidor`
   - `descobrir_ativos_rtd`, `preparar_ativos`
   - `parse_dat`, `parse_refresh_data`, `_normalizar_simbolo`
   - `thread_com`, `_thread_com_ciclo`, `_com_watchdog_writer`
   - `enforce_schema`, `write_parquet_part`
   - `consolidar_book_parquet`, `consolidar_tt_parquet`
   - `_diag`
   - Interface: `ProfitRTD.run(market_state, storage, shutdown_event)`

2. **`adapters/file_storage.py`**: Mover `captura_eventos_ms.py`:
   - `CapturaEventosMS` → `FileStorage`
   - Mesma interface, mesmo comportamento

3. **`adapters/dashboard_api.py`**: Extrair `Handler` de `motor_rt_alphaz.py`:
   - `do_GET` → `handle(path, params) -> (content_type, body)`
   - Sem HTML inline — serve `dashboard_pro.html` como arquivo estático
   - `_html`, `_json` → helpers de resposta

4. **`adapters/dashboard_html.py`**: O `dashboard_pro.html` já é arquivo separado — apenas garantir que é servido estaticamente.

5. Manter `motor_web.py` como shim: `from adapters.profit_rtd import *`

6. Rodar testes

**Checkpoint**: I/O isolado. Adapters podem ser mockados em testes.

---

### Fase 5 — Refatorar App e remover shims (4h, risco alto)

**Objetivo**: Remover arquivos antigos, atualizar entrypoints.

**Passos**:

1. Atualizar `scripts/iniciar_motor.bat`:
   ```bat
   python -m core.app
   ```

2. Atualizar `config.py` → `config/settings.py`:
   - Manter interface flat (`CONFIG = load_config()`)
   - Internamente usar dataclass ou pydantic

3. Remover shims:
   - `features_lib.py` → `from features.* import *`
   - `motor_web.py` → `from adapters.profit_rtd import *`
   - Deletar `Analise` (substituída por composição em `App`)
   - `motor_rt_alphaz.py` → apenas `from core.app import App; App().run()`

4. Atualizar imports em todos os arquivos `ml/*.py`, `testes/*.py`

5. Atualizar `watchdog.py` para apontar para `core.app`

6. Rodar testes completos: `python -m pytest testes/ -q`

7. Rodar motor em modo replay para validar

**Checkpoint**: Arquitetura final. Arquivos antigos são apenas entrypoints de 1 linha.

---

### Fase 6 — Cleanup e deprecação (2h, risco baixo)

**Passos**:

1. Deletar `__pycache__` antigo
2. Atualizar `docs/FILE_INVENTORY.csv`
3. Atualizar `docs/COMPONENTS.md`
4. Atualizar `docs/ARCHITECTURE.md` com novo diagrama
5. Atualizar `docs/CHANGELOG.md`: `v10.0 — arquitetura em camadas`
6. Mover arquivos `.py` antigos para `docs/archive/`
7. Rodar testes finais
8. Merge da branch `refactor/layered-architecture` → `main`

---

## F. RISCOS E MITIGAÇÕES

| Risco | Probabilidade | Impacto | Mitigação |
|-------|-------------|---------|-----------|
| Estado compartilhado quebra ao separar | Alta | Alto | RLock em todo `MarketState`; testes após cada extração |
| `Analise` tem `self.*` implícitos | Alta | Médio | Buscar todos os `self.` antes de mover; grep exaustivo |
| Imports circulares ao separar | Média | Médio | Usar injeção de dependência (construtor), não import direto |
| Dashboard para de funcionar | Baixa | Alto | `dashboard_pro.html` já é arquivo separado; só mudar quem serve |
| Performance muda | Baixa | Médio | Benchmark antes/depois do `_loop` (latência por tick) |
| Testes quebram por paths | Média | Baixo | Atualizar `sys.path` ou usar `PYTHONPATH=.` nos testes |
| Watchdog não acha processo | Baixa | Alto | Atualizar `watchdog.py` antes de mudar entrypoint |

---

## G. BENCHMARK DE PERFORMANCE

Antes de iniciar a migração, medir:

```bash
# Latência do loop principal (deve ser < 20ms por tick)
python -c "
import time
# Medir tempo de _avaliar com 1000 chamadas
# Registrar baseline
"

# Uso de memória
python -c "import psutil; print(psutil.Process().memory_info().rss / 1e6, 'MB')"
```

Após cada fase, re-medir. Se latência aumentar > 50%, investigar.

---

## H. CRITÉRIOS DE SUCESSO

| Critério | Como medir |
|----------|-----------|
| 98 testes passam | `pytest testes/ -q` |
| Latência não aumenta | Benchmark antes/depois |
| Motor opera em produção | Pregão completo sem crash |
| Replay determinístico | `python -m core.app --replay < data.jsonl` |
| Dashboard funciona | `curl localhost:5001/api/features` |
| Pipeline ML funciona | `python ml/walk_forward.py` |
| RTD conecta | Log mostra "RTD conectado" |

---

## I. ORDEM RECOMENDADA DE EXECUÇÃO

```
Semana 1:
  Dia 1: Fase 0 (2h) + Fase 1 início (2h)
  Dia 2: Fase 1 fim (2h) + Fase 2 (6h)
  Dia 3: Fase 3 (8h)

Semana 2:
  Dia 4: Fase 4 (6h) + Fase 5 início (2h)
  Dia 5: Fase 5 fim (2h) + Fase 6 (2h)

Total: ~30h em 5 dias (com margem de 4h para imprevistos)
```

---

## J. O QUE NÃO MUDAR

| Item | Razão |
|------|-------|
| `config.json` | Funciona, não mexer |
| `dashboard_pro.html` | Já é arquivo separado, só mudar quem serve |
| `ml/` (pipeline) | Já organizado, apenas atualizar imports |
| `testes/` | Já organizado, apenas atualizar imports |
| `scripts/` (bat files) | Apenas atualizar entrypoint na fase 5 |
| `dados/` | Estrutura de dados não muda |
| Protocolo walk-forward | Não alterar datas/embargo/label |
| Labeler | Não alterar |
| TP/SL | Não alterar (precisa de mais dados primeiro) |

---

## K. CHECKLIST POR FASE

### Fase 0 — Preparação
- [ ] Branch criada
- [ ] Diretórios criados
- [ ] `core/contracts.py` com dataclasses
- [ ] Testes passam (98/98)
- [ ] Commit

### Fase 1 — features/
- [ ] 12 arquivos movidos para `features/`
- [ ] `features_lib.py` vira shim
- [ ] `scorer.py` imports atualizados
- [ ] `motor_rt_alphaz.py` imports atualizados
- [ ] `ml/*.py` imports atualizados
- [ ] Testes passam
- [ ] Commit

### Fase 2 — core/ (estado + persistência + métricas)
- [ ] `core/market_state.py` criado
- [ ] `core/persistence.py` criado
- [ ] `core/metrics.py` criado
- [ ] `core/event_clock.py` criado
- [ ] `Analise` delega para os novos módulos
- [ ] RLock em `MarketState`
- [ ] Testes passam
- [ ] Commit

### Fase 3 — core/ (signal + risk + position + regime + learning)
- [ ] `core/regime_detector.py` criado
- [ ] `core/signal_engine.py` criado
- [ ] `core/risk_manager.py` criado
- [ ] `core/position_manager.py` criado
- [ ] `core/learning.py` criado
- [ ] `core/app.py` refatorado (composição)
- [ ] `Analise` vira façade vazia
- [ ] Testes passam
- [ ] Commit

### Fase 4 — adapters/
- [ ] `adapters/profit_rtd.py` criado (de motor_web.py)
- [ ] `adapters/file_storage.py` criado (de captura_eventos_ms.py)
- [ ] `adapters/dashboard_api.py` criado (de Handler)
- [ ] `motor_web.py` vira shim
- [ ] Testes passam
- [ ] Commit

### Fase 5 — Refatorar App
- [ ] Entrypoint atualizado (`python -m core.app`)
- [ ] `config/settings.py` criado
- [ ] Shims removidos
- [ ] Imports atualizados em todos os arquivos
- [ ] `watchdog.py` atualizado
- [ ] Testes passam
- [ ] Motor roda em replay
- [ ] Commit

### Fase 6 — Cleanup
- [ ] `__pycache__` limpo
- [ ] `docs/FILE_INVENTORY.csv` atualizado
- [ ] `docs/COMPONENTS.md` atualizado
- [ ] `docs/ARCHITECTURE.md` atualizado
- [ ] `docs/CHANGELOG.md` atualizado (v10.0)
- [ ] Arquivos antigos movidos para `docs/archive/`
- [ ] Testes finais passam
- [ ] Merge para main
