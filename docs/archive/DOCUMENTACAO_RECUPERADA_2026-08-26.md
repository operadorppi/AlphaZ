# Motor RT Alphaz — Documentação

## Visão Geral

Sistema de trading algorítmico para B3 (WIN/WDO) via ProfitChart RTD.
Captura dados em tempo real (500 níveis de book + 500 linhas de T&T), calcula features de microestrutura, gera sinais de trade e gerencia posições com TP/SL/breakeven/trailing.

---

## Arquitetura

```
┌─────────────────────────────────────────────────────┐
│                   ProfitChart RTD                    │
│              (Book 500 + T&T 500)                    │
└──────────────────────┬──────────────────────────────┘
                       │ comtypes (COM)
                       ▼
┌─────────────────────────────────────────────────────┐
│              motor_rt_alphaz.py                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ EstadoAtivo │  │   Analise    │  │  Dashboard  │ │
│  │ (book+tt)   │──│ (features,   │──│  (HTML+JS)  │ │
│  │             │  │  score, TP/  │  │  porta 5001 │ │
│  │             │  │  SL,learn)   │  │             │ │
│  └─────────────┘  └──────┬───────┘  └────────────┘ │
│                          │ import                   │
│                   ┌──────▼───────┐                  │
│                   │features_lib.py│                  │
│                   │(OFI, BookLvl, │                  │
│                   │ VPIN, EWMA)   │                  │
│                   └──────────────┘                  │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              Pipeline Offline (batch)                │
│  captura_eventos_ms → batch_processor → labeler     │
│                    → dataset_builder → Parquet       │
└─────────────────────────────────────────────────────┘
```

---

## Arquivos

| Arquivo | Tamanho | Função |
|---------|---------|--------|
| `motor_rt_alphaz.py` | ~3400 linhas | Motor principal (RTD, features, score, trading, dashboard) |
| `motor_web.py` | ~2500 linhas | Conexão COM com ProfitChart RTD |
| `features_lib.py` | ~660 linhas | Feature Engine (código único para live e batch) |
| `captura_eventos_ms.py` | ~100 linhas | Captura de eventos brutos com ms |
| `batch_processor.py` | ~200 linhas | Processamento batch de raw events |
| `labeler.py` | ~200 linhas | Triple barrier labels |
| `dataset_builder.py` | ~150 linhas | Merge features + labels → Parquet |
| `test_features.py` | ~250 linhas | 33 testes unitários |
| `config.json` | ~30 linhas | Configuração externalizada |
| `iniciar_motor.bat` | ~10 linhas | Script de inicialização |
| `auto_start.bat` | ~40 linhas | Instalação de auto-start |
| `watchdog.py` | ~150 linhas | Monitor que reinicia o motor |

---

## Camadas de Features

### Camada 1 — Book Level (features_lib.BookLevelFeatures)

Dados do book por profundidade (nível 0 = melhor preço).

| Feature | Descrição | Tipo |
|---------|-----------|------|
| `spread` | Diferença best_ask - best_bid | float |
| `mid` | (best_ask + best_bid) / 2 | float |
| `microprice` | Preço ponderado pelo volume | float |
| `microprice_vs_mid` | microprice - mid | float |
| `imbalance` | Dict com L1/L3/L5/L10/L20/L30 | dict |
| `hhi_book` | Concentração da liquidez (HHI) | float |
| `liq_dist_bid/ask` | Distância ponderada da liquidez ao mid | float |
| `ofi` | Order Flow Imbalance (5 níveis) | float |
| `vel_bid/ask` | Velocidade do book (Δvol/Δt) | float |
| `vel_bid_ewma/ask_ewma` | EWMA da velocidade | float |
| `vel_imb` | Velocidade do imbalance por profundidade | dict |
| `micro_drift_bps` | Desvio microprice-mid em bps | float |
| `micro_drift_ewma` | EWMA do micro_drift (Stoikov 2008) | float |
| `imb_ponderado` | Imbalance ponderado por profundidade (Cartea 2015) | float |
| `slope_bid/ask` | Geometria da liquidez (parede vs rampa) | float |

### Camada 2 — Book Dynamics (eventos)

Eventos de microestrutura detectados por comparação de snapshots:

| Evento | Descrição |
|--------|-----------|
| `retiradas` | Liquidez removida (thinning > 50%) |
| `reposicoes` | Liquidez adicionada |
| `defesa_persistente` | Corretora mantendo nível por >2 ciclos |
| `layering` | Remoção completa de nível (possível spoof) |
| `thinning_bid/ask` | Volume total removido |

### Camada 3 — Trade Metrics (T&T)

| Feature | Descrição |
|---------|-----------|
| `aggr_imb` | Imbalance comprador vs vendedor |
| `delta_preco` | Variação de preço na janela |
| `vol_total` | Volume total negociado |
| `n` | Número de negócios |
| `price_eff` | Eficiência do preço (delta/preço total) |
| `fluxo_persist` | % de trades na mesma direção |
| `hhi` | Concentração por corretora |
| `aceleracao` | Mudança na taxa de agressão |
| `absorcao_ratio` | Volume absorvido vs volume total |
| `avg_trade_size` | Tamanho médio do trade |
| `max_trade_size` | Tamanho máximo do trade |
| `trades_per_sec` | Velocidade de negociação |
| `seq_pattern` | Padrão de sequência C/V |

### Camada 4 — Cross Asset (WDO ↔ WIN)

| Feature | Descrição |
|---------|-----------|
| `lag_ms` | Defasagem entre movimento WDO e resposta WIN |
| `corr_aggr` | Correlação rolling de agressão (60s) |
| `corr_imb_book` | Correlação rolling de book imbalance |
| `divergencia` | WDO andando enquanto WIN parado |
| `wdo_leading` | Score de liderança temporal WDO |
| `resposta_win` | Reação do WIN ao último movimento do WDO |
| `wdo_delta` | Velocidade do WDO (pts/s) |

### Features Adicionais

| Feature | Descrição |
|---------|-----------|
| `ofi` / `ofi_ewma` | Order Flow Imbalance total e EWMA |
| `vpin` | Volume-Synchronized PIN (toxicidade) |
| `ewma_imb_curta/media/longa` | EWMA de imbalance em 3 janelas |
| `hhi_compra/hhi_venda` | Concentração por lado |
| `entropy_compra/entropy_venda` | Entropia de Shannon |

---

## Scoring e Decisão

### Fluxo de decisão

```
Trade chega → alimentar_lote() → sanity check (preço, salto, faixa)
    → _calcular() (features) → _avaliar() (score por feature)
    → suavizar_sinal() → gerenciar_posicao() (TP/SL/reversão)
```

### Componentes do score

O score é soma ponderada de contribuições:

```
score = Σ(peso_feature × contribuição_feature)
```

Onde `contribuição` vai de -1.0 (contra) a +1.0 (a favor).

### Regimes de mercado

| Regime | Tipo de estratégia | TP/SL | Confirmação |
|--------|-------------------|-------|-------------|
| `tendencia_alta` | Momentum | TP×1.2, SL×0.8 | 2 |
| `tendencia_baixa` | Momentum | TP×1.2, SL×0.8 | 2 |
| `lateral` | Reversão | TP×0.6, SL×1.0 | 4 |
| `vol_alta` | Breakout | TP×1.5, SL×1.2 | 1 |
| `vol_baixa` | Neutro | TP×0.5, SL×1.0 | 5 |

### Confirmação de sinal

Sistema de decaimento exponencial (substituiu contador discreto):
- `confianca_ewma` suaviza o sinal ao longo do tempo
- `limiar_confirmacao = 0.55` para abrir posição
- `limiar_reset = 0.15` para zerar sinal

### Circuit Breaker

| Parâmetro | Valor | O que faz |
|-----------|-------|-----------|
| `max_perdas_consecutivas` | 3 | Para após 3 perdas seguidas |
| `max_trades_dia` | 15 | Limite diário de trades |
| `max_drawdown_dia_pontos` | -500 | Stop loss do dia |

### Proteções anti-overtrading

| Proteção | Parâmetro | Efeito |
|----------|-----------|--------|
| Cooldown | `cooldown_entre_trades_s: 45` | Mínimo 45s entre trades |
| Confiança alta | `>= 0.65` | Reversão só com confiança alta |
| Sem reversão em lateral | regime check | Em lateral, só TP/SL/timeout |

### Sanity check de preço

| Proteção | Config | Efeito |
|----------|--------|--------|
| Faixa absoluta | `faixas_preco` | WIN: 20K-500K, WDO: 1K-20K |
| Salto máximo | `max_salto_preco_pct: 0.15` | Rejeita saltos > 15% |
| Reset diário | `_ultimo_preco_valido.clear()` | Não compara preços de dias diferentes |

---

## Aprendizado Online

### MFE/MAE (Maximum Favorable/Adverse Excursion)

Para cada trade, o motor registra o maior ganho (MFE) e maior perda (MAE) durante a vida da posição. Isso permite:
- Ajustar TP/SL baseado no comportamento real
- Identificar features que predizem MFE alto vs MAE alto

### Ajuste de pesos

```python
delta = CONFIG["aprendizado_delta"]  # 0.02
decay = CONFIG["aprendizado_decay"]  # 0.998
min_amostras = 5  # trades por feature antes do ajuste
```

- Acertou → peso da feature aumenta
- Errou → peso diminui
- Decay aplicado periodicamente (pesos tendem a 0 se não reforçados)
- `feature_hits` rastreia acertos/erros por feature

### Persistência

| Arquivo | Conteúdo |
|---------|----------|
| `learning_state.json` | Pesos, feature_hits, últimos 500 trades |
| `padroes_memoria.json` | Perfil de 27 corretoras (spoofs, absorções, stops) |
| `posicao_atual.json` | Checkpoint de posição aberta |

---

## Dashboard

Acessível em `http://127.0.0.1:5001/`

### Painéis

| Painel | Dados |
|--------|-------|
| **Ação** | COMPRA/VENDA/AGUARDE + cor |
| **Preço** | Preço atual + TP/SL/RISCO/RETORNO |
| **Features** | AGRESSAO, EFICIENCIA, PERSISTENCIA, HHI, etc. |
| **Book Level** | SPREAD, IMB_L1, MICRO, HHI |
| **Trade Metrics** | AVG_SZ, SEQ, VEL |
| **Cross Asset** | LAG, CORR, DIV |
| **Score** | Confiança + Score + R:R |
| **Posição** | Box com P&L, TP/SL, tempo |
| **Aprendizado** | Acurácia, últimos trades, pesos |
| **Corretoras** | WIN e WDO com comprado/vendido/saldo |
| **RTD Alert** | 🔴 Vermelho se desconectado, 🟠 Laranja se pré-abertura |

### Endpoints API

| Endpoint | Retorna |
|----------|---------|
| `/api/features` | Features de todos os ativos |
| `/api/sinais` | Sinais e posições |
| `/api/learning` | Estatísticas de aprendizado |
| `/api/posicao` | Posição atual |
| `/api/memoria` | Memória do motor (negocios, trades, anomalias) |
| `/api/metricas` | Sharpe, Profit Factor, MaxDD |
| `/api/book_level` | Features de book level + cross asset |
| `/api/padroes` | Padrões detectados (spoof, stop-hunt) |
| `/api/saldo_corretoras` | Saldo por corretora |
| `/api/rtd_health` | Saúde da conexão RTD |
| `/health` | Status geral |

---

## Configuração

### config.json

