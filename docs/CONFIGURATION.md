# Configuracao

## config.json

save_dir: D:\MarketData\mimo
web: host=127.0.0.1, port=5001
ativos: [WINV26, WDOU26]
rtd: book_linhas=500, tt_linhas=500
trading: tp_pts=100, sl_pts=50, max_trades_dia=15, max_drawdown_dia=-300
custo_execucao: WIN=5.0, WDO=1.0
horarios: abertura_fim=10:00, almoco=12:00-13:30, fechamento=16:30

## Anti-overtrading

cooldown_entre_trades_s: 45
min_holding_reversao_s: 90
confianca_min_reversao: 0.75
limiar_confirmacao: 0.55
limiar_reset: 0.15
max_salto_preco_pct: 0.15
faixas_preco: WIN=150K-250K, WDO=4K-8K

## Circuit Breaker

max_perdas_consecutivas: 3
max_trades_dia: 15
max_drawdown_dia_pontos: -500

## Aprendizado

aprendizado_delta: 0.02
aprendizado_decay: 0.998
min_amostras: 5

## ML

ml_modelo: lightgbm
normalizar_score: false

## Env Vars

SINAL_RT_DIR: diretorio save (D:\MarketData\mimo)
PROFIT_DATA_DIR: diretorio ProfitChart (D:\MarketData\Profit)
WEB_PORT: porta dashboard (5001)

## Prioridade

1. Env vars (sobrescreve tudo)
2. config.json
3. Defaults hardcoded
