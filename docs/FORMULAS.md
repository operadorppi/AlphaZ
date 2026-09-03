# FASE 7 — P1 · Exposição Financeira — Documentação de Fórmulas

## 1. Correção do conceito de `exposure_atual`

### Definição antiga (errada)

```text
exposure_atual := TP + SL
```

Problemas desta definição:

1. **Dimensão incorreta** — TP e SL normalmente são distâncias em
   *pontos de preço*. A soma delas é uma distância, não um valor em
   moeda.
2. **Mede o objeto errado** — TP + SL mede a *faixa de resultado*
   (total de pontos entre alvo e stop), e não o *tamanho* da posição.
3. **Ignora N e V** — duas posições no mesmo ativo, com quantidades e
   valores de ponto diferentes, dariam a mesma "exposição".
4. **Invariante à direção** — uma posição WIN e uma LOSS com a mesma
   entrada e as mesmas distâncias dariam o mesmo valor, mesmo tendo
   sentidos de mercado opostos.

### Definição corrigida (este projeto)

```text
exposure_atual := exposição nominal da posição (E)
risk_at_stop   := risco máximo em moeda se o stop for batido (R)
```

Dois objetos **separados e independentes**:

| Objeto        | Mede                          | Fórmula base            |
|---------------|-------------------------------|-------------------------|
| `E` (exposure)  | Tamanho da posição (notional) | `N · P · V`             |
| `R` (risk_at_stop) | Perda máxima no stop      | `d_stop · N · V`        |
| `L` (lucro no alvo) | Ganho máximo no TP      | `d_target · N · V`      |

TP + SL não desaparece do vocabulário: ela vira a *faixa de
resultado* `(d_target + d_stop)`, usada apenas em comparações de
retorno (R:R) — **nunca** como exposição.

## 2. Variáveis

| Símbolo | Nome                     | Unidade            | Restrição |
|---------|--------------------------|--------------------|-----------|
| `N`     | Quantidade               | contratos/unidades | `N > 0`   |
| `P`     | Preço de entrada         | pontos             | `P > 0`   |
| `V`     | Valor do ponto           | moeda/ponto        | `V > 0`   |
| `S`     | Stop financeiro (preço)  | pontos             | lado perdedor |
| `T`     | Alvo / TP (preço)        | pontos             | lado vencedor |
| `sgn`   | Sinal da direção         | —                  | `+1` (WIN/long) · `−1` (LOSS/short) |

## 3. Fórmulas

### 3.1 Distâncias em pontos

```text
d_stop   = (P − S) · sgn     → WIN: P − S (stop abaixo) ; LOSS: S − P (stop acima)
d_target = (T − P) · sgn     → WIN: T − P (alvo acima) ; LOSS: P − T (alvo abaixo)
```

Validação: `d_stop > 0` e `d_target > 0` (o stop fica obrigatoriamente do
lado perdedor e o alvo do lado vencedor; a construção da `Position`
rejeita o contrário).

### 3.2 Exposição nominal (exposure)

```text
E  = N · P · V
```

- É o **exposure_atual corrigido**: o valor notional da posição em moeda.
- Não depende de `S` nem de `T`.

### 3.3 Risco máximo no stop (risk_at_stop / stop financeiro em moeda)

```text
R  = d_stop · N · V
```

- Perda máxima se o stop for batido, em moeda.
- Independente do alvo.

### 3.4 Lucro máximo no alvo

```text
L  = d_target · N · V
```

### 3.5 Notional assinado (para netting)

```text
E_s = sgn · N · P · V
```

### 3.6 Razões adimensionais

```text
R / E  =  d_stop / P          (fração da exposição em risco)
L / R  =  d_target / d_stop   (risk:reward)
```

### 3.7 Stop financeiro expresso por moeda ou por pontos

```text
stop a X moedas de risco:  S = P − sgn · (X / (N · V))
stop a X pontos de dist:   S = P − sgn · X
```

Implementado em `stop_price_from_risk` e `stop_price_from_distance`
(e métodos `Position.with_stop_risk` / `with_stop_distance`).

### 3.8 Exposição agregada (carteira)

Para posições `i = 1..n`:

```text
E_agg = Σ E_i                  (bruto / gross: soma dos notionais)
R_agg = Σ R_i                  (pior caso: todos os stops batem)
E_net = Σ E_s,i                (líquido: hedges se cancelam)
E_net(j) = Σ_{i∈j} E_s,i       (líquido por ativo j)
```

- `E_agg ≠ E_net` quando há hedge: o bruto conta as duas pernas, o
  líquido as cancela.
- Posições **sem stop** têm `R` indefinido (`None`): não entram em
  `R_agg` e são contadas em `positions_without_stop`.

## 4. Exemplo numérico (posição WIN)

| Campo          | Valor                          |
|----------------|--------------------------------|
| Ativo          | WIN                            |
| Direção        | WIN (long, sgn = +1)           |
| N              | 10 contratos                   |
| P              | 150.000 pontos                 |
| V              | R$ 0,20 por ponto              |
| S (stop)       | 149.800 → d_stop = 200 pts     |
| T (alvo)       | 150.400 → d_target = 400 pts   |

Cálculos:

```text
E  = 10 · 150000 · 0.20        = 300.000
R  = 200 · 10 · 0.20           = 400
L  = 400 · 10 · 0.20           = 800
R/E = 200 / 150000             ≈ 0,1333%
L/R = 400 / 200                = 2

TP + SL (faixa) = 400 + 200    = 600 pontos  (1.200 em moeda)
   → NÃO é exposição (E = 300.000) e NÃO é risco (R = 400)
```

## 5. Validação e casos-limite

| Regra                                              | Comportamento                |
|----------------------------------------------------|------------------------------|
| `N`, `P`, `V` ≤ 0                                 | `ValueError`                 |
| Ativo vazio                                        | `ValueError`                 |
| Stop do lado vencedor (WIN: S ≥ P; LOSS: S ≤ P)    | `ValueError`                 |
| Alvo do lado perdedor (WIN: T ≤ P; LOSS: T ≥ P)    | `ValueError`                 |
| Posição sem stop                                   | `risk_at_stop` levanta erro; na agregação conta em `positions_without_stop` |
| Posição sem alvo                                   | `max_profit_at_target` → `None`; `risk_reward_ratio` → `None` |
| Números                                            | sempre `Decimal` exato (float entra via `repr`) |

## 6. Testes matemáticos independentes

| Arquivo | O que cobre |
|---|---|
| `tests/test_position.py` | Construção, conversão exata (`int/str/float → Decimal`) e todas as validações (stop/alvo do lado errado, campos ≤ 0) |
| `tests/test_formulas.py` | Valores conhecidos; identidade exata `R·750 = E`; simetria WIN/LOSS; **regressão `TP+SL ≠ E` e `TP+SL ≠ R`**; linearidade em N; construtores de stop por moeda/pontos (round-trip) |
| `tests/test_aggregate.py` | `E_agg = ΣE`, `R_agg = ΣR` (soma independente), `E_net` com hedge zerando por ativo, bruto ≠ líquido, exclusão de posição sem stop, carteira vazia |
| `tests/test_invariants.py` | 300 amostras aleatórias (semente fixa): todas as fórmulas recalculadas **por fora** do módulo + invariantes de ordenação (`R ≤ E ⟺ d_stop ≤ P`) e consistência de agregação |

Execução:

```bash
pytest -v
```