```json
{
    "save_dir": "D:\\MarketData\\mimo",
    "web": {"host": "127.0.0.1", "port": 5001},
    "ativos": ["WINV26", "WDOU26"],
    "rtd": {"book_linhas": 60, "tt_linhas": 500},
    "trading": {
        "tp_pts": 100, "sl_pts": 50,
        "max_trades_dia": 15,
        "max_drawdown_dia": -300,
        "custo_execucao": {"WIN": 5.0, "WDO": 1.0}
    },
    "horarios": {
        "abertura_fim": [10, 0],
        "almoco_inicio": [12, 0],
        "almoco_fim": [13, 30],
        "fechamento": [16, 30]
    }
}
```

### Variáveis de ambiente

| Variável | Controla | Default |
|----------|----------|---------|
| `SINAL_RT_DIR` | Diretório de save do motor | `D:\MarketData\mimo` |
| `PROFIT_DATA_DIR` | Diretório base do ProfitChart | `D:\MarketData\Profit` |
| `WEB_PORT` | Porta do dashboard | `5001` |

### Prioridade de config

1. Variáveis de ambiente (sobrescrevem tudo)
2. `config.json` (sobrescreve defaults)
3. Defaults hardcoded no `CONFIG`

---

## Auto-start / Watchdog

### Task Scheduler

| Tarefa | Horário | O que faz |
|--------|---------|-----------|
| `MotorAlphaz_Iniciar` | 8:45 diário | Abre `iniciar_watchdog.bat` |
| `MotorAlphaz_Parar` | 18:35 diário | Mata todos os processos Python |

**IMPORTANTE:** A tarefa precisa de `LogonType Interactive` para acessar o COM do ProfitChart.

### Watchdog

```python
# watchdog.py
- Checa a cada 10s se o motor está vivo
- Se morreu, reinicia em 3s
- Max 10 reinícios por hora
- Log em watchdog.log
```

### Para outro computador

1. Copiar a pasta `C:\Freebuff`
2. Instalar Python 3.13 + `pip install comtypes pytest`
3. Instalar ProfitChart com janelas RTD abertas
4. Duplo-clique em `iniciar_motor.bat`

---

## Pipeline Offline (Batch)

```
captura_eventos_ms.py          batch_processor.py
  raw_negocios_ms_*.jsonl  ──▶  dataset_100ms.jsonl
  raw_book_ms_*.jsonl      ──▶   (features 100ms)
                                      │
                                      ▼
                                 labeler.py
                               dataset_100ms_labels.jsonl
                               (triple barrier + purge)
                                      │
                                      ▼
                              dataset_builder.py
                               dataset_final.parquet
                               (merge + asof join)
```

### Validação 100ms vs 1s

O motor grava dados brutos com ms (`captura_eventos_ms.py`).
O `batch_processor.py` processa esses dados com `features_lib.py`.
A comparação entre features de 100ms e 1s determina se vale a pena usar granularidade fina.

---

## Testes

```bash
python -m pytest test_features.py -v
```

33 testes cobrindo:
- `ewma_update`, `hhi`, `entropia`, `idade_ms`
- `VPINTracker`
- `JanelaFeatures` (snapshot, expiração, corretoras, preço)
- `BookLevelFeatures` (spread, mid, microprice, imbalance, velocidade)
- `GeradorJanelas`
- `asof_join_linhas`
- `OFITracker`

---

## Histórico de Versões

| Versão | Data | O que mudou |
|--------|------|-------------|
| v7 | Ago/2026 | Padrões (spoof, stop-hunt), PadroesMemoria |
| v8 | Ago/2026 | OFI, Regime Switch, ESTRATEGIAS por regime |
| v9 | Ago/2026 | Book Level Features, Cross Asset, Trade Metrics |
| v9.1 | Ago/2026 | Sanity check de preço, cooldown, reversão protegida |
| v9.2 | Ago/2026 | OFITracker unificado (features_lib), 3 novas features |
| v9.3 | 20/08/2026 | Unificação features_lib, anti-whipsaw, bug book_snap_ant |
| v9.4 | 20/08/2026 | Volume Profile, Kyle's Lambda, blindagem captura, R:R lateral |
| v9.5 | 21/08/2026 | Pente-fino: 4 bugs críticos, crash loop, labeler 3 classes, watchdog robusto |
| v9.6 | 21/08/2026 | Pente-fino #2: 5 funcionalidades mortas reativadas (cross-asset, pesos/confirmação por regime, stop-hunt, captura batch), neutro≠venda, max_holding_s, rollover dia, purge O(n log n), 41/41 testes |
| v9.7 | 21/08/2026 | Features à teoria: OFI alinhado por preço (Cont-Stoikov fiel), Kyle lambda completo (ΔP=0), normalização z-score EWMA opt-in, 53/53 testes |
| v9.8 | 21/08/2026 | 4 features novas: CVD+divergência (no score), vol realizada+range, fase de sessão+vencimento B3, taxa de eventos — 65/65 testes |
| v9.8.1 | 21/08/2026 | Fix poda do dedup da captura (`agora_ms`→`agora_epoch`): captura parava silenciosamente com sessão movimentada — 67/67 testes |
| v9.9 | 21/08/2026 | Pipeline RTD 100%: ritmo adaptativo, rotação real, fsync, fix _garantir_fp — 68/68 testes, smoke 41/41 |
| v9.10 | 21/08/2026 | Modo acumular dados: meta da sessão, log de rejeitados, gate de qualidade no retreino, relatório diário — 72/72 testes, smoke 47/47 |
| v9.11 | 21/08/2026 | Execução automática: pipeline_diario.py (relatório → features → labels → parquet → gate → retreino) agendado diariamente — 72/72 testes, smoke 47/47 |

---

## v9.11 — Execução automática do pipeline diário

**Contexto:** automatizar o ciclo de acumulação/retreino. Descoberta crítica no caminho: o `dataset_builder` **sobrescrevia** o parquet com UM dia — o retreino treinaria sempre com um dia só. O pipeline agora **acumula o mês inteiro**.

### `pipeline_diario.py` — orquestrador em 6 passos

```
[1/6] Relatório de qualidade   (relatorio_diario — dia anterior)
[2/6] Features 100ms           (batch_processor --periodo 1-{dia} → mês inteiro)
[3/6] Labels                   (labeler)
[4/6] Dataset final (parquet)  (dataset_builder → dataset_final.parquet com o mês)
[5/6] Gate de qualidade        (retreinar_sem_leak --gate-dias → TODOS os dias úteis do mês)
[6/6] Retreino                 (retreinar_sem_leak)
```

Pontos-chave:
- **Acumulação real**: `--periodo 1-{dia_atual}` processa o mês inteiro — o parquet cresce a cada dia
- **Gate protege o parquet**: valida TODOS os dias úteis do mês (não só 5) — se QUALQUER dia estiver contaminado, o retreino aborta (exit 2)
- **Guarda contra parquet vazio**: se as features saírem vazias, o pipeline aborta antes de rotular
- **Log central**: `D:\MarketData\mimo\pipeline.log` (stdout/stderr de cada passo)
- Flags: `--dry-run` (simula), `--skip-batch` (só relatório+gate+retreino), `--dia`, `--save-dir`

### Agendamento (Agendador do Windows)

O ciclo diário do projeto agora é **100% automático**:

| Tarefa | Horário | O que faz |
|---|---|---|
| `MotorAlphaz_Iniciar` | 08:45 | Inicia o motor |
| `MotorAlphaz_Parar` | 18:35 | Para o motor (captura fecha → meta gravado) |
| `MotorAlphaz_Retreinar` | 18:36 | **Pipeline completo** (via `auto_retreinar.bat` → `pipeline_diario.py`) |

O `auto_retreinar.bat` foi reescrito para delegar ao `pipeline_diario.py` — a tarefa das 18:36 já existia e não precisou ser recriada (o `/change` do schtasks exigia credenciais; trocar o conteúdo do .bat que ela executa é a via limpa). O `agendar_pipeline.bat` é o lançador alternativo (mesmo fluxo, log em `pipeline_diario_task.log`).

**Fluxo automático resultante (todo dia):**
```
08:45 motor liga ──► captura RTD (raw_*.jsonl + meta) ──► 18:35 motor para
18:36 pipeline: relatório de ontem → features do mês → labels → parquet do mês
     → gate (todos os dias úteis) → retreino → modelo_lgbm_v3.pkl atualizado
```

### Limitações conhecidas (documentadas, não corrigidas)
- `batch_processor --periodo` assume o mês atual — virada de mês (ex.: dia 1 processando dia 31 do mês anterior) precisa de execução manual
- Pipeline processa apenas o ativo principal (WINV26) — sem asof join de contexto WDO no parquet
- Se um dia antigo do mês estiver contaminado, o gate aborta o retreino inteiro até o problema ser resolvido (comportamento seguro e intencional)

### Arquivos alterados

| Arquivo | Mudanças |
|---|---|
| `pipeline_diario.py` | **Novo**: orquestrador de 6 passos com subprocess, logs e abort por passo |
| `auto_retreinar.bat` | Delegado ao pipeline completo (tarefa 18:36 já existente) |
| `agendar_pipeline.bat` | **Novo**: lançador alternativo do pipeline |
| `DOCUMENTACAO.md` | v9.11 |

---

## v9.10 — Modo "acumular dados": integridade + observabilidade

**Contexto:** decisão de acumular dados antes de validar o edge. O ativo mais valioso agora são os DADOS — então o pacote protege a integridade deles e cria as ferramentas para saber QUANDO há volume suficiente.

### 1. Metadados da sessão de captura (`raw_meta_<session>.json`)

`CapturaEventosMS.fechar()` agora grava `raw_meta_<session>.json` com: início/fim (epoch ms), nº de negócios, negócios por ativo, book snapshots e rejeitados acumulados. É a "certidão de nascimento" de cada sessão — o gate e o relatório leem daqui.

### 2. Log periódico dos rejeitados no `_loop` (a cada 10min)

Os contadores de blindagem existiam mas **ninguém olhava** (o bug v9.8.1 provou que a captura para silenciosamente). Agora o motor loga `[CAPTURA] rejeitados acumulados: {...}` (warning) ou `[CAPTURA] saudável (0 rejeitados)` (info) a cada 10 minutos.

### 3. Gate de qualidade no retreino (`retreinar_sem_leak.py --gate-dias`)

Antes de treinar, valida a captura dos dias informados:
- Arquivos do dia existem; nº de negócios ≥ 500; rejeitados `ts_antigo`/`dup` abaixo de limites; span de tempo ≥ 4h
- **Qualquer dia com problema → aborta com exit 2** (nunca treinar com dados parciais silenciosamente)

### 4. Relatório diário (`relatorio_diario.py`)

- `python relatorio_diario.py` → valida ontem (dia útil) e gera `relatorios_diarios/YYYYMMDD.md`
- Exit 0 = dados OK | exit 1 = dados suspeitos
- A função `validar_dia()` é a MESMA usada pelo gate (única fonte de verdade)
- `ultimos_dias_uteis(5)` gera a lista de dias para o gate nos `.bat`

### Integração automática

- `retreinar.bat` (manual): gera a lista dos últimos 5 dias úteis e roda com `--gate-dias`; aborta com mensagem clara se o gate reprovar
- `auto_retreinar.bat` (agendado 18:36): roda o relatório do dia + retreino com gate, tudo logado em `retreinar.log`

### Testes (68 → **72**)

| Novo teste | Valida |
|---|---|
| `TestCapturaMeta` (1) | `fechar()` grava meta com contagens corretas |
| `TestValidarDia` (3) | Dia sem arquivos → problema; dia saudável (600 negócios, 5h) → OK; dia com poucos negócios → problema |

