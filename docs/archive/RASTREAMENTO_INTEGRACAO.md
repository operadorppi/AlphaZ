# RASTREAMENTO DE INTEGRAÇÃO — Camadas de Contexto de Mercado (v9.32)

Este documento rastreia **cada indicador novo** desde a geração até o consumo,
mapeando todos os pontos de integração no pipeline do Freebuff.

## 1. Indicadores e quem os gera

### 1.1 Camada de Preço Diário/Sessão Anterior (`features_contexto_preco.py`)

| Indicador | Função geradora | Entrada | Causalidade |
|-----------|-----------------|---------|-------------|
| `maxima_dia`, `minima_dia` | `adicionar_contexto_preco` (expanding dentro do dia) | preço | **Causal** — só passado |
| `abertura` | mesma (primeiro preço do dia) | preço | Causal |
| `dist_fechamento_anterior_pts` | mesma (proxy: fechamento de D-1) | preço, proxy | Causal — D-1 conhecido em D |
| `dist_ajuste_pts` (proxy) | mesma (proxy: fechamento de D-1) | preço, proxy | Causal |
| `dist_maxima_dia_pts` etc. | mesma (divididas por vol EWMA) | vol, distância | Causal |
| `posicao_range_dia` | mesma (com proteção range==0) | preço, maxima, minima | Causal |
| `gap_abertura_*` | mesma (abertura − referência D-1) | abertura, D-1 | Causal |
| `acima_ajuste`, `abaixo_ajuste` | mesma (flags booleanos) | preço, ajuste | Causal |
| `rompimento_maxima` (novo topo) | mesma (preco == maxima_dia E maxima sobe) | preço, maxima | Causal |
| `perto_maxima`, `perto_minima` | mesma (threshold = 1×vol) | preço, vol | Causal |

### 1.2 Camada de Ajuste Oficial B3 (`calcular_ajuste_diario.py` + `features_contexto_avancado.py`)

| Indicador | Função geradora | Entrada | Causalidade |
|-----------|-----------------|---------|-------------|
| `ajuste_oficial` (WIN/WDO) | `calcular_ajuste_multi_dias` (RAW → CSV) | `preco*qtd` ponderado na janela B3 | **Causal** — só após janela |
| `ajuste_anterior_oficial` | `adicionar_ajuste_oficial` (shift 1 dia) | tabela `ajuste_diario_<YYYYMM>.csv` | Causal — D-1 em D |
| `dist_ajuste_oficial_pts/_norm/_abs` | `adicionar_ajuste_oficial` | preço, ajuste, vol | Causal |
| `acima_ajuste_oficial`, `abaixo_ajuste_oficial` | mesma | preço, ajuste | Causal |
| `retorno_em_relacao_ao_ajuste_oficial` | mesma | preço, ajuste | Causal |
| `abertura_vs_ajuste_oficial_pts/_norm` | mesma | abertura, ajuste | Causal |

### 1.3 Camada de VWAP Intraday Causal (`calcular_vwap_diaria.py` + `features_contexto_avancado.py`)

| Indicador | Função geradora | Entrada | Causalidade |
|-----------|-----------------|---------|-------------|
| `vwap` (causal) | `calcular_vwap` (cumsum por `(contrato, dia)`) | `preco*qtd` por negócio | **Causal** — cumsum dentro do dia |
| `dist_vwap_pts/_abs/_norm/_ticks` | `adicionar_vwap_causal` (asof backward) | preço, vwap, vol, tick | Causal |
| `acima_vwap`, `abaixo_vwap` | mesma | preço, vwap | Causal |
| `aproximando_vwap`, `afastando_vwap` | mesma (shift 60s) | \|dist\| agora vs 60s atrás | Causal |
| `cruzou_vwap` (evento) | mesma (shift 1 snapshot) | lado vs snapshot anterior | Causal |

### 1.4 Interações Microestrutura × Contexto (`features_contexto_avancado.py`)

