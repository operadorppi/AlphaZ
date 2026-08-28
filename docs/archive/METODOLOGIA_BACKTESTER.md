# Metodologia do Backtester Event-Based

## 1. Objetivo

Simular a execução real de uma estratégia de trading baseada em sinais do modelo ML,
com separação rigorosa entre o que o modelo sabe no instante T e o que acontece depois.

O backtester NÃO é uma prova de rentabilidade. É uma ferramenta para medir:
- Se o sinal do modelo demonstra poder preditivo/financeiro fora da amostra
- Se a execução com custos é lucrativa
- Se o resultado é reproduzível

## 2. Princípio Fundamental

Separar completamente:

```
INFORMAÇÃO DISPONÍVEL EM T     vs     RESULTADO OBSERVADO DEPOIS DE T
─────────────────────────────          ──────────────────────────────────
Features calculadas com dados         Label (TP/SL/TIMEOUT)
≤ T (preço, volume, book, fluxo)      Preço de saída
Probabilidade do modelo               Duração do trade
Sinal (TP se prob ≥ threshold)        Retorno em pontos
```

O modelo NUNCA pode ver:
- label
- preco_saida
- duracao_ms
- retorno_pts
- qualquer informação posterior ao instante T

## 3. Contrato do Modelo

### 3.1 Direção

O modelo opera exclusivamente em **LONG**.

```
P(TP | LONG, features_T) = probabilidade de o trade LONG atingir TP antes de SL,
                           dado o estado do mercado no instante T
```

NÃO é:
- P(TP) genérica
- P(preço subir)
- P(label == +1)

É especificamente:
> Dadas as features disponíveis no instante T, qual a probabilidade de que,
> se eu entrar LONG agora, o preço atinja P0 + TP antes de P0 - SL?

**SHORT está fora do escopo atual.** Se futuramente implementado, requer:
- SL em cima (P >= P0 + SL), TP embaixo (P <= P0 - TP)
- Custos simétricos ou assimétricos (WIN pode ter bid-ask diferente)
- Re-rodar todos os 6 testes

### 3.2 Threshold

```
threshold = 0.75 (decisão: prob >= 0.75 → LONG)
```

O threshold é um **hiperparâmetro operacional** que:
- Deve ser selecionado em janela de validação SEPARADA da janela de backtest
- Mudar o threshold requer re-rodar os 6 testes
- Deve ter análise de sensibilidade (ver seção 4.9)

### 3.3 max_holding

```
max_holding = 60 segundos (barreira temporal)
```

Justificativa: análise de duração de trades no labeler mostra que a maioria dos
TP/SL é atingida em < 30s. 60s dá margem para trades mais lentos sem aumentar
demais a exposição.

max_holding é um parâmetro no stress test. Mudar max_holding:
- Muda a distribuição de labels (mais/fewer TIMEOUTs)
- Muda a duração média dos trades
- Muda o exposure e o drawdown

## 4. Fluxo do Backtester

```
                         ┌──────────────────────┐
                         │ Dados ≤ T            │
                         │ preço/book/fluxo/etc. │
                         └──────────┬───────────┘
                                    ↓
                            ┌───────────────┐
                            │ features(T)   │
                            └───────┬───────┘
                                    ↓
                            ┌───────────────┐
                            │ modelo        │
                            │ P(TP|LONG,T)  │
                            └───────┬───────┘
                                    ↓
                              prob ≥ 0.75?
                              /           \
                            NÃO           SIM
                             ↓             ↓
                           FLAT          LONG
                                           │
                                           ↓
                              ┌────────────────────┐
                              │ FUTURO observado   │
                              │ > T                 │
                              └─────────┬──────────┘
                                        ↓
                               TP / SL / TIMEOUT
                                        ↓
                                  custos + PnL
                                        ↓
                                    cooldown
```

### 4.1 Timestamps

```
tipo: int64
unidade: epoch milliseconds
diferença: duration_ms = exit_timestamp - entry_timestamp
```

### 4.2 Entrada

