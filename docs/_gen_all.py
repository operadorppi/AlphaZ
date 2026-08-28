# -*- coding: utf-8 -*-
import os

files = {}

files['ESTADO_ATUAL.md'] = """# Estado Atual do Sistema

**Ultima atualizacao:** 28/08/2026
**Versao:** v10.4 (Agnóstica)

## Status Geral

| Componente | Status |
|-----------|--------|
| Motor principal | Funcional (Core v10 desacoplado) |
| Conexao RTD | Funcional via ProfitRTDAdapter (500 níveis L500) |
| Replay | Funcional via ReplayAdapter (Determinístico) |
| Features | 115+ (incluindo imb_L500 e Contexto Avançado) |
| Dashboard | Funcional (v10 com Telemetria de Latência) |
| Scorer ML | Funcional (Inferência por Gain, Telemetria Live) |
| Watchdog | Funcional (Monitoramento run_motor.py) |
| Task Scheduler | Funcional (8:45 start, 18:35 stop) |
| Pipeline pos-pregao | Corrigido (26/08) |
| Dataset labels | Regenerado (v939, sem WIN/WDO misturados) |
| Modelo treinado | DESATUALIZADO - precisa retreino |

## Estrutura de Pastas

raiz: 15 arquivos (v10 Core + Entry Points)
core/: 10 arquivos (Lógica de Domínio)
adapters/: 8 arquivos (Interfaces de Infra)
features/: 12 arquivos (Trackers Modularizados)
ml/: 32 (Pipeline de Treinamento)
testes/: 18 (Suíte de Testes v10)
docs/: 12 (documentacao)
"""

files['ARCHITECTURE.md'] = """# Arquitetura

## Fluxo Tempo Real

ProfitRTDAdapter (Windows) / ReplayAdapter (Offline)
  -> MarketEvent (TRADE/BOOK) 
  -> App._handle_market_event()
  -> MarketState (Features de Microestrutura L500)
  -> SignalEngine (Heurística + ScorerML Inference)
  -> PositionManager (Ações Operacionais)
  -> Dashboard (HTTP)

## Fluxo Offline

raw_negocios_ms -> batch_processor -> dataset_100ms
  -> labeler_vectorizado -> labels
  -> dataset_builder -> parquet
  -> walk_forward -> modelo

## Ciclo Diario

08:45 MotorAlphaz_Iniciar
09:00+ Pregao abre
18:35 MotorAlphaz_Parar
18:36 MotorAlphaz_Pipeline (6 passos)
"""

files['DATA_CONTRACTS.md'] = """# Contratos de Dados

## motor_web -> motor_rt_alphaz

Callback COM:
  ativo: str, tipo: str, dados: dict

## motor_rt_alphaz -> features_lib

Trade:
  tms: int, preco: float, qtd: int, lado: str, corretora: str

Snapshot saida:
  aggr_imb: float, cvd_total: int, delta_preco: float, ...

## features_lib -> motor_rt_alphaz (BookLevel)

  bid_levels: [(preco, vol), ...]
  ask_levels: [(preco, vol), ...]

Saida: spread, mid, microprice, imbalance, ofi, ...

## scorer -> motor_rt_alphaz

  probabilidade: float (0-1)
  sinal: int (-1, 0, +1)
  confianca: float (0-1)
"""

files['CONFIGURATION.md'] = """# Configuracao

## config.json (principais)

save_dir: D:\\MarketData\\mimo
web.port: 5001
ativos: [WINV26, WDOU26]
rtd.book_linhas: 500
rtd.tt_linhas: 500
trading.tp_pts: 100
trading.sl_pts: 50
trading.max_trades_dia: 15
cooldown_entre_trades_s: 45
limiar_confirmacao: 0.55
normalizar_score: false
ml_modelo: lightgbm

## Env Vars

SINAL_RT_DIR: diretorio save
PROFIT_DATA_DIR: diretorio ProfitChart
WEB_PORT: porta dashboard

## Prioridade

1. Env vars (sobrescreve tudo)
2. config.json
3. Defaults hardcoded
"""

