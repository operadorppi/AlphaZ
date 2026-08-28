# API HTTP

Dashboard: http://127.0.0.1:5001/

## Endpoints

GET /                - Dashboard HTML
GET /api/features    - Features de todos os ativos
GET /api/sinais      - Sinais e posicoes
GET /api/posicao     - Posicao atual
GET /api/book_level  - Features de book level + cross asset
GET /api/memoria     - Memoria do motor (negocios, trades, anomalias)
GET /api/metricas    - Sharpe, Profit Factor, MaxDD
GET /api/saldo_corretoras - Saldo por corretora
GET /api/rtd_health  - Saude da conexao RTD
GET /api/padroes     - Padroes detectados (spoof, stop-hunt)
GET /api/learning    - Estatisticas de aprendizado
GET /api/contexto    - Contexto global de mercado
GET /health          - Status geral

## Paineis do Dashboard

| Painel | Dados |
|--------|-------|
| Acao | COMPRA/VENDA/AGUARDE + cor |
| Preco | Preco atual + TP/SL/RISCO/RETORNO |
| Features | AGRESSAO, EFICIENCIA, PERSISTENCIA, HHI |
| Book Level | SPREAD, IMB_L1, MICRO, HHI |
| Trade Metrics | AVG_SZ, SEQ, VEL |
| Cross Asset | LAG, CORR, DIV |
| Score | Confianca + Score + R:R |
| Posicao | Box com P&L, TP/SL, tempo |
| Aprendizado | Acuracia, ultimos trades, pesos |
| Corretoras | WIN e WDO com comprado/vendido/saldo |
| RTD Alert | Vermelho se desconectado, Laranja se pre-abertura |