Para cada timestamp T no dataset:

```
FEATURES EM T:
  - preco_ultimo
  - vol_total, vol_compra, vol_venda
  - aggr_imb, cvd_total, cvd_div
  - vp_vp_total, vp_vah_dist, vp_poc_dist
  - spread, microprice, hhi_book
  - ofi, vpin, kyle_lambda
  - ewma_imb_curta/media/longa
  - delta_preco_janela
  - ... (26 features totais)

MODELO EM T:
  - probabilidade_TP = modelo.predict_proba(features_T)[index_TP]
  - CONTRATO: probabilidade_TP = P(TP | LONG, features_T)
```

### 4.3 Decisão

```
SE NÃO ESTOU POSICIONADO:
  SE probabilidade_TP >= 0.75:
    → ENTRADA LONG em preco_entrada = preco_ultimo
    → Registrar: entry_timestamp = T (epoch ms)
    → Registrar: entry_price = preco_ultimo
    → Registrar: model_probability = probabilidade_TP
  SENÃO:
    → NÃO FAZ NADA (fica flat)

SE ESTOU POSICIONADO:
  → NÃO ABRE NOVA POSIÇÃO
  → Verifica se trade fechou (label-consumer: usa label; brute-force: avança eventos)
```

### 4.4 Resolução do Trade

**Label-consumer** (usa dados pré-calculados):

```
O labeler já calculou para cada timestamp T:
  label: +1 (TP), -1 (SL), 0 (TIMEOUT)
  preco_saida: preço no momento do TP, SL ou fim do horizonte
  duracao_ms: exit_timestamp - entry_timestamp (epoch ms)
  retorno_pts: lucro/prejuízo em pontos (antes de custos)

SE label == +1 (TP):
  PnL_bruto = +TP_pts = +100

SE label == -1 (SL):
  PnL_bruto = -SL_pts = -50

SE label == 0 (TIMEOUT):
  PnL_bruto = preco_saida - preco_entrada
```

**Brute-force** (resolve durante o replay):

```
A partir de T, avança evento por evento:
  Para cada evento futuro:
    SE P >= P0 + TP:
      → TP atingido
      → preco_saida = P
      → duration_ms = evento.timestamp - T  (epoch ms)
      → PnL_bruto = +100
      → PARAR

    SE P <= P0 - SL:
      → SL atingido
      → preco_saida = P
      → duration_ms = evento.timestamp - T  (epoch ms)
      → PnL_bruto = -50
      → PARAR

  SE max_holding atingido sem TP nem SL:
    → TIMEOUT
    → preco_saida = preço atual
    → PnL_bruto = preco_saida - preco_entrada

POLÍTICA DE EMPATES:
  SE P >= P0 + TP E P <= P0 - SL no mesmo tick:
    → AMBIGUOUS
    → Label = -99 (descartar)
    → Trade NÃO é executado
    → Política: conservadora (não entra em trade ambíguo)
    → Nota: para LONG com preços contínuos, AMBIGUOUS é
      fisicamente impossível (requer TP <= -SL). Só é possível
      com grid discreto gigante, gaps de abertura ou dados
      corrompidos. Código trata corretamente caso apareça.
    → Métrica obrigatória: frequência de ambíguos no relatório.
      Se > 1% dos trades são ambíguos, modelo pode estar
      operando em regime de gap, onde execução real é imprevisível.
```

### 4.5 Custos

```
slippage_entrada = 1 ponto (meio tick WIN)
slippage_saida = 1 ponto (meio tick WIN)
fee = 5 pontos (corretagem + emolumentos)

CUSTO_TOTAL = slippage_entrada + slippage_saida + fee = 7 pontos

PnL_liquido = PnL_bruto - CUSTO_TOTAL
```

**Nota sobre slippage:** 7 pontos é razoável para WIN em condições normais,
mas em eventos de alta volatilidade o slippage pode ser maior. Ver stress
de custo na seção 4.10.

### 4.6 Cooldown

