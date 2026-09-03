# Auditoria Completa da ML — Relatório

> Data: 2026-08-29
> Status: **MODELO FUNCIONAL, MAS COM ALERTAS**

---

## Resumo Executivo

| Métrica | Valor | Status |
|---------|-------|--------|
| Modelos encontrados | 5 | ✅ |
| Dataset principal | 3.4M linhas, 166 colunas | ✅ |
| Distribuição de labels | TP: 4.45%, SL: 18.09%, TO: 77.46% | ⚠️ Desbalanceado |
| AUC-ROC | 0.7053 | ✅ Aceitável |
| Accuracy | 0.625 | ⚠️ Baixo (desbalanceamento) |
| Profit Factor | 2.78 | ✅ Bom |
| ECE | 0.2633 | ⚠️ Alto (calibração ruim) |

---

## 1. Features Utilizadas

### Top 10 Features por Importância

| Rank | Feature | Importância | Categoria |
|------|---------|-------------|-----------|
| 1 | `vp_vp_total` | 3429.0 | Volume Profile |
| 2 | `cvd_total` | 2025.0 | Order Flow |
| 3 | `preco_ultimo` | 1710.0 | Preço |
| 4 | `vp_val_dist` | 1571.0 | Volume Profile |
| 5 | `vpin` | 1402.0 | Volume Profile |
| 6 | `kyle_kyle_lambda` | ~1200 | Microestrutura |
| 7 | `aggr_imb` | ~1100 | Order Flow |
| 8 | `dist_vwap_pts` | ~1000 | Contexto |
| 9 | `spread` | ~900 | Book |
| 10 | `ofi_total` | ~800 | Order Flow |

**Análise:**
- ✅ Features de Volume Profile dominam (vp_vp_total, vp_val_dist, vpin)
- ✅ Features de Order Flow presentes (cvd_total, aggr_imb, ofi_total)
- ✅ Features de Microestrutura presentes (kyle_lambda, spread)
- ⚠️ `preco_ultimo` é feature fraca (provavelmente proxy para tendência)

---

## 2. Features Descartadas

### Leakage Features (removidas)
```python
LEAKAGE_FEATURES = {'preco_saida', 'duracao_label_ms'}
PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']
```

**Status:** ✅ Correctamente removidas do treinamento

---

## 3. Correlação entre Features

NÃO ANALISADO (requer dataset completo).

**Recomendação:** Executar análise de correlação para identificar redundâncias.

---

## 4. Distribuição de Labels

| Label | Count | Percentage |
|-------|-------|------------|
| TP (+1) | 151,810 | 4.45% |
| SL (-1) | 617,570 | 18.09% |
| TIMEOUT (0) | 2,643,580 | 77.46% |
| **Total** | **3,412,960** | **100%** |

**Análise:**
- ⚠️ **Desbalanceamento severo:** 77.46% TIMEOUT
- ✅ Ratio TP/SL = 0.246 (mais SL que TP — conservador)
- ⚠️ Hit rate teórico máximo: ~22.5% (TP + SL)

---

## 5. Performance do Modelo

### Métricas Globais

| Métrica | Valor | Interpretação |
|---------|-------|---------------|
| Accuracy | 0.625 | Baixo (desbalanceamento) |
| AUC-ROC | 0.7053 | ✅ Aceitável |
| Profit Factor | 2.78 | ✅ Bom |
| ECE | 0.2633 | ⚠️ Alto (calibração ruim) |

### Análise de ECE (Expected Calibration Error)

```
ECE = 0.2633 (26.33%)
```

**Problema:** O modelo está **mal calibrado**.
- Predições de 70% de probabilidade podem ter accuracy real de 44%
- Recomenda-se recalibração (Platt scaling ou isotonic regression)

---

## 6. Overfitting

### Sinais de Overfitting