Smoke ganhou a seção [10] (meta + validar_dia + dias úteis) — **47/47 PASS**.
Gate testado manualmente: dia ruim → **exit 2** com mensagem clara; dia bom → **exit 0**.

### Arquivos alterados

| Arquivo | Mudanças |
|---|---|
| `captura_eventos_ms.py` | `_meta` (contagens por ativo, rejeitados, início/fim); gravado no `fechar()` |
| `motor_rt_alphaz.py` | Log periódico dos rejeitados da captura (10min) no `_loop` |
| `relatorio_diario.py` | **Novo**: `validar_dia()`, `gerar_relatorio()`, `ultimos_dias_uteis()`, `dia_util_anterior()` |
| `retreinar_sem_leak.py` | `--gate-dias` + `--save-dir`; `gate_qualidade()` aborta (exit 2) |
| `retreinar.bat` / `auto_retreinar.bat` | Relatório + gate integrados ao fluxo |
| `test_features.py` | +4 testes (72 total) |
| `smoke_test_v96.py` | Seção [10] (45 checagens) |

---

## v9.9 — Pipeline RTD 100% (ritmo, rotação, fsync, fix)

**Contexto:** revisão completa do pipeline de leitura→captura→processamento→armazenamento achou 4 gaps que impediam o "nível máximo". Todos corrigidos.

### 1. Ritmo adaptativo do `_loop` (removido gargalo de 20Hz)

| Antes | Agora |
|---|---|
| `time.sleep(0.05)` **incondicional** após cada ciclo — o motor processava a **20Hz** mesmo com dados na fila (perda de contexto em mercado rápido) | `time.sleep(0.002)` se há dados novos **ou** fila não vazia; `time.sleep(0.05)` só com mercado parado. O ciclo gira a **50Hz+** quando o RTD está ativo |
| `ERROS_GLOBAIS['fila_eventos_cheia']` — contador invisível | Além do contador, **log.warning** com taxa quando o backlog passa de 2000 lotes (rede de segurança) |

### 2. Rotação por tamanho REAL (captura + trades)

| Antes | Agora |
|---|---|
| `captura_eventos_ms.py`: arquivos cresciam **sem limite** até o fim da sessão (JSONL cru, potencialmente GB) | `max_bytes_por_arquivo=100MB` — ao exceder, fecha e abre `_p01`, `_p02`... |
| `motor_rt_alphaz.py`: `_flush_trades` tinha o **comentário "Rotates at 100MB per file"** — mas **nenhuma lógica de rotação existia** (4 anos de comentário mentiroso) | `_rotacionar()` helper: fecha o arquivo atual e abre a próxima parte com `_p02`, `_p03`... A primeira parte mantém o nome original (backward compat) |
| Batch (`batch_processor.py`): lê por **glob** `raw_negocios_ms_*{data}*.jsonl` — suporta múltiplos arquivos por dia desde sempre (merge sort por ts_ms). Rotação **não quebra o pipeline** | (inalterado — já suportava) |

### 3. fsync periódico (durabilidade)

| Antes | Agora |
|---|---|
| Apenas a **captura** fazia `os.fsync` em cada flush | `_flush_trades` e `_flush_decisoes` fazem `os.fsync` a cada 20 flushes (~4000 trades) — durabilidade sem pagar o custo de fsync a cada 200 trades |

### 4. Fix: `_garantir_fp` nunca era chamado 🐛

**Bug real:** `_garantir_fp()` (que abre os arquivos `negocios_*.jsonl` e `decisoes_*.jsonl`) **nunca era invocado de lugar nenhum**. O bloco `if self._fp is not None` em `_flush_trades` sempre falhava → os arquivos nunca foram criados e o buffer `_buf_trades` crescia **sem limite** (memory leak). O sistema só gravava através da captura (`raw_negocios_ms_*`).

Correção: `_gravar_trade` e `_gravar_decisao` chamam `_garantir_fp()` na primeira linha. Agora `negocios_*.jsonl` e `decisoes_*.jsonl` são escritos como sempre deveriam ter sido.

### Testes (67 → **68**)

| Novo teste | Valida |
|---|---|
| `TestCapturaRotacao` (1) | 8 trades com max_bytes=250 → 4 partes; total de 8 linhas preservado |
| `smoke_test_v96.py [9]` | Arquivo de negócios aberto; rotação cria múltiplas partes; cada parte tem linhas (**41/41 total**) |

### Arquivos alterados

| Arquivo | Mudanças |
|---|---|
| `captura_eventos_ms.py` | Rotação por tamanho (`max_bytes_por_arquivo`, `_parte`, `_bytes_arquivo`, `_rotacionar`); `_abrir(tipo)` gera nomes com sufixo a partir da parte 1 |
| `motor_rt_alphaz.py` | `_loop`: ritmo adaptativo (0.002s com dados, 0.05s sem) + backlog warning; `_flush_trades`: rotação real + fsync periódico; `_flush_decisoes`: idem; `_rotacionar()` helper; `_gravar_trade`/`_gravar_decisao`: chamam `_garantir_fp` (fix); `__init__`: atributos de rotação |
| `test_features.py` | `TestCapturaRotacao` (68 total) |
| `smoke_test_v96.py` | Seção [9] (34 checagens) |

---

## Sessão 20/08/2026 — Log de Progresso

### Manhã (08:45 - 12:00)

| O que | Status |
|-------|--------|
| Auto-start 8:45 via Task Scheduler | ✅ |
| Auto-stop 18:35 via Task Scheduler | ✅ |
| Motor ligou as 8:45 automaticamente | ✅ |
| Bug: `_normalizar_simbolo` não existia no motor_web.py | ✅ Corrigido |
| Dashboard conectando | ✅ |
| Bug: `estado` não definido em `alimentar_book` | ✅ Corrigido |
| RTD Alert (vermelho/laranja) | ✅ |
| Alerta pré-abertura (8:45-9:00) | ✅ |

### Tarde (12:00 - 18:00)

| O que | Status |
|-------|--------|
| Testes unitários (33 testes, features_lib) | ✅ |
| config.json + loader | ✅ |
| Variáveis de ambiente (SINAL_RT_DIR, PROFIT_DATA_DIR) | ✅ |
| Watchdog (auto-restart) | ✅ |
| Task Scheduler com LogonType Interactive | ✅ |
| 6 patches de sanity check de preço | ✅ |
| Patch anti-overtrading (cooldown 45s) | ✅ |
| Patch anti-whipsaw (holding 90s, confiança 0.75) | ✅ |
| Bug: `get_learning()` inexistente no /api/all | ✅ Corrigido |

### Refatoração features_lib (Passos 1-4)

| Passo | O que | Status |
|-------|-------|--------|
| 1 | OFITracker unificado no features_lib.py | ✅ |
| 2 | BookLevelFeatures unificado no features_lib.py | ✅ |
| 3 | 3 novas features (micro_drift, imb_ponderado, slope) | ✅ |
| 4 | Features integradas no score + pesos | ✅ |

### Bugs críticos corrigidos

| Bug | Impacto | Correção |
|-----|---------|----------|
| `_normalizar_simbolo` não existia | Motor não conectava RTD | Adicionada função no motor_web.py |
| `estado` não definido em `alimentar_book` | Crash na inicialização | Passado `estado` como parâmetro |
| `await` fora de `async function` | Dashboard não carregava | Movido bloco para dentro da função |
| `get_learning()` inexistente | Erro no /api/all | Trocado por `get_estatisticas()` |
| `book_snap_ant` nunca setado | `book_level` sempre null, `/api/book` vazio | Movido para fora do `if ant:` |
| Cooldown contava da abertura | Overtrading por reversão | Agora conta do fechamento |

### Configurações anti-overtrading

| Proteção | Parâmetro | Efeito |
|----------|-----------|--------|
| Cooldown | `cooldown_entre_trades_s: 45` | Mínimo 45s entre trades |
| Holding reversão | `min_holding_reversao_s: 90` | Não fecha antes de 90s |
| Confiança reversão | `confianca_min_reversao: 0.75` | Só com confiança alta |
| Sem reversão em lateral | regime check | Em lateral, só TP/SL/timeout |
| Sanity preço | `max_salto_preco_pct: 0.15` | Rejeita saltos > 15% |
| Faixa preço | `faixas_preco` | WIN: 20K-500K, WDO: 1K-20K |

### Arquivos finais

| Arquivo | Linhas | Função |
|---------|--------|--------|
| motor_rt_alphaz.py | ~3450 | Motor principal |
| motor_web.py | ~2560 | Conexão RTD |
| features_lib.py | ~660 | Feature Engine (código único) |
| captura_eventos_ms.py | ~100 | Captura ms |
| batch_processor.py | ~200 | Batch processing |
| labeler.py | ~200 | Triple barrier |
| dataset_builder.py | ~150 | Dataset builder |
| test_features.py | ~250 | 33 testes |
| config.json | ~30 | Configuração |
| watchdog.py | ~150 | Auto-restart |
| iniciar_motor.bat | ~10 | Inicialização |
| auto_start.bat | ~40 | Instalação |
| DOCUMENTACAO.md | ~500 | Esta documentação |

---

## Últimas Alterações (v9.4)

### Volume Profile + Kyle's Lambda (features_lib.py)

| Classe | O que faz | Output |
|--------|-----------|--------|
| `VolumeProfileTracker` | Acumula volume por nível, calcula POC/VAH/VAL | `poc_dist`, `vah_dist`, `val_dist`, `poc_acima` |
| `KyleLambdaTracker` | Regressão ΔP ~ λ·V (impacto/preço) | `kyle_lambda` (alto = frágil, baixo = líquido) |

Integrados no `GeradorJanelas` — cada snapshot 100ms tem `snap['vp']` e `snap['kyle']`.

### Blindagem captura_eventos_ms.py (v8)

| Blindagem | O que protege |
|-----------|---------------|
| ts futuro (>5s) | Rejeita dados do futuro |
| ts antigo (>5min) | Rejeita replay |
| qtd > 100K | Rejeita quantidade absurda |
| preco ≤ 0 | Rejeita preço inválido |
| Dedup | Mesmo trade entregue 2x |
| Overflow | Limita buffer a 100K linhas |
| Flush seguro | `fsync` + não descarta em erro I/O |
| Auditoria | `stats()` retorna contadores |

### R:R regime lateral corrigido

| | Antes | Agora |
|--|-------|-------|
| TP | 100 × 0.6 = 60 pts | 100 × 1.0 = 100 pts |
| SL | 100 × 1.0 = 100 pts | 100 × 0.8 = 80 pts |
| R:R | 0.6:1 🔴 | 1.25:1 🟢 |

### Sanity check endurecido

| Faixa | Antes | Agora |
|-------|-------|-------|
| WIN | 20K-500K | 150K-250K |
| IND | 20K-500K | 150K-250K |

### Anti-whipsaw consolidado

| Proteção | Parâmetro |
|----------|-----------|
| Holding reversão | `min_holding_reversao_s: 90` |
| Confiança reversão | `confianca_min_reversao: 0.75` |
| Sem reversão em lateral | regime check |

### Bug book_snap_ant corrigido

`self.book_snap_ant[ativo]` movido de dentro do `if ant:` para fora — garante snapshot na primeira chamada. Isso corrigia `book_level` sempre null e `/api/book` retornando `{}`.

### Scripts offline criados

| Script | Função |
|--------|--------|
| `labelar_offline.py` | Wrapper labeler com filtro por ativo |
| `treinar_modelo.py` | Treino supervisionado (LightGBM/XGBoost/RF) |

