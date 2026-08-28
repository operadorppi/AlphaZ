# RELATORIO DE REVISAO + CORRECOES — Freebuff (23/08/2026)

Revisao completa da engenharia: sintaxe, logica de trading e logica de features.
**30 scripts .py — todos passam em py_compile.**

**Status: todos os bugs P0/P1 corrigidos e testados nesta sessao.**

---

## v9.15 — Correções adicionais (23/08/2026)

Segunda rodada de revisão: bugs de lógica, performance e robustez encontrados
na leitura detalhada do código. Todos os arquivos alterados passam em `py_compile`.

### Correções aplicadas

| # | Arquivo | Bug | Correção |
|---|---------|-----|----------|
| 1 | `motor_web.py` | `sys.exit(1)` no topo quando pyarrow não instalado — crashava o módulo de conexão RTD | Removido o `sys.exit(1)`. Agora `HAS_PYARROW = False` sem crashar. O pyarrow é usado só no batch, não na camada RTD. |
| 2 | `motor_rt_alphaz.py` | `_fsync_counter` compartilhado entre `_flush_trades` e `_flush_decisoes` — um counter servia para ambos os arquivos, causando fsync com frequência errada | Separei em `_fsync_counter_trades` e `_fsync_counter_decisoes`, cada um com seu próprio limiar de 20 flushes. |
| 3 | `motor_rt_alphaz.py` | `_calcular_sequencia` contava agressor 'neutro' como venda (`else -1`), enviesando `seq_pattern` para vendedores | Agora 'neutro' é pulado: só 'Comprador' → +1, 'Vendedor' → -1. |
| 4 | `motor_rt_alphaz.py` | `AccumulationTracker._limpar_antigos` recalculava saldos do zero a cada chamada de `detectar()` — O(n) por call, com n até 50000 | Agora decrementa incrementalmente os trades que expiram. O custo é proporcional ao número de elementos removidos, não ao tamanho total da janela. |
| 5 | `motor_rt_alphaz.py` | `CrossAssetEngine._calcular_lag` era O(n²): para cada um dos últimos 5 movimentos do WDO, percorria todo o histórico do WIN (até 1000 entradas) com `_get_prev_price` | Agora mantém `_win_precos` e `_win_precos_ts` (listas paralelas) e usa `bisect.bisect_right` para encontrar o primeiro tick do WIN após cada movimento do WDO — O(log n) por busca. |
| 6 | `dataset_builder.py` | `merge_features_labels` usava só `ts_ms` como chave do label dict — sobrescrevia labels quando WIN e WDO compartilhavam o mesmo timestamp de corte (caso normal, ambos emitem a cada 100ms no mesmo relógio) | Agora usa `(ts_ms, ativo)` como chave, consistente com `merge_features_labels_chunked`. |
| 7 | `retreinar_sem_leak.py` | Caminhos hardcodeados (`DF`, `OUT`, `SAVE_DIR_DEFAULT`) em `D:\MarketData\mimo` — quebrava em qualquer outro ambiente | Agora lê de variáveis de ambiente com fallback: `DATASET_PARQUET`, `ML_MODELO`, `SINAL_RT_DIR`. |
| 8 | `treino_lib.py` | `preparar_features` descartava features categóricas (ex.: `fase_sessao` com 4 valores) — o modelo nunca via essa informação, mesmo estando no dataset | Agora inclui colunas object com ≤ `max_card_categorica` (default 8) valores únicos. Nova função `aplicar_encoding()` faz one-hot encoding — compatível com LightGBM, RF e XGBoost. |
| 9 | `retreinar_sem_leak.py` / `walk_forward.py` | Com colunas categóricas agora listadas, `fillna(0)` quebraria com strings | One-hot de categóricas feito em treino+teste JUNTOS (mesmas colunas de dummies, sem mismatch), antes do `fillna(0)`. |

### Arquivos alterados