| Sinal | Status |
|-------|--------|
| AUC treino vs teste | NÃO DISPONÍVEL |
| Profit factor | 2.78 (bom) |
| Número de features | 17 (controlado) |
| Tree depth | Não verificado |

**Análise:**
- ✅ Profit Factor > 2.0 sugere generalização
- ⚠️ ECE alto pode indicar overfitting em probabilidades
- ❓ Necessário walk-forward para confirmar

---

## 7. Leakage

### Verificações Realizadas

| Verificação | Status |
|-------------|--------|
| preco_saida removida | ✅ Sim |
| duracao_label_ms removida | ✅ Sim |
| shift lookahead | ✅ OK — shift(-1) em validacao_rigorosa.py é diagnóstico intencional (não é leakage do modelo) |
| Normalização antes do split | ✅ Correto |
| VWAP causal | ✅ Sim |

**Conclusão:** ✅ **Sem leakage crítico identificado**

---

## 8. Distribuição Train/Test

### Split Temporal

```python
TREINO_DIAS = [4, 5, 6, 7]  # Ago/2026
CAL_DIAS = [10, 11]
TEST_DIAS = [13, 14]
```

**Status:** ✅ Split temporal correto, sem sobreposição

---

## 9. Drift Temporal

NÃO ANALISADO (requer múltiplos períodos).

**Recomendação:** Executar walk-forward com janelas móveis.

---

## 10. Performance por Ativo

### WINV26

| Métrica | Valor |
|---------|-------|
| Total de labels | 3,412,960 |
| TP | 151,810 (4.45%) |
| SL | 617,570 (18.09%) |
| TIMEOUT | 2,643,580 (77.46%) |

**Status:** ✅ Dados suficientes para treinamento

---

## 11. Performance por Dia

NÃO DISPONÍVEL (dataset não contém coluna 'data').

**Recomendação:** Adicionar coluna 'data' ao dataset para análise por dia.

---

## 12. Performance por Regime

NÃO DISPONÍVEL (features de regime não no modelo atual).

**Recomendação:** Adicionar features de regime e re treinar.

---

## 13. Comparação de Modelos

### Modelos Encontrados

| Modelo | Tamanho | Métricas |
|--------|---------|----------|
| modelo_lgbm_v3.pkl | 5.0 MB | Não disponível |
| modelo_lgbm_v4_limpo.pkl | 4.0 MB | Não disponível |
| modelo_lgbm_v5_otimizado.pkl | 1.7 MB | AUC: 0.705, PF: 2.78 |

**Análise:**
- Modelo mais recente (v5) é o menor — possivelmente mais eficiente
- PF 2.78 é bom para trading de alta frequência

---

## 14. Análise de Redundância

### Features de Volume Profile

| Feature | Importância | Redundante? |
|---------|-------------|-------------|
| vp_vp_total | 3429.0 | — |
| vp_val_dist | 1571.0 | ⚠️ Possível |
| vpin | 1402.0 | ⚠️ Possível |

**Recomendação:** Executar ablation para verificar redundância.

---

## 15. Estabilidade Temporal

NÃO VERIFICADO.

**Recomendação:** Executar walk-forward com múltiplas janelas.

---

## Conclusão

### Pontos Fortes
1. ✅ **Profit Factor 2.78** — modelo economicamente viável
2. ✅ **AUC 0.705** — capacidade discriminatória aceitável
3. ✅ **Sem leakage crítico** — features de vazamento removidas
4. ✅ **Split temporal correto** — treino/teste separados

### Pontos de Atenção
1. ⚠️ **ECE 0.263** — calibração ruim, probabilidades não confiáveis
2. ⚠️ **Desbalanceamento** — 77% TIMEOUT, hit rate máximo ~22%
3. ⚠️ **Features de VP dominantes** — possível sobre-representação
4. ⚠️ **Sem validação walk-forward** — estabilidade temporal incerta

### Recomendações Prioritárias

