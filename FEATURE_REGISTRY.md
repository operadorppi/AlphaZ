# Feature Registry

**Total:** 70 features
**Causais:** 70
**Não-causais:** 0

## Features por Origem

### BOOK (15 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `hhi_book` | 1.0 | float64 | ratio | — | ✅ | HHI de concentração do book (distribuição de volume) |
| `imb_ponderado` | 1.0 | float64 | ratio | — | ✅ | Imbalance ponderado por profundidade (decay geométrico 0.85^i) |
| `imbalance` | 1.0 | dict | ratio | — | ✅ | Imbalance por profundidade (L1, L3, L5, L10, etc.) |
| `liq_dist_ask` | 1.0 | float64 | pts | — | ✅ | Distância média ponderada do ask ao mid |
| `liq_dist_bid` | 1.0 | float64 | pts | — | ✅ | Distância média ponderada do bid ao mid |
| `micro_drift_bps` | 1.0 | float64 | bps | — | ✅ | Drift do microprice em bps (microprice - mid) / mid * 10000 |
| `micro_drift_ewma` | 1.0 | float64 | bps | — | ✅ | EWMA do microprice drift (suavizado) |
| `microprice` | 1.0 | float64 | pts | — | ✅ | Microprice: preço ponderado por volumes bid/ask |
| `microprice_vs_mid` | 1.0 | float64 | pts | — | ✅ | Diferença microprice - mid (pressão compradora/vendedora) |
| `ofi` | 1.0 | float64 | contracts | — | ✅ | Order Flow Imbalance: desequilíbrio de ordens limitadas |
| `slope_ask` | 1.0 | float64 | ratio | — | ✅ | Slope do book ask: (near - far) / (near + far) |
| `slope_bid` | 1.0 | float64 | ratio | — | ✅ | Slope do book bid: (near - far) / (near + far) |
| `spread` | 1.0 | float64 | pts | — | ✅ | Spread bid-ask: best_ask - best_bid |
| `vel_ask` | 1.0 | float64 | contracts/s | — | ✅ | Velocidade de mudança do volume ask (EWMA) |
| `vel_bid` | 1.0 | float64 | contracts/s | — | ✅ | Velocidade de mudança do volume bid (EWMA) |

### CROSS_ASSET (3 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `cross_corr_aggr` | 1.0 | float64 | ratio | — | ✅ | Correlação rolling de agressão entre WIN e WDO |
| `cross_divergencia` | 1.0 | float64 | pts | — | ✅ | Divergência de preço entre WIN e WDO |
| `cross_lag` | 1.0 | float64 | ms | — | ✅ | Lag temporal entre WIN e WDO (ms) |

### INSTITUTIONAL (19 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `amplitude_dia_pts` | 1.0 | float64 | pts | — | ✅ | Amplitude do dia (máxima - mínima) |
| `bounces_ajuste_norm` | 1.0 | float64 | ratio | — | ✅ | Número de bounces no ajuste normalizado (0-1) |
| `bounces_vwap_norm` | 1.0 | float64 | ratio | — | ✅ | Número de bounces no VWAP normalizado (0-1) |
| `dist_abertura_norm` | 1.0 | float64 | ticks | — | ✅ | Distância à abertura normalizada (em ticks) |
| `dist_abertura_pts` | 1.0 | float64 | pts | — | ✅ | Distância à abertura do dia em pontos |
| `dist_ajuste_norm` | 1.0 | float64 | ticks | — | ✅ | Distância ao ajuste normalizada (em ticks) |
| `dist_ajuste_pts` | 1.0 | float64 | pts | — | ✅ | Distância ao ajuste (settlement D-1) em pontos |
| `dist_maxima_pts` | 1.0 | float64 | pts | — | ✅ | Distância à máxima do dia em pontos |
| `dist_minima_pts` | 1.0 | float64 | pts | — | ✅ | Distância à mínima do dia em pontos |
| `dist_vwap_norm` | 1.0 | float64 | ticks | — | ✅ | Distância ao VWAP normalizada (em ticks de 5pts) |
| `dist_vwap_pts` | 1.0 | float64 | pts | — | ✅ | Distância ao VWAP em pontos |
| `posicao_relativa` | 1.0 | float64 | ratio | — | ✅ | Posição relativa no range do dia (0=fundo, 1=topo) |
| `reversao_perto_ajuste` | 1.0 | float64 | bool | — | ✅ | Sinal de reversão perto do ajuste |
| `reversao_perto_vwap` | 1.0 | float64 | bool | — | ✅ | Sinal de reversão perto do VWAP (1.0 se <15pts e direção oposta) |
| `zona_abertura` | 1.0 | int64 | category | — | ✅ | Zona em relação à abertura: 0=far, 1=near, 2=at |
| `zona_ajuste` | 1.0 | int64 | category | — | ✅ | Zona em relação ao ajuste: 0=far, 1=near, 2=at |
| `zona_maxima` | 1.0 | int64 | category | — | ✅ | Zona em relação à máxima: 0=far, 1=near, 2=at |
| `zona_minima` | 1.0 | int64 | category | — | ✅ | Zona em relação à mínima: 0=far, 1=near, 2=at |
| `zona_vwap` | 1.0 | int64 | category | — | ✅ | Zona em relação ao VWAP: 0=far, 1=near, 2=at |