files['RUNTIME.md'] = """# Runtime e Automacao

## Task Scheduler

MotorAlphaz_Iniciar: 08:45 seg-sex
MotorAlphaz_Parar: 18:35 seg-sex
MotorAlphaz_Pipeline: 18:36 seg-sex

## Watchdog

CHECK_INTERVAL: 10s
RESTART_DELAY: 45s
MAX_RESTARTS: 10/hora (com trava após motor funcional)
Nao roda fins de semana
Protecao multi-instancia

## Ciclo Diario

08:45 Motor liga
08:45-09:00 Pre-abertura
09:00+ Captura L500 + Latência monitorada + Scoring
18:35 Motor para
18:36 Pipeline (6 passos)
"""

files['DATA_PIPELINE.md'] = """# Pipeline de Dados

## Pipeline Offline

captura_eventos_ms -> batch_processor -> dataset_100ms
  -> labeler_vectorizado -> labels (triple barrier)
  -> dataset_builder -> dataset_final.parquet

## Labeler

TP WIN: 100 pts
TP WDO: 1 pt
SL: 50 pts
Janela: 30s
Purge: 10s

## Pipeline Diario (scripts/pipeline_diario.py)

1. Relatorio qualidade
2. Features batch
3. Labels (Labeler Vectorizado v9.14)
4. Dataset (Parquet v9.50)
5. Gate qualidade
6. Retreino (LightGBM)

Flags: --dry-run, --skip-batch, --dia, --save-dir
"""

files['MACHINE_LEARNING.md'] = """# Machine Learning

## Motor de Inferência (v10.4)

Tipo: LightGBM (Gradient Boosting)
Métrica de Importância: **Gain** (Ganho de Informação)
Telemetria: Latência de inferência < 5ms por evento.

## Features Críticas (Top por Gain)

1. vpin - Volume-weighted Probability of Informed Trading
2. dist_vwap_norm - Distância do preço para VWAP normalizada
3. cvd_total - Cumulative Volume Delta
4. aggr_imb - Desequilíbrio de agressão na janela
5. imb_L500 - Imbalance do Book em 500 níveis de profundidade
6. dist_ajuste_oficial_pts - Distância para ajuste B3 D-1
8. kyle_kyle_lambda (302) - Impacto
9. realized_vol_bps (236) - Volatilidade
10. ewma_imb_longa (224) - Imbalance

## Resultados (labels v939 - CORRETOS)

Baseline real: -10 pts/trade
AUC medio: 0.755
Modelo so gera trades quando confianca > 0.6

## Comparacao RF vs LGBM

RF: acc 57.7%, AUC 0.616, PF 2.73
LGBM: acc 52.2%, AUC 0.534, PF 2.19
Vencedor: RandomForest

## Ablacao

Grupo fluxo (8 features): AUC 0.6175, PF 2.79
Todas 29 features: AUC 0.6048, PF 2.63
Fluxo supera todas as 29!
"""

files['VALIDATION.md'] = """# Validacao e Causalidade

## Integridade Causal (Regra Zero)

115/115 features: OK (calculadas com dados <= t)
Deduplicação: Aplicada no Adapter para evitar bursts de volume irreais.
Nenhum leak direto confirmado

## Testes de Leakage (5 obrigatorios)

A: Negocio futuro nao muda features anteriores
B: Maxima futura nao muda features anteriores
C: VWAP final nao muda features anteriores
D: POC final nao muda features anteriores
E: Volume futuro nao muda features anteriores

Todos: PASS

## Classificacao Final

A - CONFIRMADO
PF > 2.0 em todos os dias
AUC > 0.6
Features reais de microestrutura
"""