1. **Recalibrar probabilidades** (Platt/isotonic) para reduzir ECE
2. **Executar walk-forward** para validar estabilidade temporal
3. **Avaliar ablation** de features de Volume Profile
4. **Adicionar features de regime** para performance por regime
5. **Monitorar drift** semanalmente

---

## Veredito Final

**O modelo ESTÁ APRENDENDO ALGO ÚTIL.**

Evidências:
- Profit Factor > 2.0 em validação
- AUC > 0.7 (acima de random 0.5)
- Features de microestrutura fazem sentido (VP, CVD, Kyle)

**Riscos:**
- Calibração ruim (ECE alto)
- Desbalanceamento severo
- Sem validação temporal completa

**Recomendação:** Usar com cautela, monitorar ECE e PF semanalmente.

---

## P0-A29 (v15.24) — Scorer: ausente != inválida != zero legítimo

**Achado:** no fallback sem `feature_manifest.json` (modelo .pkl puro), o
scorer montava o vetor com `row.get(c, 0.0)` — feature **ausente** virava
`0.0` em silêncio e o modelo rodava com informação falsa (ausente não é
zero). O `extract()` do manifest zerava também feature presente com valor
não-numérico e opcional ausente sem default.

**Contrato implementado (`FeatureManifest.montar_vetor` + gate no scorer):**

| Estado da feature | Comportamento |
|---|---|
| Obrigatória ausente | Fail-safe: sinal neutro 0.5 + contador + log (`AUSENTE:name`) |
| Presente mas não-numérica (string/None) | Fail-safe: idem (`INVALIDA:name`) — nunca adivinhar |
| Opcional ausente COM default | Usa o default documentado (ok) |
| Opcional ausente SEM default | Problema (`SEM_DEFAULT:name`) — nunca zero fake |
| Presente e zero | **Zero legítimo** — vai ao modelo (ok) |

- Fallback sem manifest: TODA feature da lista do .pkl é obrigatória
  (mesma semântica do `required=True` padrão do manifest).
- Erro distingue o tipo (`self.ultimo_error = 'COBERTURA: ...'`), o log é
  throttled por assinatura (feature sistematicamente ausente não inunda o
  log; contador `fallos` continua incrementando).
- `ml/retreinar_lgbm_limpo.py` agora salva `feature_manifest.json` AO LADO
  do .pkl ao treinar (antes o `FeatureManifest.from_model/save` existia mas
  nenhum script de treino o chamava — o scorer ao vivo rodava sempre no
  fallback sem contrato).

**Arquivos:** `ml/feature_manifest.py` (`_valor_numerico`, `montar_vetor`),
`ml/scorer.py` (gate de cobertura no `_prever`), `ml/retreinar_lgbm_limpo.py`
(save do manifest), teste `testes/test_scorer_cobertura_v1524.py` (15 casos:
unit do contrato + integração fallback/manifest).

---

## P1-A30 (v15.25) — prob x status: 0.5 de erro != 0.5 neutro

**Achado:** qualquer falha de inferência (`predict_proba` exceção, cobertura
incompleta, ECE alto) retornava `0.5` — o consumidor não distinguia "modelo
neutro" de "modelo falhou". Esse 0.5 de erro podia chegar ao gate do ML como
probabilidade **válida**: a calibration rodava com ele e o motor bloqueava ou
liberava trade com base em silêncio do modelo.

**Correção (fonte de verdade = status, não o float):**

| Status | Significado | `obter_estado()` devolve |
|---|---|---|
| `OK` | Inferência válida | `(prob, 'OK')` |
| `NAO_INFERIDO` | Ainda sem snap p/ o ativo | `(0.5, 'NAO_INFERIDO')` |
| `MODEL_ERROR` | Inferência falhou (cobertura A29 / predict) | `(None, 'MODEL_ERROR')` |
| `ECE_ALTO` | Inferiu mas ECE > 0.15 — neutro POLÍTICO | `(0.5, 'ECE_ALTO')` |