| Indicador | Função geradora | Operandos |
|-----------|-----------------|-----------|
| `aggr_x_dist_vwap` | `adicionar_interacoes_micro_contexto` | aggr_imb × dist_vwap |
| `aggr_x_dist_ajuste_oficial` | mesma | aggr_imb × dist_ajuste_oficial |
| `aggr_x_acima_vwap`, `aggr_x_acima_ajuste_oficial` | mesma | aggr_imb × flags |
| `cvd_x_*` (4 features) | mesma | cvd × contexto |
| `imb_x_dist_vwap`, `imb_x_dist_ajuste_oficial` | mesma | imb_L5 × contexto |
| `vol_x_acima_vwap`, `vol_x_acima_ajuste_oficial` | mesma | vol × flags |

### 1.5 Features de Regime Contínuo (`features_contexto_avancado.py`)

| Indicador | Função geradora | Causalidade |
|-----------|-----------------|-------------|
| `vwap_inclinacao_1m`, `vwap_inclinacao_5m` | `adicionar_features_regime` (shift) | Causal |
| `regime_realiz_vol`, `regime_realiz_vol_bps` | mesma (EWMA \|ret\|) | Causal |
| `regime_vol_zscore` | mesma (z-score EWMA dupla) | Causal |
| `regime_aggr_persistencia` | mesma (EWMA suave) | Causal |
| `regime_cvd_aceleracao` | mesma (delta delta) | Causal |
| `regime_range_dia_norm` | mesma (range/vol) | Causal |
| `regime_pos_vs_vwap`, `regime_pos_vs_ajuste` | mesma (réplicas das distâncias) | Causal |

## 2. Onde os indicadores são gerados (pipeline)

| Etapa | Script | Artefato de saída |
|-------|--------|-------------------|
| RAW → ajuste | `calcular_ajuste_multi_dias` | `ajuste_diario_<YYYYMM>.csv` |
| RAW → VWAP | `calcular_vwap` | `vwap_<YYYYMM>.parquet` |
| Micro + contexto base | `adicionar_contexto_preco` | (em memória) |
| Contexto avançado | `integrar_base.py` (orquestra todos) | `dataset_final_completo.parquet` |

## 3. Onde os indicadores são consumidos

| Consumidor | Como consome | Arquivo |
|------------|--------------|---------|
| **Dataset final (batch)** | `dataset_builder.py` injeta via `--ajuste-oficial` e `--vwap-por-negocio` | `dataset_builder.py:224-284` |
| **Modelo ML (treino)** | `retreinar_lgbm_limpo.py --usar-complemento` lê `dataset_final_completo.parquet` | `retreinar_lgbm_limpo.py:91-99` |
| **Walk-Forward OOS** | `walk_forward_v914_limpo.py` auto-detecta dataset enriquecido | `walk_forward_v914_limpo.py:71-79` |
| **Scorer ao vivo** | `scorer.py` mantém `VWAPTracker` por ativo e tabela de ajuste D-1 em memória | `scorer.py:18-91, 117-165` |
| **Motor RT** | `motor_rt_alphaz.py` carrega `ajuste_diario_<YYYYMM>.csv` e passa ao `ScorerML` | `motor_rt_alphaz.py:3412-3434` |
| **Dashboard** | `motor_rt_alphaz.py /api/contexto` retorna estado por ativo | `motor_rt_alphaz.py:3344-3347, 4050-4091` |
| **Pipeline diário** | `pipeline_diario.py` passo 4.5 chama `integrar_base.py`; passo 6 usa `--usar-complemento` | `pipeline_diario.py:138-152, 196-203` |
| **Auditoria de leakage** | `auditoria_leakage()` em `features_contexto_preco.py` e `features_contexto_avancado.py` | ambos |
| **Análise de redundância** | `analise_redundancia.py` lê o parquet final | `analise_redundancia.py` |
| **Ablation test** | `ablation_test.py` testa camadas individualmente | `ablation_test.py` |

## 4. Fluxo de dados ponta a ponta