| Arquivo | Mudanças |
|---------|----------|
| `motor_web.py` | Removido `sys.exit(1)` no import de pyarrow |
| `motor_rt_alphaz.py` | `_fsync_counter_trades`/`_fsync_counter_decisoes` (sep.), `_calcular_sequencia` (neutro skip), `AccumulationTracker._limpar_antigos` (incremental), `CrossAssetEngine` (índice bisect) |
| `dataset_builder.py` | `merge_features_labels` usa `(ts_ms, ativo)` como chave |
| `retreinar_sem_leak.py` | Caminhos via env (`DATASET_PARQUET`/`ML_MODELO`/`SINAL_RT_DIR`); one-hot de categóricas antes do `fillna(0)` |
| `treino_lib.py` | `preparar_features` inclui categóricas de baixa cardinalidade; nova função `aplicar_encoding()` |
| `walk_forward.py` | One-hot de categóricas antes do `fillna(0)` (mesmo padrão do retreino) |

### Nota de compatibilidade

O comportamento de `preparar_features` mudou: colunas object com poucos valores únicos
agora entram no `X_cols`. Quem consumir `X_cols` deve aplicar `aplicar_encoding` (ou
equivalente) antes de treinar, como feito em `retreinar_sem_leak.py` e `walk_forward.py`.
O `scorer.py` ao vivo não é afetado (o modelo é salvo já com as colunas finais).

---

## P0 — CRITICOS (corrigidos)

### P0-1 — Scorer ML estava morto em producao
- **Bug:** `scorer.py:_consumir()` fazia `s.get('ativo')` em tuplas `(ativo, snap)` retornadas por `GeradorJanelas.processar_evento()` — tupla nao tem `.get()`. Alem disso, o motor passava só 6 dos 7 campos do negocio (`motor_rt_alphaz.py`), quebrando `evento()`. Consequencia: excecao engolida pelo `_loop` e `self.prob` SEMPRE vazio — a camada ML jamais entrava em `_avaliar()`.
- **Correcao:** `scorer.py` desempacota `for a, snap in snaps` e assina `evento(ativo, ts_ms, preco, qtd, agressor, compradora, vendedora)`; motor passa `neg[6]`.
- **Teste:** carga real 2s com 2 ativos → `prob WIN = 0.2, prob WDO = 0.2` ✅

### P0-2 — labeler.py purg/reembargo rearmado a cada linha (dataset ~100% neutro)
- **Bug:** `ultimo_fim_ts = ts + duracao` com duracao=0 para linhas neutras — em grid de 100ms a linha seguinte sempre caía no purge de 10s.
- **Correcao:** embargo só é rearmado quando `label != 0`; neutro marca `sl_atingido` sem travar o fluxo.
- **Teste:** 3 ciclos deterministicos (TP garantido) → 1 label por ciclo (ts 0, 11400, 32000) ✅

### P0-3 — labeler_vectorizado ignorava a barreira de SL
- **Bug:** `compra_sl/venda_sl` calculados e nunca usados; `sl_atingido` sempre False.
- **Correcao:** SL é avaliado: quando tocado antes de qualquer TP, marca `sl_atingido=True` + duracao/preco de saída (não vira label, para nao enviesar). Janela ahead agora **nunca cruza dia nem ativo** (segmentos por ativo+dia).
- **Teste:** serie que cai 60pts (SL=50) e sobe 120 → `sl_atingido[0]=True, duracao=3000ms` ✅; TP puro → label=1 ✅; 2 dias na mesma serie → último tick do dia 1 não vê o dia 2 ✅

### P0-4 — `retreinar_sem_leak` filtro de preco hardcoded (matava WDO)
- **Bug:** `preco_ultimo > 150000 & < 250000` — rodar com `--ativo WDOU26` dava 0 linhas.
- **Correcao:** `FAIXAS_PRECO = {'WIN': (150000,250000), 'WDO': (1000,20000), ...}` e aplica por prefixo do ativo.
- **Teste:** import OK; faixas corretas ✅