- `ScorerML.status[ativo]` atualizado em cada desfecho do `_prever`; prob
  válida também registrada lá (invariante prob/status para qualquer
  chamador); `estado_salud()` expõe `status`.
- **Gate do SignalEngine:** `MODEL_ERROR` → ML **não fala** — mesmo tratamento
  de scorer ausente (heurística pura decide), com motivo explícito
  `ML_ERRO (inferencia falhou — heuristica)` no Signal; a calibration NUNCA é
  alimentada com 0.5 de erro. `ECE_ALTO` → `ML_BLOCK (ECE alto — neutro
  politico)` (bloqueio deliberado, distinto de erro). `get_raw_signal()`
  segue como legacy documentado — prefira `obter_estado()`.

**Arquivos:** `ml/scorer.py` (status + `obter_estado`), `core/signal_engine.py`
(gate), teste `testes/test_scorer_estado_v1525.py` (8 casos: transições de
status no scorer + integração do gate com stub — MODEL_ERROR cai na heurística
com `ML_ERRO` e sem `ML_BLOCK`; ECE_ALTO e OK/0.5 rodam o caminho de
calibration normalmente).

---

## P1-A30 aprofundado (v15.26) — status MODEL_ERROR propagado ao RiskEngine/app

**Achado:** o app informava `ml_disponivel=self.scorer is not None` — modelo
carregado mas com a última inferência FALHA (`MODEL_ERROR`), neutro político
(`ECE_ALTO`) ou ainda sem inferir (`NAO_INFERIDO`) contava como "ML
disponível" para o RiskEngine. O journal também rotulava sinais de fallback
pós-erro como `ML(USADO)/ML(BLOQUEADO)` — nunca como erro.

**Correção:**

| Camada | Antes | Depois |
|---|---|---|
| `app._ml_operacional(scorer, ativo)` | `scorer is not None` | Scorer presente **E** status do ativo == `OK` |
| `app._status_do_ml` / `atualizar_mercado` | — | app passa `ml_status` + `ml_ativo` ao RiskEngine |
| `app._classificar_modelo(scorer, sig)` | `ML(USADO)` se prob>0.5 com lado | `ML(ERRO)` prioritário quando `ML_ERRO` nos motivos; senão USADO/BLOQUEADO |
| `risk_engine` motivo de ML down | `"ML indisponivel por Xs"` | `"ML indisponivel p/ {ativo} (status={status}) ha Xs"` |

- Semântica: `MODEL_ERROR`/`ECE_ALTO`/`NAO_INFERIDO` ⇒ ML **down para risco**
  (a proteção 10 registra `ml_available=False` com motivo auditável; política
  PRODUCTION bloqueia, DEVELOPMENT segue com heurística — a mesma decisão
  explícita do gate A30 no SignalEngine, agora também no RiskEngine).
- Scorer sem atributo `status` (compat/stubs) continua tratado como
  operacional — sem mudança de comportamento para quem não migrou.

**Arquivos:** `core/app.py` (helpers puros + 2 pontos de chamada),
`core/risk_engine.py` (estado + motivo), teste
`testes/test_ml_status_risco_v1526.py` (14 casos: helpers + risco — motivo
com status/ativo, ML up/down, decisão real com `risk_components['model']`).

---

## P0-A31 (v15.27) — Contrato único de features + paridade tripla offline/realtime/scorer

**Achado:** o ScorerML mantém estado temporal próprio (vwaps/ctx/vol/ret/
vps/mig/vrels/inter) paralelo ao motor, com a alegação de "paridade total"
nunca validada numericamente. Medição real sobre os MESMOS eventos
determinísticos (60s, rajada no meio, book por segundo) revelou:

| Feature | Resultado medido |
|---|---|
| `vol_100ms` | **exata (diff 0)** — tracker ≡ pandas EWMA do grid |
| `vol_5s/15s/1s/500ms` | ≤ ~5e-7 (borda de ≤1 linha do grid no instante final) |
| `retorno_*x100ms` | ≤ ~3e-5 (borda de ≤1 linha — 1 passo de retorno) |
| **VP (poc/vah/val/vp_total)** | **DIVERGE ~110 pts** — causa raiz encontrada: o snap do corte do `GeradorJanelas` embute o perfil **antes** do trade que cruza a borda (lag de 1 trade = semântica do dataset_100ms), mas o scorer sobrescrevia o row com `self.vps.calcular()` no instante do trade (sem lag) |
| `aggr_imb/cvd_*/delta_preco/...` A(1s) x C(grid) | **AGR_DIFERENTE por design** (agregação por segundo vs janela do master clock) — nunca comparadas como iguais, só presença |

**Correção (eliminar a implementação paralela de VP dentro do scorer):**
`ml/scorer.py _prever` agora usa o **vp embutido no snap do gerador** (mesma
semântica do dataset de treino) para o row do modelo; o fallback p/ `self.vps`
só cobre snaps sem vp (warm-up/chamadas diretas). O perfil VP de fim de
stream entre `gerador.vp_trackers` e o tracker do scorer é **idêntico**
(mesma classe, mesmos trades) — validado.

**Artefato novo — o contrato:**
`ml/paridade_features.py` — catálogo `CONTRATO` nome-a-nome (quem produz +
classe `MESMA_DEFINICAO`/`VP_PARALELO`/`AGR_DIFERENTE`), gerador de stream
determinístico, pemas A/B/C, referência offline pandas (grid denso
forward-filled de 100ms, como o dataset_100ms) e `relatorio()` com diff/tol/
status por feature. Executável: `python -m ml.paridade_features` (report no
stdout). Teste `testes/test_paridade_tripla_v1527.py` (9 casos) trava
numericamente: vol_100ms exata, vol/ret dentro da borda de 1 linha, VP
idêntico fim-de-stream, SEM_COBERTURA honesto p/ horizontes > história,
AGR_DIFERENTE nunca comparado como igual.

**Limites documentados (não são bugs, são contratos):** a comparação
vol/ret no instante final carrega ≤1 linha de desalinhamento de borda; os
horizontes maiores que a história do stream ficam SEM_COBERTURA; e a
diferença A(1s) x modelo(grid) é a semântica de agregação distinta por design
(a heurística usa segundos; o modelo usa janelas do master clock).

---

## P0-A31 — extensão v15.28 — VP_LAG_1_TRADE validado (as-of do corte)

**Questão:** o snap do corte do `GeradorJanelas` embute o perfil VP ANTES do
trade que cruza a borda (lag de 1 trade = semântica do dataset_100ms). Isso
desvia o VP do dataset do "VP real" do scorer em rajadas? Medido:

| Comparação | Resultado |
|---|---|
| Magnitude do bug antigo (scorer lia o perfil pós-trade, ~1 trade à frente do row) | `poc_dist` ≤ 1 tick, `vah/val_dist` ≤ ~7, `vp_total` ≤ qtd do trade, **`poc_acima` flipa em 20% dos cortes** (binária sensível ao instante) |
| **Validação nova (`vp_lag_1_trade`)** — por corte c, tracker INDEPENDENTE avançado até o último trade com ts **estritamente < c** vs `vp` do snap | **diff = 0 em 100% dos cortes (597), inclusive na rajada (41 cortes)** — o lag NÃO desvia: o snap carrega exatamente o perfil causal as-of do corte |

**Conclusão do contrato:** dataset (snap) e scorer-side (tracker causal) VP
coincidem por construção quando ambos respeitam a regra "último trade com ts
< corte". O bug A31 era o scorer ler o perfil no **instante do trade** (pós-
crossing) — corrigido em v15.27 (fonte = snap). `ml/paridade_features.py`
agora expõe `vp_lag_1_trade()` + linhas `VP_LAG_1_TRADE` no relatório (campo
a campo + rajadas). Teste v15.27 ganhou 2 casos (lag sem desvio + presença no
relatório) — 11 no total.