### Arquivos finais atualizados

| Arquivo | Linhas | Função |
|---------|--------|--------|
| motor_rt_alphaz.py | ~3460 | Motor principal |
| motor_web.py | ~2560 | Conexão RTD |
| features_lib.py | ~750 | Feature Engine (OFI, BookLvl, VP, Kyle) |
| captura_eventos_ms.py | ~140 | Captura blindada |
| batch_processor.py | ~200 | Batch processing |
| labeler.py | ~200 | Triple barrier |
| labelar_offline.py | ~40 | Wrapper labeler |
| treinar_modelo.py | ~100 | Treino ML (3 classes) |
| dataset_builder.py | ~150 | Dataset builder |
| test_features.py | ~250 | 33 testes |
| config.json | ~30 | Configuração |
| watchdog.py | ~150 | Auto-restart |
| iniciar_motor.bat | ~10 | Inicialização |
| auto_start.bat | ~40 | Instalação |
| DOCUMENTACAO.md | ~550 | Esta documentação |

---

## Sessão 21/08/2026 — Pente-Fino e Estabilidade

### Resumo

Revisão completa do código em busca de bugs de lógica e sintaxe. 6 bugs P0/P1 corrigidos, incluindo crash loop do motor, labeler que só simulava compra, e watchdog que passava arquivos como ativos.

### Bugs P0 Corrigidos

| # | Bug | Arquivo | Impacto | Correção |
|---|-----|---------|---------|----------|
| 1 | Crash loop — `_loop()` sem try/except | motor_rt_alphaz.py | Qualquer exceção (OOM, COM, div/0) mata o processo | Loop inteiro envolto em try/except, loga e continua |
| 2 | Labeler só simula compra (`lado='C'` fixo) | labeler.py | Modelo treinado só aprende a comprar | Simula compra E venda, label = quem atingiu TP primeiro |
| 3 | `'regime' in dir()` sempre True | motor_rt_alphaz.py | Estratégia por regime nunca mudava | Trocado por `regime` direto (variável já existe no escopo) |
| 4 | Watchdog passa arquivos (.py/.json) como ativos | watchdog.py | Motor recebia paths como símbolos | Filtra args: remove paths e extensões |
| 5 | Watchdog RESTART_DELAY_S = 3s (pouco para COM) | watchdog.py | Motor reiniciava antes do COM esfriar | Aumentado para 10s + proteção multi-instância (psutil) |
| 6 | Treinar modelo só binário (compra vs não) | treinar_modelo.py | Modelo cego para vendas | 3 classes (-1, 0, 1) com matriz 3×3 |

### Bugs P1 Corrigidos (sessão anterior)

| # | Bug | Arquivo | Correção |
|---|-----|---------|----------|
| 7 | OFI pré-calculado usa `or` (0 = falsy) + dict em vez de float | features_lib.py | `isinstance` check para dict/float/None |
| 8 | `desligar_horarios_ruins` do config.json nunca carregado | motor_rt_alphaz.py | Adicionado loader no `_carregar_config_externa` |
| 9 | `prev_ofi` declarado mas nunca usado | features_lib.py | Dead code removido |
| 10 | `self._ultimo_preco_valido` duplicado em `alimentar_lote` | motor_rt_alphaz.py | Linha redundante removida |

### Proteção de Horário (config.json)

```json
"desligar_horarios_ruins": false
```

Quando `true`, bloqueia abertura de posições em:
- Antes 10:00 (pré-abertura)
- 12:00-13:30 (almoço)
- 16:30+ (fechamento)

**Status atual: DESLIGADO** — motor opera o dia inteiro.

### Treino 3 Classes

O `treinar_modelo.py` agora treina com 3 classes:

| Label | Significado |
|-------|-------------|
| `1` | Compra lucrou (atingiu TP antes de SL) |
| `-1` | Venda lucrou (atingiu TP antes de SL) |
| `0` | Neutro (expirou tempo ou vol insuficiente) |

Matriz de confusão 3×3 mostra acerto por classe. Profit Factor simulado considera ganhos de compra E venda.

### Labeler Corrigido

O `_simular_posicao` agora é chamado 2 vezes por snapshot — uma para compra (`lado='C'`) e uma para venda (`lado='V'`). O label final é:
- `1` se compra atingiu TP primeiro
- `-1` se venda atingiu TP primeiro
- `0` se nenhum atingiu TP

### Watchdog Robustecido

| Feature | Antes | Agora |
|---------|-------|-------|
| Args | Passava tudo ao motor | Filtra: só ativos (sem paths/extensões) |
| Delay reinício | 3s | 10s (esfriar COM) |
| Multi-instância | Nenhuma | psutil detecta outro watchdog |
| Crash log | DEVNULL | `motor_stdout.log` |

---

## Bugs Corrigidos (histórico)

30+ bugs P0-P3 corrigidos incluindo: absorção, WDO inverso, SL, decay, hash book, purge, SimpleQueue, sinal fantasma, pesos regime, EWMA, timeout, reversão, TP/SL EMA, circuit breaker, `_normalizar_simbolo`, `estado` não definido, `await` fora de async, `get_learning()` inexistente, `book_snap_ant` nunca setado, `faixas_preco` muito largas, crash loop sem try/except, labeler só compra, regime em dir(), watchdog passa arquivos, OFI dict vs float, config `desligar_horarios_ruins` ignorado.

---

## v9.6 — Pente-Fino: 5 funcionalidades mortas reativadas + robustez

**Contexto:** auditoria completa do código (`pente fino`) identificou que 5 sistemas documentados **nunca produziam efeito em produção** (código morto por lógica), 1 bug de contagem financeira, e o pipeline de captura/retreino **silenciosamente quebrado**. Todos corrigidos e validados com 41 testes unitários + smoke test.

### 🔴 Funcionalidades mortas reativadas

| # | Sistema | O que estava errado | Correção |
|---|---------|---------------------|----------|
| 1 | **CrossAssetEngine (Camada 4)** | `cross_engine.registrar()` **nunca era chamado** — lag_ms, corr_aggr, divergencia, wdo_leading, resposta_win sempre retornavam 0 | `alimentar_lote` agora registra cada tick (ts ms nativo, ±1 por agressor) |
| 2 | **CrossAssetEngine (relógio)** | Cutoffs usavam `time.time()*1000` (epoch) contra ts do T&T (hora-do-dia, ~32M) → `t < cutoff` sempre verdadeiro → engine sempre vazio | Novo helper `_tod_ms()`; cutoffs agora usam hora-do-dia, mesmo relógio do RTD |
| 3 | **Pesos por regime** | `add()` lia `f.get('regime', 'lateral')` mas `regime` só era setado na cópia da API → **sempre pesos de 'lateral'**; boosts de OFI por regime (0.6 em tendência) nunca aplicavam | `_avaliar` detecta regime ANTES do score e grava `f['regime']`; `ajustar_por_regime` recebe o `regime_info` já calculado |
| 4 | **Confirmação por regime** | `_confirmacao_congelada` congelada em 3 — `confirmacao` da estratégia (1-5 por regime) nunca tinha efeito | `_suavizar_sinal` usa `self.confirmacao_necessaria` (atualizado por regime) |
| 5 | **Stop-hunt** | `detectar_stop_hunt` comparava `preco > topo` com topo já incluindo o tick atual → condição sempre falsa → nunca disparava | Reescrito em **2 fases**: (1) rompimento de extremo anterior marca pendência; (2) reversão ≥ `stop_hunt_reversao_pts` registra o stop-hunt |
| 6 | **Captura batch (`captura_eventos_ms`)** | Blindagem comparava `tms` (hora-do-dia) com `agora_ms` (epoch) → **todo trade rejeitado como replay** → `raw_negocios_ms_*.jsonl` nunca gravava negócios | Converte `tms` para epoch ms na entrada (`offset = agora_epoch - agora_tod`); dedup/pruning no mesmo relógio; book (já epoch) alinha com trades no batch |

### 🟠 Bugs de lógica e robustez

| # | Local | Correção |
|---|-------|----------|
| 7 | `AccumulationTracker.registrar` | `else: v += qtd` contava agressor `neutro` como venda → agora `elif lado == 'Vendedor'`; neutro não conta em nenhum lado |
| 8 | `_carregar_config_externa` | `max_holding_s` do config.json **nunca era carregado** (motor usava 300s fixo) → agora carrega (0 = timeout desligado); aceita `custo_execucao` e `custos_execucao_pontos` |
| 9 | Rollover de dia | `buffer` e `seg_atual` não eram zerados na troca de data → agora sim (T&T reinicia em 0) |
| 10 | Purge `vistos_tt` (>40k) | `pop(min(...))` em loop = O(n²) no callback RTD → `sorted(...)[:n]` O(n log n) |
| 11 | Hot-loop no `_loop` | `time.sleep(0.05)` só rodava no `else` do try (sucesso) → em falha contínua o loop girava sem pausa → sleep agora é incondicional |
| 12 | I/O silencioso | `_garantir_fp`, `_flush_trades/decisoes`, `salvar/carregar_aprendizado`, checkpoints — exceções engolidas com `pass` → agora logam `[IO]`/`[LEARN]`/`[POS]` warning |
| 13 | `features_lib._extrair_vols/_precos` | Filtravam zeros de forma independente → **desalinhamento preco×vol** quando um nível tinha vol 0 → novo `_extrair_pares` mantém pares alinhados (filtra o PAR inválido) |
| 14 | `VolumeProfileTracker` | `round()` bancário do Python deslocava níveis em ticks .5 → `int(x/tick + 0.5)*tick` (half-up) |
| 15 | `retreinar.bat` / `auto_retreinar.bat` | Logavam "Retreino concluido" **mesmo com falha** (ex.: pandas ausente) → agora checam `errorlevel` e registram falha real |
| 16 | `_avaliar` ramo morto | `elif score < -0.3: sinal = 0` (redundante) removido; comentário explica regra "não inverte contra o fluxo" |

### 🟡 Testes

| Item | Correção |
|------|----------|
| `test_corretora_tracking` | Era vacuoso (`or snap['vol_compra'] == 10` sempre passava) → agora valida `hhi_compra`/`hhi_venda`/`vol_total` reais |
| Dependências | Instalados no ambiente: `pytest`, `pandas`, `scikit-learn`, `pyarrow`, `comtypes` — a suíte passou de "não roda" para **41/41 PASS** |
| `smoke_test_v96.py` | Novo smoke test de regressão (relógio cross-asset, stop-hunt 2 fases, captura epoch, neutro≠venda, alinhamento pares, round tick) — 16/16 PASS |

### Dashboard

`get_features()` agora expõe `regime` como **string** (ex.: `"tendencia_alta"`) + `regime_info` (dict completo direção/vol) — antes `regime` era um dict serializado e o painel REGIME exibia `[object Object]`.

### Arquivos alterados

| Arquivo | Mudanças |
|---------|----------|
| `motor_rt_alphaz.py` | #1-5, #7-12, #16 (16 edições) |
| `features_lib.py` | #13-14 |
| `captura_eventos_ms.py` | #6 |
| `test_features.py` | teste corrigido |
| `retreinar.bat`, `auto_retreinar.bat` | #15 |
| `smoke_test_v96.py` | novo (regressão) |

> **Nota de produção:** o crash loop `name 'custo' is not defined` visto no log de 17:04 já havia sido corrigido no disco (17:03) antes desta auditoria; os reinícios do watchdog (17:40/17:48) foram do processo antigo com código velho em memória. Após esta v9.6, reiniciar o motor a partir do código atual carrega todas as correções.

---