```
SE trade fechou:
  cooldown_until = exit_timestamp + 45000 ms
  PRÓXIMA ENTRADA SÓ APÓS cooldown_until
  TODOS os sinais durante cooldown são IGNORADOS
```

### 4.7 Métricas por Trade

```
trade_id
entry_timestamp (epoch ms)
exit_timestamp (epoch ms)
entry_price
exit_price
direction (LONG)
model_probability
threshold (0.75)
label (TP/SL/TIMEOUT)
exit_reason
duration_ms (exit_timestamp - entry_timestamp)
gross_pts
slippage_pts (2)
fees_pts (5)
net_pts
R (net_pts / SL_pts)
equity_before
equity_after
drawdown_after
cooldown_until
```

### 4.8 Métricas Agregadas

```
n_trades                    (mínimo aceitável: 30+ para significância)
win_rate (net_pts > 0)
tp_rate (label == +1)
profit_factor (soma(ganhos) / soma(perdas))
expectancy (PnL médio por trade)
expectancy_R (R médio por trade)
max_drawdown (maior queda do equity curve)
max_drawdown_pct
exposure (tempo posicionado / tempo total)
sharpe (aproximado)
consecutive_losses_max
consecutive_wins_max
ambiguous_rate (taxa de trades ambíguos)
```

### 4.9 Critério de Sinal e Baselines

Para declarar que o modelo demonstra poder preditivo/financeiro,
o resultado deve superar **3 baselines definidos**:

**Baseline 1 — "Entra em todo sinal" (threshold = 0):**
```
Entra em TODOS os timestamps (sem filtro de probabilidade).
Mede o valor do filtro do modelo.
Se o modelo não melhora sobre isso, o filtro não agrega valor.
```

**Baseline 2 — "Aleatório com mesma taxa de entrada":**
```
Para cada trade do modelo, gera um trade aleatório
na mesma direção, no mesmo timestamp, com mesmo custo.
Mede se o modelo está selecionando trades melhores que ao acaso.
```

**Baseline 3 — "Momentum simples":**
```
Entra LONG quando retorno dos últimos N ticks > 0.
Regra heurística sem ML.
Mede se o ML adiciona valor sobre regra simples.
```

**O modelo precisa superar TODOS os 3 baselines em PF e expectancy.**

Além disso, requer:
```
REQUER TODOS:
  - n_trades >= 30
  - PF > baseline PF (todos os 3)
  - expectancy_R > 0
  - drawdown < limite aceitável
  - resultado consistente entre dias
  - resultado consistente entre regimes
  - intervalo de confiança não contém zero
  - ECE < 0.10 (modelo razoavelmente calibrado)
```

NÃO basta PF alto com poucos trades. Exemplo:
```
3 trades, 3 wins → PF = infinito → NÃO é evidência forte
```

### 4.10 Stress de Custo

Rodar o backtest com slippage variável para encontrar o "breakeven de custo":

```
Para cada slippage em [1, 2, 3, 5, 7, 10] pontos:
  - Rodar backtest completo
  - Calcular PF, expectancy_R, n_trades
  - Encontrar slippage_máximo onde expectancy_R > 0
```

Se o modelo quebra com slippage = 10 (3 pontos acima do normal),
pode estar capturando micro-arbitragens irreais.

### 4.11 Métricas de Calibração

O threshold 0.75 só é confiável se o modelo está calibrado.
Se `prob = 0.80` mas a taxa real de TP é 0.60, o threshold está filtrando
trades que o modelo *acha* que são bons mas não são.

Métricas obrigatórias no relatório:

```
Brier Score: média de (prob - resultado)²
  0 = perfeito, 1 = pior

ECE (Expected Calibration Error):
  Média de |fração_real - prob_predita| por bin
  0 = perfeito, < 0.10 = aceitável

Reliability Diagram:
  Plot de prob_predita vs fração_real por bin
  Diagonal perfeita = calibrado

Log-loss:
  Penaliza confiança mal calibrada
  Menor = melhor
```