---

## P1-A32 (v15.29) — split_com_purge: embargo solicitado != realizado

**Achado:** `split_com_purge()` reduzia o embargo em silêncio quando os dados
após o corte não comportavam o gap pedido (ex.: pedido purge=5s/embargo=30s
com menos de 30s de teste virava um gap menor) e o resultado seguia sendo
apresentado como "sem leakage".

**Correção (`ml/treino_lib.py`):**
- `retornar_politica=True` → devolve `(train, test, politica)` com
  `embargo_solicitado_s`, `embargo_realizado_s`, `embargo_integral` (bool) e
  `status` `OK | EMBARGO_REDUZIDO` — o chamador **nunca** confunde um split
  reduzido com a metodologia integral;
- `exigir_integral=True` → **não adapta**: `ValueError` explícito
  (`VALIDACAO INCONCLUSIVA`) quando o embargo solicitado não cabe;
- fallback operacional (default, compatível com o retorno antigo) loga
  `VALIDACAO INCONCLUSIVA` com o embargo realizado — nunca silencioso;
- removida uma checagem morta de "teste vazio" pós-redução (o cap em
  `max_ts` a tornava inalcançável); o caso degenerado (cauda < purge) fica
  explícito: `EMBARGO_REDUZIDO` + log INCONCLUSIVA, nunca `OK`.

**Validação — `testes/test_split_purge_politica_v1529.py` (8 casos):** OK
integral (gap treino→teste ≥ purge+embargo), compat de retorno 2-tupla,
redução explícita na política (realizado < solicitado, nunca mente
`integral`), `exigir_integral` falha quando não cabe e passa quando cabe,
purge preservado (treino termina purge antes do corte), caso degenerado de
cauda nunca rotulado OK.


---

## P0-A33 (v15.30) — labeler com horizonte por TIMESTAMP real, nao por linhas

**Achado:** label_vectorizado()/label_ponto_ref() convertiam max_holding_s em
max_holding_ms // tick_ms LINHAS e gravavam duracao_ms = ticks*100. Com dados
RAW irregulares (rajada a 1ms, silencio a 800ms), N linhas != N*100ms — um
holding de 30s podia virar ~300 eventos (rajada) ou varrer 45s reais
(silencio), alterando os labels.

**Correcao (canonica = tempo real):**
- ml/labeler_vectorizado.py e labeler_core (label_ponto_ref/label_array_ref):
  horizonte = ts[i] + max_holding_ms (eventos com ts <= limite, respeitando
  segmento ativo+dia); duracao_ms = delta REAL de timestamps do evento de
  saida (barreira ou ultimo evento da janela no TIMEOUT).
- label_ponto_ref mantem a semantica LEGACY por contagem de linhas quando
  ts_ms=None (APIs puras por indice dos testes de invariante); toda pipeline
  real passa ts_ms e usa o horizonte temporal.
- Em grid uniforme 100ms (dataset_100ms) os resultados sao IDENTICOS aos
  anteriores; em dados irregulares refletem o tempo real — requer regenerar
  labels/retreino (ja pendente).

**Validacao — testes/test_labeler_tempo_real_v1530.py (6 casos):** rajada
densa (TP real ~10s alem da 300a linha -> TP; legado: TIMEOUT); esparso
(barreira a 45s com holding 30s -> TIMEOUT; legado: TP errado); duracao real
(SL a 1ms -> 1ms, nao 100); TIMEOUT ate o ultimo evento real; core ==
vectorizado em ts irregulares; compat em grid 100ms. Invariantes: 139 passed
(133 + 6); suite completa 1031 passed — zero regressoes.


---