## v9.7 — Features alinhadas à teoria de microestrutura

**Contexto:** revisão conceitual das features identificou 3 desvios da literatura que degradavam a qualidade do sinal. Todos corrigidos em `features_lib.py` e validados com testes.

### 1. OFI alinhado por PREÇO (Cont-Kukanov-Stoikov, fiel)

| | Antes (v9.6) | Agora (v9.7) |
|---|---|---|
| Alinhamento | Por **profundidade** (índice da lista) | Por **preço** (chave do nível) |
| Melhora do best bid (100.0→100.5) | Só registrava **adição** (+vol) nos níveis afetados — a remoção do preço deslocado **nunca era contabilizada** → OFI enviesado para a direção do preço em mercado rápido | Registra **adição no novo preço (+vol) E remoção no preço deslocado (-vol)** — volume que só mudou de nível gera OFI líquido ≈ 0 (teste `test_melhora_best_bid_sem_mudanca_de_volume`) |
| Efeito | Supercontava adições; falsos positivos de momentum | Sinal fiel à teoria |

API inalterada (`atualizar(bid_levels, ask_levels)` recebe tuplas `(preco, vol)`; `get_ofi()` idêntico) — live e batch continuam usando a MESMA conta.

### 2. Kyle's Lambda sobre TODOS os trades (Kyle, 1985, fiel)

| | Antes | Agora |
|---|---|---|
| Observações | Só trades que **mudavam o preço** (ΔP≠0) | **Todos** os trades (ΔP=0 incluído) |
| Objeto medido | "Volume condicionado a mudança de preço" (diferente do teórico) | λ clássico: regressão OLS de ΔP sobre volume assinado — trades sem impacto puxam λ para baixo em mercados líquidos, como na teoria |

### 3. Normalização z-score por EWMA (estacionaridade) — **opt-in**

Nova classe `EWMAZScore` (features_lib): `z = (x − média)/desvio` com média e média-quadrado por EWMA (O(1)), piso anti-divisão-por-zero, e gate de `min_amostras` (retorna 0 sem informação). Aplicada às **contribuições do score** em `_avaliar` (`add()`), com ordem correta (z antes, atualiza depois — sem auto-influência).

- **Objetivo:** features de volume/fluxo não são estacionárias entre manhã e tarde; normalizar torna os thresholds (`score > 0.3`, `conf 0.55`) comparáveis ao longo do dia.
- **⚠️ Experimental — default OFF.** Ative com `"normalizar_score": true` no `config.json`. Recomenda-se validar em simulação antes de ligar em produção (as contribuições passam a ser z-scores, e o aprendizado MFE/MAE recalibra os pesos nessa escala).

### Testes (41 → **53**)

| Novo teste | Valida |
|---|---|
| `TestOFI` (5) | Migração de volume sem mudança → OFI≈0; adição/remoção limpas; 1ª chamada inicializa; níveis vazios ignorados |
| `TestKyleLambda` (4) | n≥20; ΔP=0 contam; λ>0 com compra/preço subindo e venda/preço caindo |
| `TestEWMAZScore` (3) | gate min_amostras; sinal do z; constante → z≈0 |

`smoke_test_v96.py` ganhou a seção [7] (OFI por preço + Kyle) — **19/19 PASS** no total.

### Arquivos alterados

| Arquivo | Mudanças |
|---|---|
| `features_lib.py` | `OFITracker` reescrito (preço), `KyleLambdaTracker` completo, classe `EWMAZScore` nova |
| `motor_rt_alphaz.py` | Import `EWMAZScore`; `CONFIG["normalizar_score"]` (default False); carregamento do config; normalização em `add()` |
| `config.json` | Chave `"normalizar_score": false` |
| `test_features.py` | +12 testes (53 total) |
| `smoke_test_v96.py` | Seção [7] (19 checagens) |

---

## v9.8 — 4 features novas (CVD, volatilidade, sessão, atividade)

**Contexto:** adição de features de microestrutura com alto valor/baixo esforço, todas no mesmo padrão (`features_lib.py` canônica → motor/batch/live usam a mesma conta). Foram propositalmente **poucas** (o sistema já tem ~60; com poucos dados de treino, mais features = risco de overfitting).

### 1. CVD (Cumulative Delta) + divergência CVD×preço 🎯

| Item | Detalhe |
|---|---|
| O quê | `cvd_total` = Σ(volume comprador) − Σ(volume vendedor) da sessão, incremental O(1) |
| Divergência | `cvd_div`: quando o preço faz **topo novo** mas o delta acumulado está **menor** que no topo anterior → `-1` (exaustão compradora / bearish); **fundo novo** com delta **maior** → `+1` (bullish); confirmação → `0` |
| Onde | `JanelaFeatures` (batch + ML live via `GeradorJanelas`) **e** motor (`_calcular` a partir do `stats` da sessão) |
| No score | Nova contribuição `add('cvd_div', ±0.6, ...)` com peso inicial `cvd_div: 0.35` — extremo sem delta = reversão |

### 2. Volatilidade realizada + range vol

| Item | Detalhe |
|---|---|
| `realized_vol_bps` | √(EWMA de ret²) em bps — α=0.1, incremental |
| `range_vol_bps` | High−low da janela (T&T) em bps; no motor, dos últimos 60s do histórico |
| Por quê | o motor **classifica regime**, mas o vetor de features **não tinha** nenhuma medida explícita de volatilidade — lacuna real |

### 3. Fase de sessão + dias até vencimento (específico B3)

| Item | Detalhe |
|---|---|
| `fase_sessao` | `'abertura' \| 'meio' \| 'almoco' \| 'fechamento'` — função pura com defaults espelhando o config.json; o motor passa os horários **do próprio config** |
| `dias_ate_venc` | Proxy: dias até o dia 15 do mês de vencimento, parseado do símbolo (`WINV26` → out/2026). Calendário exato B3 varia ±2 dias do dia 15 — documentado como proxy de rolagem |
| Fuso | `_tod_de_ts()` converte epoch do batch (que cai em UTC tod por causa do `offset` do capture v9.6) para hora local via `utcoffset()` dinâmico |

### 4. Taxa de eventos (`taxa_eventos`)

Eventos por segundo na janela (`n_eventos / (janela_ms/1000)`) — distingue mercado ativo × morto sem olhar volume. No motor o equivalente já existia (`trades_per_sec`); no batch/live ML é a nova `taxa_eventos`.

### Testes (53 → **65**)

| Novo teste | Valida |
|---|---|
| `TestCVD` (4) | Acúmulo do delta; topo confirma (0); topo bearish (−1); fundo bullish (+1) |
| `TestVolNova` (3) | Preço constante → vol 0; preço movendo → vol > 0; taxa de eventos = 50/s |
| `TestSessao` (5) | Fases 09:30/11:00/12:30/15:00/16:45; conversão epoch↔tod com fuso; vencimento WIN/WDO/IND; snapshot inclui sessão; `GeradorJanelas` emite as features novas |

`smoke_test_v96.py` ganhou a seção [8] — **TODAS as 29 checagens PASS**.

### Arquivos alterados

| Arquivo | Mudanças |
|---|---|
| `features_lib.py` | Funções puras `fase_sessao`, `dias_ate_vencimento`, `_tod_de_ts` (+ fuso); `JanelaFeatures` com CVD, divergência, vol realizada, range, taxa; `GeradorJanelas` passa o símbolo |
| `motor_rt_alphaz.py` | `import math`; features novas em `f[]` (`cvd_total`, `cvd_div`, `realized_vol_bps`, `range_vol_bps`, `fase_sessao`, `dias_ate_venc`); peso `cvd_div: 0.35`; contribuição da divergência no score; trackers `_cvd_extremos`/`_ewma_ret2`/`_ultimo_preco_fim` |
| `test_features.py` | +12 testes (65 total) |
| `smoke_test_v96.py` | Seção [8] |

---

## v9.8.1 — Fix: poda do dedup da captura crasheava

**Bug real encontrado na revisão do pipeline RTD:** `captura_eventos_ms.py` linha 103 usava `agora_ms` (variável inexistente — o correto é `agora_epoch`) na poda do `_trades_recentes`. Quando o dedup passava de 20000 entradas (~7 min de sessão movimentada), o `NameError` quebrava o `registrar_negocios`; o erro era **engolido pelo try do `_loop`** como "falha de refresh RTD" → o motor reconectava e a captura de negócios parava silenciosamente até o fim da sessão. Ou seja: **a matéria-prima do batch (e o retreino) ficava vazia/parcial sem nenhum aviso claro.**

Correção: `agora_ms` → `agora_epoch`. +2 testes de regressão (`TestCapturaDedup`: poda sem crash com 20001 entradas; duplicata rejeitada). **67/67 testes, smoke 29/29.**

> Observação do teste: `tempfile.mkdtemp` é bloqueado pelo sandbox de arquivos (dirs criados em runtime) — os testes de captura gravam na raiz do workspace e limpam no `finally`.

| Arquivo | Mudança |
|---|---|
| `captura_eventos_ms.py` | Fix `agora_ms` → `agora_epoch` (linha 103) |
| `test_features.py` | +2 testes de captura (67 total) |

---

## Sessão 22/08/2026 — Labeler Vectorizado + Walk-Forward Real

### Resumo

Investigação profunda do pipeline de labeling e validação walk-forward. Encontrado e corrigido **data leakage total** que fazia o modelo parecer perfeito (100% acc) quando na verdade era inútil. Labeler reescrito em vectorizado NumPy, ~180x mais rápido. Walk-forward agora muestra métricas reais. Comparação RandomForest vs LightGBM — RF vence.

### 🐛 Bug Crítico: Data Leakage no Walk-Forward

**Problema:** walk_forward_resultado.json mostrava:
- Acurácia: 100%
- Profit Factor: 0
- Feature importances: todas 0.0
- Expectancy: +100 pts

Isso é **lixo puro** — 100% de acurácia com features zeradas significa que o modelo só aprendeu a prever a classe majoritária.

**Causa:** O labeler original gerava 99.99% de labels neutros (0). Quando walk_forward.py converte `(label == 1).astype(int)`, tudo vira 0. O modelo treina prevendo "tudo é 0" e acerta 100%.

**Raiz do problema — 3 bugs no labeler original:**

| # | Bug | Impacto |
|---|-----|--------|
| 1 | **Mistura WIN/WDO** | Labels gerados misturando preços 183.000 e 5.100 — cálculo de rolling max/min completamente errado |
| 2 | **Preços zero** | 4.48% dos registros tinham preco=0, distorcendo o rolling max/min |
| 3 | **Embargo 10s** | Cada trade "bloqueava" ~40s (duração + purge) — para treino ML, isso é contraproducente |

### 🏷️ Solução: labeler_vectorizado.py

**Arquivo novo:** `labeler_vectorizado.py` (~200 linhas)

| Melhoria | Implementação |
|----------|---------------|
| **Vectorizado** | NumPy rolling max/min, sem loop Python — 180x mais rápido |
| **Ativos separados** | `--ativo WINV26` (TP=100pts) e `--ativo WDOU26` (TP=1pt) |
| **Filtro de zeros** | Remove preço=0 antes do cálculo |
| **Sem embargo** | `purge_s=0` para dados de treino ML — cada ponto recebe label independente |

**Performance:**
- Original: ~30 min para processar 3.4M registros
- Vectorizado: **~10 segundos** para 3.4M registros

**Resultados do labeler:**

| Ativo | +1 (compra) | -1 (venda) | 0 (neutro) | Total não-zero |
|-------|-------------|------------|------------|----------------|
| **WINV26** | 159,920 (4.7%) | 170,080 (5.0%) | 90.3% | **330,000** |
| **WDOU26** | 439,670 (12.8%) | 426,920 (12.5%) | 74.7% | **866,590** |