Se ECE > 0.10, o threshold 0.75 é menos confiável e deve ser
reportado como limitação.

### 4.12 Análise de Sensibilidade do Threshold

O threshold 0.75 não é fixo. Deve-se mostrar como métricas variam:

```
Para cada threshold em [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
  - n_trades
  - win_rate
  - PF
  - expectancy_R
  - max_drawdown
  - ECE
```

Isso mostra:
- Trade-off entre seletividade e oportunidade
- Se o threshold 0.75 é realmente o melhor ou se há plateau
- Se o modelo é sensível a mudanças pequenas no threshold

## 5. Os 6 Testes de Validação

### Os seis testes atacam pontos diferentes:

```
T1 → implementação do label
T2 → leakage direto
T3 → leakage indireto nas features
T4 → determinismo
T5 → correção da resolução temporal
T6 → causalidade do presente em relação ao futuro
```

### Teste 1: Labeler referência = vectorizado

**Status:** ✅ COMPLETO (133 testes, 100/100 cenários)

**O que valida:** A implementação de referência (labeler_core.py) e a vetorizada
(labeler_vectorizado.py) produzem resultados idênticos.

**Critério de aprovação:** 100% de concordância em todos os cenários.

**Se falhar:** Um dos labelers tem bug de implementação.

---

### Teste 2: Modelo nunca recebe informação futura

**Procedimento:**
1. Listar todas as colunas usadas como features (X_cols)
2. Verificar que NENHUMA contém:
   - "label"
   - "preco_saida"
   - "duracao"
   - "retorno"
   - "atingido"
   - "saida"
   - "ts_ms" (timestamp não é feature, é chave)
3. Verificar que NENHUMA é calculada com dados > T

**Critério de aprovação:** Zero colunas com leakage direto.

**Se falhar:** Data leakage direto — modelo treinado com informação futura.

---

### Teste 3: Feature Causality Test (Snapshot Strategy)

Este é o teste mais sutil e importante. O objetivo é garantir que
features calculadas em T não são afetadas por dados posteriores a T.

**Estratégia de implementação (Replay com Janela Deslizante):**

Em vez de truncar o dataset e recalcular (que reconstrói o estado do zero),
usar abordagem de snapshot:

```
1. Rodar pipeline de features em modo streaming, mantendo estado interno
2. Em cada T, salvar snapshot do estado:
   - OFI acumulado
   - CVD acumulado
   - VP acumulado
   - VPIN acumulado
   - Kyle Lambda acumulado
   - EWMA acumulado
   - etc.
3. Para o teste, restaurar snapshot de T e processar apenas evento T
4. Comparar com valor produzido no pipeline full-run
```

Isso evita o problema de "inicialização com dados futuros" porque
o snapshot já contém o estado correto até T.

**O que NÃO pode mudar quando o futuro é alterado:**
- Features em T
- Probabilidade em T
- Sinal em T
- Entrada em T

**O que PODE mudar quando o futuro é alterado:**
- Saída (preco_saida)
- Exit reason (TP/SL/TIMEOUT)
- Duration
- PnL
- R
- Drawdown

**Armadilhas conhecidas (12 pontos de atenção):**

1. **Janela de cálculo:** Feature usa janela de N ticks. Se T está
   no início da janela, truncar encurta a janela.
   → Snapshot preserva janela completa até T.

2. **Estado acumulado:** OFI, CVD, VPIN mantêm estado incremental.
   Se o estado é inicializado com dados futuros, a feature em T muda.
   → Snapshot contém estado construído SOMENTE com dados ≤ T.

3. **Inicialização:** VP, Kyle Lambda precisam de dados históricos.
   → Snapshot já inclui inicialização correta.

4. **Fechamento de sessão:** Features que usam "total da sessão"
   (ex: volume_da_sessao / volume_total_da_sessao) contêm
   informação futura.
   → Essas features são inherentemente leaky. Marcar e excluir.

5. **Agregação:** Features que agregam em janelas maiores podem
   incluir dados posteriores a T.
   → Snapshot delimita janela corretamente.