files['TESTING.md'] = """# Testes

## Como Rodar

python -m pytest testes/ -v

## Status (28/08/2026)

test_features.py: 71 passed, 1 skipped
test_contexto_preco.py: 16 passed
test_contexto_avancado.py: 7 passed
test_imb_l500.py: 3 passed (Cálculo L500 validado)
test_scorer.py: 4 passed, 2 skipped
leakage_test.py: 5 passed (Rule Zero confirmada)
Total: 106 passed, 3 skipped (10.2s)

## Testes em Manutenção

test_b3_staleness: interfaces mudaram
test_book_writer: mocks desatualizados
test_com_watchdog: interfaces mudaram
test_config_flat: estrutura mudou
test_r2_aprendizado: API antiga
"""

files['API.md'] = """# API HTTP

Dashboard: http://127.0.0.1:5001/
Otimização: Suporte a ETag (304 Not Modified) para /api/all.

## Endpoints

GET /                - Dashboard HTML Pro (Live Telemetry)
GET /api/features    - Features todos ativos
GET /api/sinais      - Sinais e posicoes
GET /api/posicao     - Posicao atual
GET /api/book_level  - Book level + cross asset
GET /api/memoria     - Memoria do motor
GET /api/metricas    - Sharpe, PF, MaxDD
GET /api/saldo_corretoras - Saldo por corretora
GET /api/rtd_health  - Saude conexao RTD
GET /api/padroes     - Padroes detectados
GET /api/learning    - Estatisticas aprendizado
GET /api/contexto    - Contexto global
GET /api/ml_health   - Saúde do ScorerML (Top Gain Features)
GET /health          - Status geral
"""

files['OPERATIONS.md'] = """# Operacao Diaria

## Rotina Automatica

08:45 Motor liga
08:45-09:00 Pre-abertura
09:00+ Pregao
18:35 Motor para
18:36 Pipeline pos-pregao

## Checklist

Antes: motor rodando? RTD conectado? Dashboard acessivel?
Durante: dados atualizados? score variando? trades OK?
Apos: pipeline rodou? dataset atualizado? modelo retreinado?

## Comandos Utileis

tasklist | grep -i python     (verificar motor)
taskkill /IM python.exe /F    (matar motor)
python motor_rt_alphaz.py     (reiniciar)
curl http://127.0.0.1:5001/health  (verificar API)
python -m pytest testes/ -v   (testes)
"""

files['TROUBLESHOOTING.md'] = """# Troubleshooting

## Problemas Conhecidos

CRITICO:
1. Labels WIN/WDO misturados -> CORRIGIDO (v939)
2. Modelo treinado com dados corrompidos -> PENDENTE retreino
3. config_models.py vazio -> Restaurar backup

ALTO:
4. Motor nao conecta RTD -> Abrir ProfitChart + janelas RTD
5. Pipeline nao roda 18:36 -> Task Scheduler path errado -> CORRIGIDO
6. Watchdog mata motor sadio -> Aumentar intervalo

MEDIO:
7. Score sempre 0 -> Modelo nao carregado -> Verificar config.json
8. Dashboard trava -> Recarregar (F5)

## Diagnostico Rapido

tasklist | grep -i python
curl http://127.0.0.1:5001/api/rtd_health
tail -20 motor_stdout.log
"""

files['DECISIONS.md'] = """# Decisoes Arquiteturais

D1: Motor em arquivo unico (4155 linhas)
  Justificativa: state sharing em tempo real

D2: features_lib como codigo canonico (live + batch)
  Justificativa: evita divergencia live/batch

D3: Labeler vectorizado NumPy (180x mais rapido)
  Justificativa: 6.8M linhas em 10s vs 30min

D4: Walk-forward com cache em disco
  Justificativa: re-run em segundos vs minutos

D5: Dashboard HTML puro (sem framework)
  Justificativa: zero dependencia externa

D6: Captura em JSONL (nao Parquet)
  Justificativa: append-only, tolerante a crashes

D7: Sem dedup 