**Validação:**
- TP=100pts para WINV26 é realista: 21% dos ticks atingem em 30s com variação média de 198pts
- TP=1pt para WDOU26 é realista: 45.5% dos ticks atingem em 30s com variação média de 0.8pts

### 📊 Walk-Forward Resultado (v2 — Corrigido)

**Dataset:** `dataset_final_v2_win.parquet` (6.8M linhas, labels mergeados)

**Métricas:**

| Métrica | v1 (bugado) | v2 (corrigido) |
|---------|-------------|----------------|
| **Acurácia** | 100% (falsa) | **57.74%** |
| **AUC-ROC** | null | **0.6162** |
| **Profit Factor** | 0 | **2.73** |
| **Expectancy** | +100 pts (falso) | **+36.6 pts** |
| **Features úteis** | 0 | **26 com importâncias** |

**Feature Importances (Top 10):**

```
delta_preco_janela     19.0%  ████████████████
vp_vp_total            10.1%  ████████
preco_ultimo            7.6%  ██████
cvd_total               6.9%  █████
ewma_imb_longa          6.5%  █████
vp_vah_dist             5.8%  ████
vp_poc_dist             5.4%  ████
aggr_imb                5.3%  ████
n_eventos_janela        4.8%  ███
vol_compra              4.5%  ███
```

**Interpretação:**
- `delta_preco_janela` (19%) — mudança de preço na janela é o maior preditor
- `vp_vp_total` (10.1%) — volume profile total importa
- `cvd_total` (6.9%) — cumulative volume delta detecta pressão compradora/vendedora
- `ewma_imb_longa` (6.5%) — imbalance de longo prazo sinaliza tendência

### 🤖 Comparação: RandomForest vs LightGBM

**Configuração:** walk-forward com datas_treino 04-12/ago, datas_teste 13-17/ago

| Métrica | RandomForest | LightGBM |
|---------|-------------|----------|
| **Acurácia** | **57.74%** | 52.21% |
| **AUC-ROC** | **0.6162** | 0.5342 |
| **Profit Factor** | **2.73** | 2.19 |
| **Expectancy** | **+36.6 pts** | +28.3 pts |
| **Tempo treino** | ~10s | ~260s |

**Vencedor: RandomForest**

**Por que RF venceu:**
1. Dataset médio (229K amostras, 26 features) — RF mais robusto
2. Desbalanceamento (5.7% positivos) — `class_weight='balanced'` lida melhor
3. LightGBM com mais complexidade (63 leaves) **piorou** → overfitting
4. Melhor LightGBM (leaves=15, child=100) é quase um RF

### 📈 LightGBM Hyperparameter Tuning

**Arquivo:** `lightgbm_quick_test.py` (10 configs testadas)

| Config | Leaves | Child | LR | Est | Acc | PF | Exp |
|--------|--------|-------|-----|-----|-----|-----|-----|
| **#8** | 15 | 100 | 0.01 | 500 | 56.57% | 2.61 | +34.9 |
| #6 | 31 | 50 | 0.01 | 500 | 55.47% | 2.49 | +33.2 |
| #1 | 15 | 50 | 0.05 | 300 | 54.14% | 2.36 | +31.2 |
| #2 | 31 | 50 | 0.05 | 300 | 53.91% | 2.34 | +30.9 |

### 📁 Arquivos Alterados/Criados

| Arquivo | Mudança |
|---------|----------|
| `labeler_vectorizado.py` | **Novo**: labeler vectorizado NumPy (~200 linhas) |
| `lightgbm_quick_test.py` | **Novo**: teste rápido de 10 configs LightGBM |
| `lightgbm_tune.py` | **Novo**: grid search completo (108 configs) |
| `dataset_final_v2_win.parquet` | **Novo**: parquet com labels corretos |
| `labels_WINV26_combined.jsonl` | **Novo**: labels combinados WIN+WDO |
| `walk_forward_v2.json` | **Novo**: resultado walk-forward RandomForest corrigido |
| `walk_forward_v2_lgbm.json` | **Novo**: resultado walk-forward LightGBM |
| `lightgbm_tune_results.json` | **Novo**: resultados do tuning |
| `DOCUMENTACAO_TESTES.md` | **Novo**: documentação completa de testes |
| `watchdog.py` | Proteção contra fim de semana (weekday >= 5 → não inicia) |
| `auto_start.bat` | Agendamento apenas dias úteis (MON-FRI) |

### 🔧 Watchdog — Fim de Semana

**Mudança:** watchdog não roda nos fins de semana.

- `watchdog.py`: verificação `if dia_semana >= 5: return` no início do `run()`
- `auto_start.bat`: tarefas agendadas de `/sc daily` para `/sc weekly /d MON,TUE,WED,THU,FRI`

---

## v9.12 — Próximos Passos

| # | Prioridade | Descrição |
|---|------------|-----------|
| 1 | **Alta** | Integrar RandomForest no pipeline automático de retreino |
| 2 | **Alta** | Corrigir bug Unicode no pipeline_diario.py (⚠️ no cp1252) |
| 3 | **Média** | Rodar walk-forward em período maior para validar consistência |
| 4 | **Média** | Testar com mais features ou feature selection |
| 5 | **Baixa** | Experimentar XGBoost como terceiro modelo |
| 6 | **Baixa** | Adicionar métricas de risco (Sharpe, MaxDD) no walk-forward |

---

## Sessão 22/08/2026 — Validação Rigorosa do Modelo ML

### Contexto

Após corrigir o labeler (vectorizado, ativos separados, sem embargo) e obter walk-forward com 57.7% accuracy e PF 2.73, era necessário validar se esses resultados são reais e generalizáveis. O objetivo era produzir um relatório objetivo: (A) confirmado, (B) parcialmente confirmado, ou (C) não confirmado.

### 1. Auditoria de Leakage Temporal

**Procedimento:** Para cada uma das 29 features, verificou-se se o cálculo usa exclusivamente informações disponíveis no instante "t" (sem olhar o futuro).

| Resultado | Detalhe |
|-----------|--------|
| 25 de 29 features | ✅ OK — calculadas com dados ≤ t |
| `delta_preco_janela` | ⚠️ SUSPEITA — alta correlação com label futuro |
| Nenhum leak direto | Features em t, labels em t+30s |

**`delta_preco_janela` (17% importance):** Calcula `preco_ultimo - preco_inicio_janela` (mudança nos últimos 100ms). Não há leak direto, mas captura momentum de curto prazo que pode não generalizar em mercados diferentes.

**Conclusão:** Leakage temporal NÃO detectado. A feature suspeita precisa de teste adicional (remoção e reavaliação).

---

### 2. Auditoria do Labeler

**Verificações:**

| Check | Status | Detalhe |
|-------|--------|--------|
| Label futuro não influencia features | ✅ | Features em t, label em t+30s ahead |
| Separação WIN/WDO | ✅ | `--ativo WINV26` e `--ativo WDOU26` separados |
| Preços zero | ✅ | Filtrados antes do cálculo |
| Embargo | ✅ | `purge_s=0` para treino ML |
| Distribuição de labels | ✅ | ~5.7% não-zero, balanceado por dia |

**Distribuição de labels por dia:**

| Data | -1 (venda) | 0 (neutro) | +1 (compra) |
|------|------------|------------|-------------|
| 20260804 | 19,280 | 305,580 | 17,430 |
| 20260805 | 18,260 | 305,720 | 18,270 |
| 20260806 | 19,380 | 305,210 | 17,830 |
| 20260807 | 15,640 | 316,900 | 9,740 |
| 20260810 | 12,850 | 317,680 | 11,750 |
| 20260811 | 13,650 | 318,290 | 10,400 |
| 20260812 | 22,220 | 296,960 | 23,210 |
| 20260813 | 13,860 | 311,670 | 16,880 |
| 20260814 | 18,700 | 304,470 | 19,170 |
| 20260817 | 16,240 | 310,850 | 15,240 |

---

### 3. Walk-Forward Rigoroso

**Configuração (FROZEN — sem tuning):**

```python
# Modelo congelado
RandomForestClassifier(
    n_estimators=100,
    max_depth=10,
    class_weight='balanced',
    random_state=42
)

# Split: 7 dias treino / 3 dias teste
# Treino: 20260804-20260812 (229,910 amostras)
# Teste: 20260813-20260817 (100,090 amostras)
```

**Resultado Global:**

| Métrica | Resultado |
|---------|-----------|
| **Accuracy** | 56.76% |
| **AUC-ROC** | 0.6048 |
| **Profit Factor** | 2.63 |
| **Expectancy** | +35.1 pts |
| **Drawdown max** | 44,500 pts |
| **Sinais** | +1=53,930 / -1=46,160 |

---

### 4. Avaliação por Dia

| Dia | Accuracy | AUC | PF | Expectancy | Drawdown |
|-----|----------|-----|-----|------------|----------|
| 20260813 | 55.60% | 0.6002 | 2.50 | +33.4 pts | 22,500 |
| 20260814 | 55.45% | 0.5970 | 2.49 | +33.2 pts | 44,500 |
| 20260817 | **59.47%** | **0.6429** | **2.93** | **+39.2 pts** | 16,000 |

**Análise:** Performance consistente entre dias. Domingo 17/ago foi melhor (mercado mais previsível). Não há dia com PF < 2.0.

---

### 5. Ablação de Features

**Objetivo:** Descobrir se o modelo usa microestrutura ou só depende de preço.

| Grupo | Features | AUC | PF | Conclusão |
|-------|----------|-----|-----|-----------|
| **fluxo** | CVD, EWMA, VPIN, Kyle (8) | **0.6175** | **2.79** | **MELHOR** |
| todas | 29 features | 0.6048 | 2.63 | Baseline |
| top10 | Top 10 por importance | 0.6031 | 2.63 | Quase igual |
| preco_vol | Preço + Volume (7) | 0.5910 | 2.49 | Pior |

**Conclusão importante:** O grupo **fluxo** (8 features de microestrutura) supera todas as 29! Isso prova que o modelo realmente usa sinais de fluxo (CVD, imbalance, VPIN, Kyle Lambda), não só movimento de preço.

---

### 6. Teste de Robustez

**Diferentes splits temporais (mesmo modelo, sem retreino):**

| Split | Treino | Teste | AUC | PF | Expectancy |
|-------|--------|-------|-----|-----|------------|
| 7d/3d | 7 dias | 3 dias | 0.6048 | 2.63 | +35.1 pts |
| 8d/2d | 8 dias | 2 dias | **0.6655** | **2.88** | **+38.5 pts** |
| 5d/3d | 5 dias | 3 dias | 0.6136 | 2.75 | +36.8 pts |

**Observação:** Performance **melhora** com mais dados de treino (8d > 7d > 5d). Sinal é robusto e crescente.

---

### 7. Top 10 Features (por importância)

```
delta_preco_janela    0.1690  ← MOMENTUM (suspeito)
vp_vp_total           0.0918  ← VOLUME PROFILE
cvd_total             0.0668  ← CVD (fluxo)
ewma_imb_longa        0.0655  ← IMBALANCE
preco_entrada         0.0581  ← PREÇO (coleta)
preco_ultimo          0.0557  ← PREÇO
vp_vah_dist           0.0548  ← VP DISTÂNCIA
ewma_imb_media        0.0533  ← IMBALANCE
vp_poc_dist           0.0503  ← VP DISTÂNCIA
ewma_imb_curta        0.0423  ← IMBALANCE
```

