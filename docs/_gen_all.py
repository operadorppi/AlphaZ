# -*- coding: utf-8 -*-
import os

files = {}

files['ESTADO_ATUAL.md'] = """# Estado Atual do Sistema

**Ultima atualizacao:** 26/08/2026
**Versao:** v9.39+

## Status Geral

| Componente | Status |
|-----------|--------|
| Motor principal | Funcional |
| Conexao RTD | Funcional (500 niveis book + 500 T&T) |
| Features | 102 ao vivo |
| Dashboard | Funcional (porta 5001) |
| Scorer ML | Funcional (LightGBM) |
| Watchdog | Funcional |
| Task Scheduler | Funcional (8:45 start, 18:35 stop) |
| Pipeline pos-pregao | Corrigido (26/08) |
| Dataset labels | Regenerado (v939, sem WIN/WDO misturados) |
| Modelo treinado | DESATUALIZADO - precisa retreino |

## Estrutura de Pastas

raiz: 14 arquivos (motor core)
ml/: 29 (pipeline ML)
testes/: 16 (suite testes)
docs/: 12 (documentacao)
scripts/: 9 (automacao)
dados/: 13 (resultados)
"""

files['ARCHITECTURE.md'] = """# Arquitetura

## Fluxo Tempo Real

RTD COM -> motor_web.py (PumpEvents)
  -> processar_dados() -> extrair_niveis_book()
  -> alimentar_book() -> BookLevelFeatures
  -> alimentar_lote() -> JanelaFeatures
  -> _calcular() -> 102 features
  -> _avaliar() -> score
  -> gerenciar_posicao() -> TP/SL
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
RESTART_DELAY: 10s
MAX_RESTARTS: 10/hora
Nao roda fins de semana
Protecao multi-instancia

## Ciclo Diario

08:45 Motor liga
08:45-09:00 Pre-abertura
09:00+ Captura + features + scoring
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
Purge: 0s

## Pipeline Diario (scripts/pipeline_diario.py)

1. Relatorio qualidade
2. Features batch
3. Labels (labeler_vectorizado)
4. Dataset (parquet)
5. Gate qualidade
6. Retreino (LightGBM)

Flags: --dry-run, --skip-batch, --dia, --save-dir
"""

files['MACHINE_LEARNING.md'] = """# Machine Learning

## Walk-Forward Otimizado

n_jobs=-1, float32, col selection, baselines vetorizados
Benchmark: 458s (7m50s) vs >600s

## Features (top 10)

1. vp_vp_total (682) - Volume Profile
2. vpin (510) - Fluxo
3. cvd_total (504) - Delta
4. preco_ultimo (440) - Preco
5. vp_poc_dist (381) - POC
6. vp_val_dist (372) - Value Area
7. vp_vah_dist (340) - Value Area
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

## Leakage

25/29 features: OK (calculadas com dados <= t)
delta_preco_janela: SUSPEITA (17% importance, momentum 100ms)
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

## Status (26/08/2026)

test_features.py: 71 passed, 1 skipped
test_contexto_preco.py: 16 passed
test_contexto_avancado.py: 7 passed
test_scorer.py: 4 passed, 2 skipped
Total: 98 passed, 3 skipped (9.80s)

## Testes Antigos (nao executados)

test_b3_staleness: interfaces mudaram
test_book_writer: mocks desatualizados
test_com_watchdog: interfaces mudaram
test_config_flat: estrutura mudou
test_r2_aprendizado: API antiga
"""

files['API.md'] = """# API HTTP

Dashboard: http://127.0.0.1:5001/

## Endpoints

GET /                - Dashboard HTML
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