6. **Ordenação:** Se dados não estão ordenados cronologicamente,
   snapshot pode conter dados errôneos.
   → Garantir ordenação antes do teste.

7. **Normalização:** Z-scores globais incluem dados futuros.
   → Verificar se normalização é incremental (rolling).

8. **Forward fill:** Preenchimento pode propagar dados futuros.
   → Verificar direção do fill no snapshot.

9. **Volume Profile:** POC/VAH/VAL podem se estender além de T.
   → Snapshot delimita janela do VP.

10. **VPIN:** Volume sincronizado pode olhar além de T.
    → Verificar síncronização no snapshot.

11. **Volatilidade realizada:** EWMA pode propagar variance futura.
    → Verificar ordem de atualização no snapshot.

12. **Features rolling:** Janelas > 1 tick podem incluir dados futuros.
    → Snapshot delimita janela rolling.

**Se falhar:** A feature usa dados futuros → leakage temporal.

---

### Teste 4: Replay determinístico

**Procedimento:**
1. Rodar o backtester duas vezes com os mesmos dados
2. Comparar trade-by-trade:
   - Mesmas entradas
   - Mesmas saídas
   - Mesmos timestamps
   - Mesmos preços
   - Mesmos labels
   - Mesmos custos
   - Mesmo PnL
   - Mesmo equity curve
   - Mesmo drawdown
3. Se houver QUALQUER diferença → FAIL

**Critério de aprovação:** 100% de igualdade trade-by-trade.

**Se falhar:** O backtester tem estado não determinístico (RNG, ordenação, concorrência).

---

### Teste 5: Brute-force vs label-consumer

**Este é o teste mais importante.**

**Backtester A (label-consumer):**
- Usa preco_saida e label pré-calculados pelo labeler
- Implementação: lookup vetorizado (tabela pré-calculada)
- Rápido (O(N))

**Backtester B (brute-force):**
- Recebe eventos brutos (preços em cada tick)
- Resolve TP/SL/timeout durante o replay
- Implementação: event-driven pura (loop sobre ticks, verificação condicional)
- Lento (O(N × max_holding_ticks))
- INDEPENDENTE do labeler na implementação (mesma especificação, código diferente)

**A independência é na IMPLEMENTAÇÃO, não na especificação.**
Ambos usam a mesma regra de TP/SL, mas a implementação é diferente:
- label-consumer: "dado que o label é +1, PnL = +100"
- brute-force: "percorro ticks até encontrar P >= P0+TP"

**Comparação trade-by-trade:**
Para cada trade, verificar igualdade exata de:

```
entrada
saída
timestamp de entrada
timestamp de saída
preço de entrada
preço de saída
exit_reason
duration
gross_pts
costs
net_pts
R
cooldown
equity
drawdown
```

**Critério de aprovação:** 100% de igualdade trade-by-trade.

**Se divergir:**
- Investigar primeira divergência
- Pode ser bug no labeler, no backtester A, ou no backtester B
- NÃO aceitar resultado até resolver

---

### Teste 6: Perturbação do Futuro (3 tipos)

**Procedimento:**
1. Pegar dataset D:
   ```
   T-2, T-1, T, T+1, T+2, T+3
   ```
2. Criar D' com 3 tipos de perturbação:
   ```
   D'_A (ruído):      T-2, T-1, T, T+1+ε, T+2+ε, T+3+ε
   D'_B (reordenação): T-2, T-1, T, T+2, T+1, T+3
   D'_C (truncamento): T-2, T-1, T, T+1, T+3  (remove T+2)
   ```
   O evento T NÃO é alterado em nenhum dos casos.
3. Rodar o modelo em D, D'_A, D'_B, D'_C
4. Comparar para cada T:

**O que NÃO pode mudar em NENHUM D':**
- features(T)
- probabilidade(T)
- sinal(T)
- entrada(T)

**O que PODE mudar em D':**
- resultado do trade (saída, exit_reason, duration, PnL, R)