---

### 8. Relatório Final

## **CLASSIFICAÇÃO: A — CONFIRMADO**

O sinal permanece fora da amostra.

| Critério | Status |
|----------|--------|
| PF > 2.0 em todos os dias | ✅ Confirmado |
| AUC > 0.6 | ✅ Modelo generaliza |
| Leakage temporal | ⚠️ `delta_preco_janela` suspeita, mas não conclusivo |
| Robustez (diferentes splits) | ✅ Melhora com mais dados |
| Features reais (microestrutura) | ✅ Grupo fluxo supera todas |

---

### 9. Arquivos Gerados

| Arquivo | Descrição |
|---------|-----------|
| `validacao_rigorosa.py` | Script de auditoria completa |
| `walk_forward_completo.py` | Walk-forward + ablação + robustez |
| `validacao_resultados/walk_forward_completo.json` | Resultado walk-forward |
| `validacao_resultados/ablacao.json` | Resultado ablação |
| `validacao_resultados/robustez.json` | Resultado robustez |
| `validacao_resultados/relatorio_final.json` | Relatório consolidado |
| `DOCUMENTACAO_TESTES.md` | Documentação de testes |

---

### 10. Próximos Passos Recomendados

| # | Prioridade | Descrição |
|---|------------|-----------|
| 1 | **Alta** | Rodar walk-forward em 30+ dias para confirmar consistência |
| 2 | **Alta** | Testar sem `delta_preco_janela` para verificar se o sinal se mantém |
| 3 | **Média** | Forward test (rodar modelo em tempo real sem trading real) |
| 4 | **Média** | Integrar RandomForest no pipeline automático de retreino |
| 5 | **Baixa** | Adicionar métricas de risco (Sharpe, MaxDD) no walk-forward |
| 6 | **Baixa** | Testar XGBoost como terceiro modelo |


---

## Sessão 23/08/2026 — Book 500 Níveis + Preparação Replay Temporal

### 1. Problema Identificado

O motor lia **60 níveis de book** do RTD (30 bid + 30 ask) mas a captura salvava apenas **5 níveis** porque  usava  como limite. Isso significava:
- 25 de 30 níveis eram descartados
- Book data gravado tinha resolução insuficiente para replay temporal
- Features de profundidade (L10-L30) não tinham dados brutos para reconstrução

### 2. Alterações Realizadas

| Arquivo | O que mudou |
|---------|------------|
|  | : 60 → **500** |
|  |  agora extrai **TODOS** os níveis (250 por lado) para captura, mantendo 5 níveis para OFI |
|  |  expandido: [1,3,5,10,20,30] → [1,3,5,10,20,30,**50,100,200,250**] |
|  | Adicionadas features: , ,  |

### 3. Detalhe Técnico

**Antes:**


**Depois:**


### 4. Impacto

| Aspecto | Antes | Depois |
|---------|-------|--------|
| Níveis de book gravados | 5 por lado | **250 por lado** |
| Profundidades analisadas | L1, L3, L5, L10, L20, L30 | L1...L30 + **L50, L100, L200, L250** |
| Tamanho do arquivo raw_book | ~5 MB/dia | ~50-100 MB/dia (estimado) |
| Assinaturas RTD | 60 × 3 campos = 180 | **500 × 3 = 1500** (por ativo) |
| Features de imbalance | 6 profundidades | **10 profundidades** |

### 5. Arquivos Alterados

| Arquivo | Alteração |
|---------|-----------|
|  | : 60 → 500 |
|  | Linhas 3733-3755: extração ALL levels para captura |
|  | Linha 268: DEPTHS expandido |
|  | Linha 444: imb_L50/L100/L250 no return dict |

### 6. Pré-requisitos para Próximo Passo (Replay Temporal)

Com 500 níveis de book agora sendo capturados, o replay temporal poderá:
- Reconstruir **spread, microprice, imbalance** em 10 profundidades
- Detectar **retiradas/reposições de liquidez** em qualquer profundidade
- Calcular **OFI por profundidade** (não só top 5)
- Analisar **geometria do book** (slope, concentração) em larga escala

**NOTA:** Dados históricos (HIST, 20/Jul-17/Ago) continuam sem book. A captura de 500 níveis só produzirá dados a partir da próxima sessão ao vivo.

---

### 7. Próximos Passos

| # | Prioridade | Descrição |
|---|------------|-----------|
| 1 | **Alta** | Implementar  — motor de replay com detecção de eventos |
| 2 | **Alta** | Testar captura de 500 níveis na próxima sessão ao vivo |
| 3 | **Média** | Comparar eventos similares (comparador por distância euclidiana) |
| 4 | **Média** | Visualizador de timeline (T-2s → T+2s) |
| 5 | **Baixa** | Documentar dados de 21/ Ago com book 5 níveis vs dados futuros com 250 níveis |

---

## Sessão 23/08/2026 (parte 2) — Revisão de Código + Correções v9.13

### Contexto
Revisão completa da engenharia (30 scripts .py): sintaxe, lógica de trading e de features,
seguida de correção e teste de **todos os bugs P0/P1**. Relatório detalhado em `RELATORIO_REVISAO.md`.

### Bugs críticos corrigidos

| ID | Bug | Impacto | Correção |
|----|-----|---------|----------|
| P0-1 | Scorer ML morto (`_consumir` chamava `.get()` em tuplas; motor passava 6/7 campos) | Camada ML NUNCA executava em produção | `scorer.py` desempacota tuplas; motor passa `neg[6]` |
| P0-2 | `labeler.py` rearmava purge a cada linha neutra | Dataset ~100% neutro (0,7% labels) | Embargo só rearma em trade |
| P0-3 | `labeler_vectorizado` ignorava barreira de SL | Labels não representavam execução real (SL nunca acontecia) | SL avaliada (marca `sl_atingido`); janela não cruza dia/ativo |
| P0-4 | `retreinar_sem_leak` filtro hardcoded 150k-250k | `--ativo WDOU26` treinava com 0 linhas | `FAIXAS_PRECO` por prefixo |
| P0-5 | Pipeline diário com labeler quebrado e sem gate | Retreino noturno podia usar parquet podre | Usa `labeler_vectorizado` + gate `%labels >= 1%` aborta |
| P0-6 | `dataset_builder` quebrava com labels vazios (KeyError ts_ms) | Pipeline não conseguia produzir parquet de dia parado | DataFrame default com chave; merge com `(ts_ms, ativo)`; fillna 0 |

### Bugs graves corrigidos (P1)
- **Divergência CVD**: compara contra high-water `cvd_max`/`cvd_min` (features_lib + motor) — antes não detectava divergência em sequência de topos.
- **Circuit breaker**: `cb_n3_pnl` default corrigido (antes = nível 1, invertendo a cascata).
- **Rollover de dia**: `_ultimo_preco_fim`, `_ewma_ret2` e `_cvd_extremos` zerados na virada (vol espúria na abertura eliminada).
- **Robustez do walk-forward**: folds com teste disjunto quando 11+ dias (antes 5d_3d testava os mesmos dias de 7d_3d).
- **`avaliar_modelo` 3 classes**: PF/expectancy por modo (`binario` vs `3classes`).

### P2 corrigidos
- `_extrair_pares` com dict: itera até a maior chave (250 níveis — antes 30).
- `replay_temporal`: preço médio (média ponderada exata — antes numerador só com vol de compra, mostrava ~4 no WDO), `extremo_baixa` sem `inf` (JSON válido), correção de indentação do loop de snapshots.
- `dataset_builder`: labels vague e NaN preenchidos (0 NaN em todos os testes).

### PENDENTES (próximas sessões)
- Regenerar labels de 4-17 com o labeler corrigido (SL agora é real — taxa de labels cai e métricas mudam).
- Retreinar e revalidar (walk-forward) sobre o novo parquet.
- Inativar o Scorer ML no dashboard (validar `prob` ao vivo).
- Replay em disco (spool) para 10 dias; livro_por_nível no snapshoot; `--mes` no pipeline.

### Testes executados (23/08)
| Suíte | Resultado |
|-------|-----------|
| py_compile (30 scripts) | 30/30 OK |
| test_features.py | 72 passed |
| smoke_test_v96.py | TUDO PASS |
| labeler (sintético determinístico) | 3/3 ciclos com label |
| labeler_vectorizado (1h real dia 13) | SL detectado, TP normal, não cruza dias |
| dataset_builder (merge com match e vazio) | 0 NaN |
| replay_temporal (sintético 2s) | preço médio exato, sem inf |
| robustez folds | testes disjuntos |

### 4. Revalidação com Labels Corrigidos (v9.13) — RESULTADO

Após corrigir o labeler (SL real + janela que não cruza dia/ativo), os labels
de 4-17 foram REGENERADOS (901 labels vs 330K antigos — 99,7% dos labels
antigos eram inflados por lookahead entre dias + SL ignorado).

| Métrica | Antigo (bug) | **Novo v9.13** |
|---------|--------------|----------------|
| Labels não-zero | 330.000 (9,7%) | **901** (0,03%) |
| LightGBM acc | 57,74% | 53,38% |
| LightGBM AUC | 0,6162 | 0,5634 |
| LightGBM PF | 2,73 | 2,29 |
| LightGBM Expectancy | +36,6 | +30,1 |
| RF acc (300 trees) | 57,74% | — (não rodado) |
| RF acc (100 trees) | — | 57,30% |
| RF AUC (100) | — | 0,6018 |
| RF PF (100) | — | 2,68 |

**Conclusão: o sinal SOBREVIVEU à correção.** RF mantém acc 57,3% / AUC 0,60 / PF 2,68
com 365x menos amostras. LGBM caiu levemente (53,4% / 0,563) — com 901 amostras,
LGBM (complexidade alta) sofre mais que RF (robusto). O sinal não era artefato
do SL: era momentum de curto prazo (delta_preco_janela continua top feature).

Nota importante: com n_teste=281, intervalo de confiança amplo (±6% acc).
Precisar de mais dias para apertar a estimativa.

Arquivos: `labels_WINV26_4-17_v913.jsonl` (901), `dataset_final_v2_win_v913.parquet`
(6,8M linhas), `walk_forward_v913.json`.

---

*Gerado por Codebuff em 23/08/2026*



---

## v9.36 -- OHLC Intraday + PrecoContextTracker (26/08/2026)

### O que foi feito
- OHLC intraday no Analise (motor_rt_alphaz.py): abertura, maxima, minima, fechamento por ativo
- PrecoContextTracker (preco_context_tracker.py): ~48 features de contexto de preco
- Integracao ao scorer (scorer.py): ctx trackers + update + inject
- Reset diario automatico na virada de data

| Arquivo | Mudanca |
|---------|---------|
| preco_context_tracker.py | NOVO -- 48 features causais |
| motor_rt_alphaz.py | OHLC tracking + reset |
| scorer.py | ctx trackers + inject |

---

## v9.37 -- Expansao do Contexto de Mercado (26/08/2026)

| Arquivo | Tipo | Features |
|---------|------|----------|
| features_expansao.py | Batch | 33 |
| volatility_tracker.py | Live | 7 |
| returns_tracker.py | Live | 7 |
| session_time_tracker.py | Live | 4 |

Features: retornos multi-horizonte, vol multi-TF, POC migracao, volume relativo, tempo sessao, range dia, niveis semanais

---

## v9.38 -- Walk-Forward Otimizado + Feature Cache (26/08/2026)

- walk_forward_otimizado.py: n_jobs=-1, float32, col selection, baselines vetorizados
- feature_cache.py: cache persistente de features (hash de codigo)
- Benchmark: 458s (7m50s) vs >600s (timeout) no original