### KYLE (1 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `kyle_kyle_lambda` | 1.0 | float64 | pts/contract | — | ✅ | Kyle's Lambda: impacto de preço / liquidez (regressão ΔP ~ λ·V) |

### METADATA (6 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `ativo` | 1.0 | object | string | — | ✅ | Símbolo do ativo |
| `dias_ate_venc` | 1.0 | int64 | days | — | ✅ | Dias até o vencimento do contrato |
| `fase_sessao` | 1.0 | object | category | — | ✅ | Fase da sessão: abertura, meio, fechamento |
| `preco_fim` | 1.0 | float64 | pts | — | ✅ | Preço final da janela (último trade) |
| `preco_ini` | 1.0 | float64 | pts | — | ✅ | Preço inicial da janela (primeiro trade) |
| `time_ms` | 1.0 | int64 | ms | — | ✅ | Timestamp da janela em milissegundos |

### OFI (2 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `ofi_ewma` | 1.0 | float64 | contracts | — | ✅ | OFI suavizado por EWMA (decay 0.92) |
| `ofi_total` | 1.0 | float64 | contracts | — | ✅ | OFI total: soma de (bid_event - ask_event) por nível |

### REGIME (1 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `regime` | 1.0 | object | category | — | ✅ | Regime de mercado detectado |

### TRADE (18 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `absorcao_ratio` | 1.0 | float64 | ratio | 1000ms | ✅ | Ratio de absorção: volume passivo / volume total |
| `aggr_imb` | 1.0 | float64 | ratio | 1000ms | ✅ | Imbalance de agressão: (vol_comprador - vol_vendedor) / vol_total |
| `avg_trade_size` | 1.0 | float64 | contracts | 1000ms | ✅ | Tamanho médio do trade |
| `cvd_div` | 1.0 | float64 | ratio | 1000ms | ✅ | Divergência entre preço e CVD (possível reversão) |
| `cvd_total` | 1.0 | int64 | contracts | 1000ms | ✅ | Cumulative Volume Delta acumulado |
| `delta_preco_janela` | 1.0 | float64 | pts | 1000ms | ✅ | Variação de preço na janela (preco_fim - preco_ini) |
| `fluxo_persist` | 1.0 | float64 | ratio | 1000ms | ✅ | Persistência do fluxo: se o lado dominante se mantém |
| `hhi` | 1.0 | float64 | ratio | 1000ms | ✅ | Índice Herfindahl-Hirschman de concentração de volume |
| `max_trade_size` | 1.0 | int64 | contracts | 1000ms | ✅ | Tamanho máximo do trade na janela |
| `n` | 1.0 | int64 | count | 1000ms | ✅ | Número de eventos na janela |
| `price_eff` | 1.0 | float64 | ratio | 1000ms | ✅ | Eficiência de preço: retorno / volatilidade (proxy de tendência) |
| `range_vol_bps` | 1.0 | float64 | bps | 1000ms | ✅ | Volatilidade realizada em bps (range da janela) |
| `realized_vol_bps` | 1.0 | float64 | bps | 1000ms | ✅ | Volatilidade realizada em bps (desvio padrão de retornos) |
| `top_corretoras` | 1.0 | object | list | 1000ms | ✅ | Top corretoras por volume (lista) |
| `trades_per_sec` | 1.0 | float64 | trades/s | 1000ms | ✅ | Taxa de trades por segundo |
| `vol_compr` | 1.0 | int64 | contracts | 1000ms | ✅ | Volume comprador na janela |
| `vol_total` | 1.0 | int64 | contracts | 1000ms | ✅ | Volume total na janela |
| `vol_venda` | 1.0 | int64 | contracts | 1000ms | ✅ | Volume vendedor na janela |

### VOLUME_PROFILE (4 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `vp_poc_dist` | 1.0 | float64 | pts | — | ✅ | Distância do preço ao POC (Point of Control) |
| `vp_vah_dist` | 1.0 | float64 | pts | — | ✅ | Distância do preço ao VAH (Value Area High) |
| `vp_val_dist` | 1.0 | float64 | pts | — | ✅ | Distância do preço ao VAL (Value Area Low) |
| `vp_vp_total` | 1.0 | float64 | contracts | — | ✅ | Volume total do Volume Profile |

### VPIN (1 features)

| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |
|---------|--------|------|---------|----------|--------|-----------|
| `vpin` | 1.0 | float64 | ratio | — | ✅ | Volume-synchronized Probability of Informed Trading |