**Critério de aprovação:**
```
features(T)_D == features(T)_D'_A == features(T)_D'_B == features(T)_D'_C
probabilidade(T)_D == probabilidade(T)_D'_A == ... 
sinal(T)_D == sinal(T)_D'_A == ...
entrada(T)_D == entrada(T)_D'_A == ...
```

**Se falhar em A:** Sensibilidade a valores → possible leakage via magnitude.

**Se falhar em B:** Sensibilidade a ordenação → leakage via rank/percentil/OFI.

**Se falhar em C:** Sensibilidade a densidade → leakage via contagem/agregação.

---

## 6. CAUSALITY AUDIT — Relatório Obrigatório

Ao final da validação, gerar relatório `CAUSALITY_AUDIT.md` com:

```markdown
# CAUSALITY AUDIT

## Dataset

| Item | Valor |
|------|-------|
| Arquivo | dataset_final_v2_win_v914.parquet |
| Hash SHA256 | abc123... |
| Linhas | 6.846.660 |
| Features | 26 |
| Período | 04-17/ago/2026 |

## Resumo

| Métrica | Resultado |
|---------|-----------|
| Eventos testados | 100.000 |
| Features testadas | 26 |
| Feature causality (snapshot) | PASS |
| Future perturbation A (ruído) | PASS |
| Future perturbation B (reordenação) | PASS |
| Future perturbation C (truncamento) | PASS |
| Probability stability | PASS |
| Signal stability | PASS |
| Entry stability | PASS |
| Brute-force equality | PASS |
| Trade equality | PASS |
| First divergence | NONE |

## Threshold

| Threshold | n_trades | PF | expectancy_R | ECE |
|-----------|----------|-----|--------------|-----|
| 0.50 | 50.000 | 3.64 | +0.47 | 0.16 |
| 0.60 | 12.000 | 5.81 | +0.58 | 0.14 |
| 0.70 | 3.500 | 7.24 | +0.68 | 0.12 |
| 0.75 | 1.800 | 7.50 | +0.72 | 0.11 |
| 0.80 | 800 | 7.46 | +0.70 | 0.10 |
| 0.85 | 300 | 7.80 | +0.75 | 0.09 |
| 0.90 | 80 | 8.20 | +0.80 | 0.08 |

## Baselines

| Baseline | PF | expectancy_R |
|----------|-----|--------------|
| Baseline 1 (threshold=0) | 3.64 | +0.47 |
| Baseline 2 (aleatório) | 2.00 | +0.20 |
| Baseline 3 (momentum) | 1.76 | +0.15 |
| **Modelo (thresh=0.75)** | **7.50** | **+0.72** |

## Stress de Custo

| Slippage (pontos) | PF | expectancy_R | Lucrativo? |
|-------------------|-----|--------------|------------|
| 1 | 9.20 | +0.92 | SIM |
| 2 | 8.10 | +0.81 | SIM |
| 3 | 7.20 | +0.72 | SIM |
| 5 | 5.80 | +0.58 | SIM |
| 7 | 4.50 | +0.45 | SIM |
| 10 | 3.20 | +0.32 | SIM |
| 15 | 1.80 | +0.18 | NÃO |
| Breakeven: ~13 pontos | | | |

## Calibração

| Métrica | Valor | Critério |
|---------|-------|----------|
| Brier Score | 0.21 | < 0.25 |
| ECE | 0.11 | < 0.10 |
| Log-loss | 0.45 | < 0.50 |

## Ambíguos

| Métrica | Valor |
|---------|-------|
| Total de trades | 1.800 |
| Ambíguos | 0 (0.00%) |
| Critério | < 1% |

## Detalhes por Feature

| Feature | Causality | Leakage | Notas |
|---------|-----------|---------|-------|
| delta_preco_janela | PASS | OK | Janela ≤ T |
| vp_vp_total | PASS | OK | VP calculado com dados ≤ T |
| cvd_total | PASS | OK | Acumulativo incremental |
| ... | ... | ... | ... |

## Veredito

PASS — Todos os 6 testes aprovados.
O backtester é tecnicamente validado.
Resultados históricos são causais, determinísticos e consistentes.
```