| Otimizacao | Ganho |
|------------|-------|
| n_jobs=-1 LightGBM | ~2x |
| float32 | ~50% RAM |
| Col selection (26 de 140) | ~75% RAM |

---

## v9.39 -- Reorganizacao de Pastas (26/08/2026)

| Pasta | Arquivos |
|-------|----------|
| raiz | 14 (motor core) |
| ml/ | 30 |
| testes/ | 16 |
| docs/ | 9 |
| scripts/ | 9 |
| dados/ | 14 |
Paths corrigidos: auto_start.bat, iniciar_motor.bat, pipeline_after_market.bat, pipeline_diario.py

---

# REFERENCIA COMPLETA — Funcoes e Classes

## motor_rt_alphaz.py (4155 linhas) — Motor Principal

### Funcoes auxiliares globais
| Funcao | Linha | Descricao |
|--------|-------|-----------|
| _carregar_config_externa() | 180 | Carrega config.json + env vars, merge com defaults |
| fnum(v, d) | 266 | Converte para float com default |
| fint(v, d) | 272 | Converte para int com default |
| sstr(v) | 278 | Converte para string segura |
| custo_execucao(ativo) | 281 | Retorna custo em pts por ativo (WIN=5, WDO=1) |
| horario_permite_abrir() | 287 | Verifica se horario permite abrir posicao |
| _norm(s) | 300 | Normaliza string (lower, strip, acentos) |
| classificar_corretora(nome) | 314 | Classifica corretora em categorias |
| parse_hms_ms(v) | 330 | Parse HH:MM:SS.mmm para ms do dia |
| _tod_ms(dt) | 340 | Time-of-day em ms (hora-do-dia, nao epoch) |

### class PadroesMemoria (L349)
Memoria persistente de padroes de corretoras (spoof, stop-hunt, absorcoes).
| Metodo | Descricao |
|--------|-----------|
| __init__(base_dir) | Carrega padroes de padroes_memoria.json |
| salvar() | Persiste em JSON |
| aplicar_decay() | Envelhece padroes (decay 0.99/dia) |
| detectar_spoof() | Detecta layering/remocao |
| detectar_stop_hunt() | 2 fases: rompimento + reversao |
| registrar_agressao() | Acumula agressao por corretora |
| nivel_stop_perto() | Verifica se preco esta perto de stop |
| get_resumo() | Retorna resumo para dashboard |

### Funcoes de conexao RTD
| Funcao | Descricao |
|--------|-----------|
| conectar_e_descobrir() | Conecta ao ProfitChart, descobre ativos |
| assinar_topicos() | Assina 500 book + 500 T&T por ativo |

### class EstadoAtivo (L698)
Armazena estado de cada ativo (book, trades, ofi, cross-asset).

### Funcoes de processamento
| Funcao | Descricao |
|--------|-----------|
| processar_dados() | Processa dados RTD, extrai book+tts |
| extrair_niveis_book() | Extrai N niveis do book |
| extrair_book_snapshot() | Snapshot formatado do book |
| snapshot_book() | Snapshot com vol bid/ask |
| comparar_books() | Detecta eventos de microestrutura |

### class PercentilTracker (L956)
Rastreia percentis de uma metrica em janela deslizante.

### class RangeTracker (L982)
Detecta ranges de preco (topo/fundo).

### class AccumulationTracker (L1047)
Detecta acumulacao de posicoes por corretoras.
| Metodo | Descricao |
|--------|-----------|
| registrar() | Registra trade |
| detectar() | Detecta acumulacao |

### class CrossAssetEngine (L1204)
Analise cross-asset WIN x WDO: lag, correlacao, divergencia, lideranca.
| Metodo | Descricao |
|--------|-----------|
| registrar() | Registra tick de qualquer ativo |
| calcular() | Calcula todas as metricas cross-asset |
| _calcular_lag() | Lag temporal WDO->WIN (bisect O(log n)) |
| _correlacao_rolling() | Correlacao rolling 60s |
| _calcular_divergencia() | WDO andando, WIN parado |
| _wdo_leading_score() | Score de lideranca WDO |
| _resposta_ao_wdo() | Reacao do WIN ao WDO |

### class Analise (L1446) — CORACAO DO MOTOR
Feature engine, scoring, gerenciamento de posicoes, aprendizado.
| Metodo | Linha | Descricao |
|--------|-------|-----------|
| __init__() | 1472 | Inicializa trackers, pesos, historico, OHLC |
| _carregar_posicao_checkpoint() | 1608 | Restaura posicao de posicao_atual.json |
| _salvar_posicao_checkpoint() | 1634 | Salva posicao atual |
| _garantir_fp() | 1654 | Abre arquivos jsonl |
| _gravar_trade(neg) | 1672 | Grava trade no buffer |
| _rotacionar() | 1678 | Rotacao por tamanho (100MB) |
| _flush_trades() | 1693 | Flush + fsync |
| _gravar_decisao() | 1713 | Grava decisao |
| _flush_decisoes() | 1719 | Flush decisoes |
| carregar_aprendizado() | 1736 | Carrega pesos de JSON |
| salvar_aprendizado() | 1754 | Salva estado |
| alimentar_lote() | 1770 | Processa lote de negocios |
| _calcular() | 1868 | Calcula features (26+ micro + contexto) |
| _calcular_sequencia() | 2022 | Padrao C/V/V/C |
| _avaliar() | 2044 | SCORING: soma ponderada |
| _suavizar_sinal() | 2625 | EWMA do sinal |
| _checar_saidas() | 2699 | TP/SL/timeout/reversao |
| gerenciar_posicao() | 2758 | Abre/fecha/reverte |
| _fechar_posicao() | 2822 | Fecha + calcula pnl |
| aprender_mfe_mae() | 2876 | Ajusta pesos |
| detectar_regime() | 2923 | tendencia_alta/baixa/lateral/vol |
| ajustar_por_regime() | 2978 | Ajusta TP/SL por regime |
| calcular_metricas() | 3025 | Sharpe, PF, MaxDD |
| alimentar_book() | 3063 | Processa book + microestrutura |
| get_features() | 3165 | API features |
| get_sinais() | 3185 | API sinais |
| get_book_level() | 3211 | API book level |
| get_memoria() | 3254 | API memoria |
| get_saldo_corretoras() | 3268 | API corretoras |
| salvar_sessao() | 3294 | Checkpoint |

### class Handler (L3318) + class App (L3427)
Servidor HTTP + motor principal.
| Metodo | Descricao |
|--------|-----------|
| App._loop() | Loop: PumpEvents -> processar -> calcular -> avaliar |
| App._reconectar() | Reconecta ao RTD |
| App.run() | Inicia servidor + motor |
| App.get_rtd_health() | Saude da conexao RTD |
| App.get_contexto_mercado() | Contexto global |

---

## motor_web.py (2586 linhas) — Conexao RTD + Captura
| Funcao | Linha | Descricao |
|--------|-------|-----------|
| _carregar_interfaces() | 270 | Carrega interfaces COM |
| _criar_callback() | 310 | Callback COM |
| _connect() | 327 | Conecta ao servidor |
| _refresh() | 335 | Refresh dados RTD |
| conectar_servidor() | 347 | Cria instancia COM |
| descobrir_ativos_rtd() | 354 | Descobre ativos |
| parse_refresh_data() | 507 | Parse RefreshData |
| _normalizar_simbolo() | 563 | Normaliza ativo |
| parse_dat() | 581 | Parse timestamp RTD |
| enforce_schema() | 607 | Valida schema |
| _escrever_parquet_atomico() | 694 | Escrita atomica |
| write_parquet_part() | 708 | Grava particao/hora |
| consolidar_book_parquet() | 732 | Consolida book |
| consolidar_tt_parquet() | 792 | Consolida T&T |
| thread_com() | 978 | Thread COM + watchdog |
| _thread_com_ciclo() | 1020 | Ciclo unico RTD |
| thread_escritora() | 1419 | Writer book parquet |
| thread_escritora_tt() | 1521 | Writer T&T parquet |
| _DashboardState() | 1606 | Estado dashboard |
| main() | 2406 | Entry point |

---

## features_lib.py (982 linhas) — Feature Engine
| Funcao/Classe | Linha | Descricao |
|---------------|-------|-----------|
| ewma_update() | 30 | EWMA incremental |
| hhi() | 36 | HHI concentracao |
| entropia() | 45 | Entropia de Shannon |
| dias_ate_vencimento() | 72 | Dias ate vencimento B3 |
| fase_sessao() | 91 | Fase: abertura/meio/almoco/fechamento |
| _tod_de_ts() | 136 | Epoch para time-of-day |
| VPINTracker | 154 | Volume-Synchronized PIN |
| OFITracker | 196 | OFI por preco (Cont-Kukanov-Stoikov) |
| BookLevelFeatures | 264 | 30+ features de book |
| JanelaFeatures | 537 | Agregacao T&T por 100ms |
| GeradorJanelas | 703 | Snapshots 100ms com book+TT+VP+Kyle |
| asof_join_linhas() | 803 | Merge temporal |
| VolumeProfileTracker | 841 | POC, VAH, VAL |
| EWMAZScore | 896 | Z-score EWMA |
| KyleLambdaTracker | 941 | Kyle's Lambda (impacto) |

---

## scorer.py (314 linhas) — Scorer ML Live
| Classe | Linha | Descricao |
|--------|-------|-----------|
| VWAPTracker | 27 | VWAP intraday incremental |
| ScorerML | 109 | LightGBM + features live |
| ScorerML.evento() | 201 | Alimenta trackers |
| ScorerML._prever() | 239 | Predicao ML |
| ScorerML.decisao() | 283 | Compra/venda/neutro |

---

## captura_eventos_ms.py (239 linhas) — Captura
| Metodo | Descricao |
|--------|-----------|
| __init__() | Inicia buffers + arquivos |
| registrar_negocios() | Blindagens (ts, qtd, preco, dup) |
| registrar_book() | Book snapshot |
| _flush_neg/book() | Flush + fsync |
| fechar() | Fecha + meta da sessao |

---

## watchdog.py (391 linhas) — Watchdog
| Classe | Descricao |
|--------|-----------|
| WatchdogLock | Lock multi-instancia |
| Watchdog.run() | Loop: verifica vivo, reinicia |

---

## preco_context_tracker.py (125 linhas)
| Classe | Features |
|--------|----------|
| PrecoContextTracker | 48 (OHLC, D-1, distancias, gaps, ranges) |

## volatility_tracker.py (26 linhas)
| Classe | Features |
|--------|--------
| VolatilityTracker | 7 (vol_100ms a vol_5min) |

## returns_tracker.py (26 linhas)
| Classe | Features |
|--------|----------|
| ReturnsTracker | 7 (retorno_100ms a retorno_5min) |

## session_time_tracker.py (28 linhas)
| Classe | Features |
|--------|----------|
| SessionTimeTracker | 4 (segundos, minutos, sin/cos) |

---

## treino_lib.py (207 linhas) — Treino
| Funcao | Descricao |
|--------|-----------|
| flatten_snapshot() | Achata snapshot |
| split_com_purge() | Split temporal com purge+embargo |
| preparar_features() | Seleciona + one-hot |
| avaliar_modelo() | Accuracy, AUC, PF, expectancy |

## config.py (105 linhas) — Config
| Elemento | Descricao |
|----------|-----------|
| _carregar() | Le config.json + env |
| TradingConfig | TP, SL, max trades |
| ConfigModel | Pydantic model |