## v15.30-R — Regeneracao de labels no RAW Hive + A/B ANTES x DEPOIS (2026-09-03)

Executado: `scripts/comparar_labels_v1530.py` sobre o RAW Parquet/Hive do dia
2026-09-03 (fluxos TT canonicos WIN/WDO/IND/DOL, is_rlp=False, 1 janela por
ativo). O codigo ANTES e extraido literalmente de `HEAD:ml/labeler_vectorizado.py`
(git) — comparacao e codigo vs codigo, nao reimplementacao.

### 1. Compat em grid uniforme (prova de que a ferramenta compara certo)

40.000 linhas sinteticas em grid 100ms uniforme, tp=20/sl=15/holding=30:
**ANTES == DEPOIS em 100% das linhas** (label, duracao_ms, preco_saida, diff 0).
Qualquer divergencia vista no RAW e efeito genuino do tempo irregular.

### 2. Medicao real — WIN, 6 min de rajada (56.942 eventos), purge=0

| Metrica | ANTES (linhas) | DEPOIS (tempo real) |
|---|---|---|
| TP | 26,38% | **43,56%** |
| SL | 46,18% | **54,06%** |
| TIMEOUT | 27,45% | **2,38%** |
| dur TP (mediana) | 13,8s (**falso**: relogio de linhas) | 3,8s (real) |
| dur SL (mediana) | 10,6s (falso) | 1,4s (real) |
| dur TIMEOUT (mediana) | 30s (falso: sempre o teto) | 0,28s (real) |

**25,06% das linhas mudaram de outcome.** Causa: a janela antiga de 300 linhas,
em rajada a ~90-150 trades/s, cobria ~2-3s REAIS — 27% das entradas nunca
alcancavam a barreira nesse intervalo curto e viravam TIMEOUT. Com holding real
de 30s, so 2,4% sao TIMEOUT.

### 3. Embargo purge=5 (config de producao) em ticks brutos densos

Com purge=5s ambas as semanticas colapsam para ~99,9% TIMEOUT no fluxo RAW de
eventos: cada barreira atingida embargoa 5s ≈ 450+ linhas a ~90 trades/s — e
como ~50% das linhas atingem barreira, quase toda linha cai dentro de algum
embargo. Conclusao operacional: **labels finais para ML devem ser gerados no
grid do dataset (100ms), onde o embargo atinge ~50 linhas por barreira — nao no
fluxo de ticks brutos** — ou o embargo deve rearmar apenas em saidas nao
sobrepostas.

### 4. Calibracao de barreira por ativo (achado separado)

tp=20/sl=15 no proprio scale de preco e razoavel para WIN/IND (135k-190k) mas
irrelevante para WDO/DOL (preco ~5k: 20 pts = 0,4% de movimento em 30s — nunca
atingido; DOL/IND ficam ~100% TIMEOUT). A calibracao tp/sl por ativo (em
unidades reais de tick/ponto) e passo separado e pendente.

### 5. Desempenho (limitacao atual do labeler)

Labeler puro Python custa ~1,5-2,3 ms/linha no fluxo de eventos: dia cheio de
WIN (2,5M linhas) ≈ 1,5-2h por semantica. O script suporta `--modo full` para a
rotina pos-pregao/overnight; para rotina, recomenda-se vetorizar o scan forward
(barreiras constantes por linha permitem busca com estrutura de dados, ex.
segment tree / first-ge).

### 6. Artefatos

- `scripts/comparar_labels_v1530.py` (modos amostra/full; ANTES extraido do git)
- `D:/MarketData/mimo/26/labels_v1530/relatorio_ab_<data>_<modo>_purge<N>.json`
- `D:/MarketData/mimo/26/labels_v1530/labels_<ATIVO>_<data>.jsonl` (DEPOIS)
- `D:/MarketData/mimo/26/labels_v1530_legado/labels_<ATIVO>_<data>.jsonl` (ANTES)