```
RAW (D:\MarketData\Profit\RAW\ano=YYYY\mes=MM\dia=DD\sym=*\tipo=TT\*.parquet)
  │
  ├─→ calcular_ajuste_diario.py ─→ ajuste_diario_<YYYYMM>.csv
  │                                     │
  │                                     ▼
  ├─→ calcular_vwap_diaria.py ─→ vwap_<YYYYMM>.parquet
  │                                     │
  │                                     ▼
  └─→ integrar_base.py ─────────────────────┐
         │ (orquestra)                       │
         │  • features_contexto_preco.py     │
         │  • features_contexto_avancado.py  │
         │     - adicionar_ajuste_oficial    │
         │     - adicionar_vwap_causal       │
         │     - adicionar_interacoes_*      │
         │     - adicionar_features_regime   │
         ▼                                    │
   dataset_final_completo.parquet (140 cols)│
         │                                    │
         ├──→ walk_forward_v914_limpo.py      │
         │                                    │
         ├──→ retreinar_lgbm_limpo.py ───→ modelo_lgbm_v4_limpo.pkl
         │                                    │
         ├──→ analise_redundancia.py          │
         │                                    │
         └──→ ablation_test.py               │
                                              │
   ajuste_diario_<YYYYMM>.csv ───────────────┤
         │                                    │
         ▼                                    │
   motor_rt_alphaz.py inicializa:            │
     • ScorerML(tabela_ajuste=...)           │
     • /api/contexto → dashboard            │
```

## 5. Garantias de causalidade

- **Nenhuma feature olha o futuro** — verificado por `auditoria_leakage()` em ambos os módulos
- **Reset diário explícito** — `VWAPTracker.reset_diario()` detecta virada de dia via `(ts_ms - 3h) // 86400000`
- **Embargo temporal** — `walk_forward_v914_limpo.py` usa `PURGE_S=30`, `EMBARGO_S=30`
- **Testes de leakage A-E** — `test_contexto_avancado.py:138-218` perturbam o futuro e validam invariância

## 6. Status da integração

| Componente | Status | Evidência |
|------------|--------|-----------|
| `integrar_base.py` | ✅ Criado | `integrar_base.py` |
| `scorer.py` com VWAPTracker | ✅ Criado | `scorer.py:18-91` |
| `scorer.py` com ajuste D-1 | ✅ Criado | `scorer.py:117-165` |
| `motor_rt_alphaz.py` carrega tabela | ✅ Integrado | `motor_rt_alphaz.py:3412-3434` |
| `/api/contexto` endpoint | ✅ Criado | `motor_rt_alphaz.py:3344-3347, 4050-4091` |
| `pipeline_diario.py` passo 4.5 | ✅ Integrado | `pipeline_diario.py:138-152` |
| `retreinar_lgbm_limpo.py --usar-complemento` | ✅ Integrado | `retreinar_lgbm_limpo.py:91-99` |
| `walk_forward_v914_limpo.py` auto-detecta | ✅ Integrado | `walk_forward_v914_limpo.py:71-79` |
| Documentação `DOCUMENTACAO.md` (v9.32, v9.33) | ✅ Atualizado | `DOCUMENTACAO.md:249-` |
| Testes `test_contexto_avancado.py` | ✅ 14/14 | pytest |
| Testes `test_contexto_preco.py` | ✅ 9/9 | pytest |
| Testes `test_novas_features.py` | ✅ 13/13 | pytest |

## 7. Pendências conhecidas

- **Painel visual no dashboard HTML** — o endpoint `/api/contexto` está pronto mas o template HTML ainda não faz polling nem exibe. Requer atualização do JS/CSS.
- **Validação contra ajuste oficial B3** (item 18 do pedido original) — falta feed oficial do usuário.
- **OOS baseline vs +todas as camadas** — `validar_contexto_preco.py` está pronto mas a sessão anterior travou. Recomendado rodar com `--sample 500000` para evitar OOM.

## 8. Como reproduzir a integração completa

```bash
# 1. Gerar dataset enriquecido
python integrar_base.py --mes 202608 --ativo WINV26 WDOU26

# 2. Retreinar modelo com features novas
python retreinar_lgbm_limpo.py --usar-complemento \
    --gate-dias 20260801,20260804,20260805,20260806 \
    --save-dir D:\MarketData\mimo

# 3. Validar via walk-forward OOS
python walk_forward_v914_limpo.py

# 4. Iniciar motor (carrega scorer com VWAP + ajuste)
python motor_rt_alphaz.py WINV26 WDOU26

# 5. Polling do contexto (qualquer cliente HTTP)
curl http://localhost:porta/api/contexto
```