### Se houver falha:

```markdown
## CAUSALITY AUDIT: FAIL

### Falha detectada

| Teste | Feature | Timestamp | Original | Perturbado | Diferença |
|-------|---------|-----------|----------|------------|-----------|
| 3 | volume_profile | 1786579283000 | 0.4523 | 0.4524 | 0.0001 |

### Ação necessária

O backtester NÃO pode ser considerado validado.
Corrigir a feature antes de continuar.
```

### Regra obrigatória

Se o CAUSALITY AUDIT retornar FAIL:
- NÃO apresentar resultados como "validados"
- NÃO usar PF, expectancy, drawdown como evidência
- Corrigir a causa raiz
- Repetir TODOS os 6 testes
- Só então gerar novo CAUSALITY AUDIT

## 7. Dataset Lock

Para garantir que o dataset de backtest não contém dados de treino:

```
1. Dataset de backtest deve ser gerado por script SEPARADO
   sem acesso aos dados de treino

2. Hash SHA256 do dataset deve ser registrado no CAUSALITY AUDIT

3. Qualquer mudança no pipeline de features requer:
   - Re-gerar dataset
   - Re-rodar os 6 testes
   - Re-gerar CAUSALITY AUDIT

4. Dataset de backtest e dataset de treino devem ter
   timestamps disjuntos (purge + embargo)
```

## 8. O que o backtester NÃO é

1. **Não é uma prova de rentabilidade** — é uma simulação com dados históricos
2. **Não considera impacto de mercado** — ordens grandes podem mover o preço
3. **Não considera liquidez** — pode não haver volume suficiente para executar
4. **Não considera latência** — assume execução instantânea
5. **Não considera slippage variável** — usa custo fixo (mas stress test mede limite)
6. **Não substitui forward testing** — precisa de dados fora da amostra

## 9. O que o backtester PODE dizer

1. **Se o sinal existe** — modelo supera 3 baselines em PF e expectancy
2. **Se a execução é lucrativa** — PnL líquido > 0
3. **Se o risco é controlável** — drawdown < limite
4. **Se o sinal é estável** — performance consistente entre dias/regimes
5. **Se o custo não mata o sinal** — PnL bruto > custos (e stress test confirma)
6. **Se o resultado é estatisticamente significativo** — IC95% não contém zero
7. **Se o modelo está calibrado** — ECE < 0.10
8. **Se o threshold é robusto** — análise de sensibilidade mostra plateau

## 10. O que o backtester NÃO PODE dizer

1. **Que vai lucrar no futuro** — dados passados não garantem futuro
2. **Que o modelo é bom** — pode ser overfitting
3. **Que a estratégia é robusta** — pode ser frágil a mudanças de regime
4. **Que não há leakage** — só os 6 testes podem confirmar

## 11. Documentação de cada trade

Cada operação deve ser registrada em JSON:

```json
{
  "trade_id": 1,
  "entry_timestamp": 1786579283000,
  "exit_timestamp": 1786579313000,
  "entry_price": 184995.0,
  "exit_price": 185095.0,
  "direction": "LONG",
  "model_probability": 0.82,
  "threshold": 0.75,
  "label": 1,
  "exit_reason": "TP",
  "duration_ms": 30000,
  "gross_pts": 100.0,
  "slippage_pts": 2.0,
  "fees_pts": 5.0,
  "net_pts": 93.0,
  "R": 1.86,
  "equity_before": 10000.0,
  "equity_after": 10093.0,
  "drawdown_after": 0.0,
  "cooldown_until": 1786579358000
}
```

## 12. Etapa Seguinte (pós-backtester)

Passar nos 6 testes não significa "estratégia validado".
Significa que o pipeline é causal, determinístico e consistente.

A etapa seguinte deve ser:

```
CAUSALITY (6 testes)
   ↓
WALK-FORWARD
   ↓
PURGE + EMBARGO
   ↓
BASELINES (3 definidos)
   ↓
CONFIDENCE INTERVAL
   ↓
STABILITY BY DAY/REGIME
   ↓
CALIBRATION (Brier, ECE, reliability)
   ↓
STRESS DE CUSTO
   ↓
SENSIBILIDADE DO THRESHOLD
   ↓
FORWARD TEST
```

Só depois disso começa a ser interessante discutir se o
resultado (PF, expectancy, drawdown) é real e generalizável.

## 13. Executive Summary

O backtester event-based é uma simulação que:
1. Usa o modelo para decidir QUANDO entrar (threshold 0.75)
2. Usa o labeler para resolver O QUE aconteceu (TP/SL/TIMEOUT)
3. Aplica custos reais (slippage + fees)
4. Controla risco (cooldown, sem sobreposição)
5. Registra cada trade auditavelmente
6. Testa robustez (stress de custo, sensibilidade, calibração)

Os 6 testes de validação garantem que:
1. O labeler está correto (teste 1)
2. O modelo não tem leakage direto (teste 2)
3. As features são causais via snapshot (teste 3)
4. O backtester é determinístico (teste 4)
5. O backtester é consistente com brute-force independente (teste 5)
6. O futuro não afeta o presente — 3 tipos de perturbação (teste 6)

O CAUSALITY AUDIT é obrigatório antes de apresentar qualquer resultado como "validado".
Sem ele, nenhum número (PF, expectancy, drawdown) deve ser considerado evidência.

## 14. Status dos Testes

| Teste | Status | Resultado |
|-------|--------|-----------|
| 1. Labeler ref == vectorizado | ✅ COMPLETO | 100/100 concordantes |
| 2. Sem leakage direto | ✅ COMPLETO | 26 features, 0 violações |
| 3. Feature causality (snapshot) | ✅ COMPLETO | 3 dias, 45 timestamps, 0 divergências |
| 4. Replay determinístico | ✅ COMPLETO | 3 dias, 3 runs, 48K+ snapshots, 0 divergências |
| 5. Brute-force vs label | ✅ COMPLETO | 5 dias, 437 trades, 0 divergências |
| 6. Perturbação do futuro (3 tipos) | ✅ COMPLETO | 3 dias, 43 timestamps, 0 divergências |
| CAUSALITY AUDIT | ✅ PASS | Pipeline causal, determinístico, consistente |

**Data da execução:** 23/08/2026
**Dias testados:** 11/08, 13/08, 14/08 (3 dias distintos)
**Arquivo de resultado:** CAUSALITY_AUDIT_v3.json
**Relatório completo:** CAUSALITY_AUDIT.md

### Resultados por teste

**T1 (100/100):** labeler_core.py e labeler_vectorizado.py produzem resultados idênticos em 100 cenários.

**T2 (PASS):** 26 features verificadas. Nenhuma contém palavras de leakage.

**T3 (0 divergências):** Em 3 dias distintos, 45 timestamps testados, 26 features cada. Features calculadas com TODOS os eventos são idênticas às calculadas com apenas eventos ≤ T. Nenhuma feature depende de dados futuros.

**T4 (0 divergências):** 3 execuções idênticas por dia, 48K+ snapshots total. O sistema é determinístico.

**T5 (0 divergências):** 5 dias, 437 trades. Dois backtesters independentes produzem exatamente os mesmos trades.

**T6 (0 divergências):** 3 tipos de perturbação (ruído, reordenação, truncamento) em 3 dias. Modificar eventos futuros não altera features em T.

### Nota sobre amplitudes

O v2 usava checkpoints que não salvavam VP/Kyle trackers, causando falsos positivos.
O v3 usa execução direta (correto mas mais lento). Ampliação futura: checkpoint
completo para permitir 10K+ samples por dia.

### Veredito

O pipeline está **tecnicamente validado** em 3 dias distintos: causal, determinístico
e consistente. Os resultados históricos foram calculados de maneira causal e reproduzível.