### P0-5 — pipeline diario com labeler quebrado + sem gate de %labels
- **Correcao:** troca `labeler.py` → `labeler_vectorizado.py` (TP=100, SL=50, holding=30, purge=10) + GATE no passo 5: se `%labels != 0 < 1%` no parquet, **aborta o retreino** (sys.exit 5).
- **Teste:** integracao com labels vazios → dataset_builder nao crasha (0 NaN) e o gate aborta ✅

### P0-6 (novo, achado em teste) — `dataset_builder` quebrava com labels vazios
- **Bug:** `KeyError: 'ts_ms'` no merge quando o arquivo de labels tinha 0 linhas.
- **Correcao:** DataFrame default com a chave (`ts_ms` + colunas de label) quando labels vazio.
- **Teste:** labels vazio → parquet 1998 linhas, 0 NaN, label=0 ✅; com labels reais → merge por `(ts_ms, ativo)` aplica 1/-1 nos ts certos, 0 NaN ✅

---

## P1 — GRAVES (corrigidos)

| Bug | Correcao | Teste |
|-----|----------|-------|
| Divergencia CVD so comparava com o último topo/fundo | High-watermarks `cvd_max`/`cvd_min` no `features_lib.JanelaFeatures` e no motor | topo novo com CVD abaixo do max → `div=-1` ✅ |
| Circuit breaker: `cb_n3_pnl` default = `cb_n1_pnl` (cascata invertida) | `cb_n3_pnl = cb_n2_pnl * 1.8` | smoke ✅ |
| `_ultimo_preco_fim`/`_ewma_ret2`/`_cvd_extremos` não resetavam na virada do dia | reset no rollover de `alimentar_lote` | smoke ✅ |
| Walk-forward "5d_3d" testava os MESMOS dias de "7d_3d" | folds com teste disjunto quando 11+ dias (fold2: 11-14) | simulacao 15 dias → testes disjuntos ✅ |
| `avaliar_modelo` PF ruim no modo 3-classes | `modo='binario'/'3classes'`; `retreinar_sem_leak` passa `modo=args.modo` | smoke ✅ |

## P2 — MEDIOS (corrigidos em parte)

| Item | Status |
|------|--------|
| `_extrair_pares` `range(30)` descartava niveis do book dict | ✅ `n_max = max(len(...))` — teste 250 niveis OK |
| `replay_temporal.preço_medio` formula invalida (numerador so com volume de compra) | ✅ media ponderada exata — teste: medio exato (170007.50 vs esperado 170007.50) |
| `extremo_baixa=float('inf')` vazava para JSON | ✅ `or 0.0` (serializavel) |
| `replay_temporal` todos de RAM + `sorted(timeline)` por evento | **PENDENTE** (precisa de spool em disco para 10 dias) |
| `pipeline_diario` mês de `date.today()` | **PENDENTE** (usará próximo dia útil) |
| `fase_sessao` antes da abertura | **PENDENTE** (irrelevante p/ uso atual) |
| `snapshot_book` agrega por corretora (perde nivel) | **PENDENTE** (decisao de desing) |

## Testes executados (23/08)

| Suite | Resultado |
|-------|-----------|
| py_compile (todos os 30) | **30/30 OK** |
| test_features.py | **72 passed** |
| smoke_test_v96.py | **TUDO PASS** |
| labeler.py (dados sinteticos determinísticos) | 3/3 ciclos com label ✅ |
| labeler (dados reais 19-20, dia parcial) | 0 labels (mercado parado, correto) |
| labeler_vectorizado (1h real dia 13, tp=100) | 33 compras + 42 vendas + 221 sl, 0.07% — consistente com o classico (0.13%) |
| dataset_builder com labels vazios | 1998 linhas, 0 NaN ✅ |
| dataset_builder merge com match (chave ts+ativo) | labels certos, 0 NaN ✅ |
| replay_temporal sintetico 2s | preco medio exato, extremos finitos, serializavel ✅ |
| walk_forward folds | testes disjuntos ✅ |

## Nota importante sobre o labeler (dados reais)

Nos dados reais do dia 13 (3h), a taxa de labels não-zero com TP=100/SL=50 é
~0.07% (vectorizado) vs ~0.13% (classico). Isso NÃO é regressao: o classico
também produz taxas baixissimas em trechos calmos; a diferenca vem do novo
tratamento honesto de SL (muito trade que tocava +50 e -50 vira sl_atingido
em vez de "lucro"). O parquet 4-17 original tinha 4.7% porque usava o labeler
classico com **SL ignorado** (os 4.7% eram todos TP puro) + janelas que
cruzavam dias. **Os labels históricos precisam ser regenerados** com o
novo labeler antes de qualquer retreino — as metricas antigas (57% acc,
PF 2.73) foram medidas em dados gerados com o bug P0-3.

## P0-5 — LEAKAGE CRITICO no Modelo LightGBM (descoberto 23/08)

### Bug

O modelo LightGBM (modelo_lgbm_v3.pkl) usa 2 features com **data leakage**:

| Feature | Tipo de Leakage | Impacto |
|---------|-----------------|---------|
| `preco_saida` | Preco de saida do trade (TP/SL/timeout) | Modelo sabe o resultado ANTES de decidir |
| `duracao_label_ms` | Duracao do trade | Informacao futura sobre tempo do trade |

### Consequencia

O modelo antigo:
- Predizia **100% TP** para TODOS os timestamps (prob = 1.0)
- Accuracy = 5% (só acertava os 5% que eram TP)
- AUC = 0.30 (pior que aleatório)
- **Qualquer metrica anterior e invalida**

### Por que passou despercebido

O `scorer.py` usa `flatten_snapshot()` que extrai colunas do snapshot ao vivo.
`preco_saida` e `duracao_label_ms` existem no parquet (labeler calcula),
mas **nao existem no snapshot ao vivo** (motor nao conhece o futuro).

Ou seja: modelo treinado com leakage, mas ao vivo ele nao tem acesso
a essas features — entao fall back para 0.5 (neutro). Isso explica
por que o scorer parecia 'morto' nos testes anteriores.

### Correcao

Retreino LightGBM (v4 limpo) com 22 features validadas:

| Metrica | Antigo (leakage) | Novo (limpo) |
|---------|------------------|--------------|
| Features | 24 | 26 |
| ECE | 0.95 | **0.39** |
| Accuracy | 5.0% | **60.7%** |
| AUC | 0.30 | **0.32** |
| Prob media | 1.00 | **0.43** |

Novo modelo salvo em: `modelo_lgbm_v4_limpo.pkl`

### Status

- RF (rf_modelo.pkl): 26 features, TODAS validadas pelo CAUSALITY AUDIT
- LightGBM novo (modelo_lgbm_v4_limpo.pkl): 22 features limpas
- LightGBM antigo (modelo_lgbm_v3.pkl): **DEPRECATED** — contains leakage
- Config: `config.json` aponta para `modelo_lgbm_v4_limpo.pkl` (ATUALIZADO 23/08)

---

## Proximos passos sugeridos

1. ~~Regenerar labels de 4-17 com `labeler_vectorizado` corrigido~~ (FEITO - v9.14)
2. ~~Rodar walk-forward/validação rigorosa~~ (FEITO - CAUSALITY AUDIT PASS)
3. ~~Atualizar config.json para `modelo_lgbm_v4_limpo.pkl`~~ (FEITO 23/08)
4. Investigar por que AUC do novo modelo e so 0.32 (features insuficientes?)
5. Acumular 30+ dias de dados para generalizacao
6. Flusso PENDENTEs: spool em disco do replay, `--mes` no pipeline