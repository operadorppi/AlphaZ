# FASE 17 — P2: REPLAY REALISTA

## Objetivo

Melhorar o replay engine para simular condições de execução mais realistas, sem alterar parâmetros para melhorar resultados. O replay deve representar uma execução conservadora.

## Melhorias Implementadas

### 1. **Latência Simulada**
- Atraso de execução baseado no volume do mercado
- Simula tempo entre signal e execução real

### 2. **Spread Variável**
- Spread dinâmico baseado na volatilidade do ativo
- Spread maior em momentos de alta volatilidade
- Spread menor em momentos de baixa liquidez

### 3. **Slippage Variável**
- Slippage proporcional ao tamanho da ordem
- Slippage maior em movimentos bruscos de preço
- Slippage menor em mercado consolidado

### 4. **Execução Parcial**
- Ordens grandes podem ser executadas parcialmente
- Simula falta de liquidez para ordens grandes
- Partial fills registrados separadamente

### 5. **Rejeição de Ordens**
- Rejeição baseada em circuit breaker
- Rejeição por limite de trades diários
- Rejeição por spread excessivo

### 6. **Custos de Execução**
- Taxa por operação configurável por ativo
- Custo de slippage incluído no cálculo
- Custo fixo por tipo de ordem

### 7. **Stop Intrabar**
- Stop loss pode ser atingido intra-barra
- Monitoramento contínuo durante sessão
- Saída antecipada se stop for atingido

### 8. **Prioridade de Fila**
- Simulação de fila de ordens
- Ordens market têm prioridade sobre limit
- Delay baseado no volume acumulado

## Configurações Adicionadas ao config.json

```json
{
  "replay": {
    "latency_ms": {
      "WINV26": 50,
      "WDOU26": 20,
      "INDV26": 50,
      "DOLU26": 20
    },
    "slippage_model": "volume_based",
    "partial_fill_threshold": 0.8,
    "rejection_probability": {
      "circuit_breaker": 1.0,
      "daily_limit": 1.0,
      "spread_excessive": 0.5
    },
    "execution_costs": {
      "WINV26": 5.0,
      "WDOU26": 1.0,
      "INDV26": 5.0,
      "DOLU26": 1.0
    }
  }
}
```

## Testes Criados

`tests/test_replay_realistic_execution.py`:
- `test_latency_simulation` — verifica atraso de execução
- `test_variable_spread` — spread dinâmico baseado em volatilidade
- `test_variable_slippage` — slippage proporcional ao volume
- `test_partial_execution` — ordens parciais
- `test_order_rejection` — rejeição por circuit breaker
- `test_execution_costs` — custos de execução
- `test_intraday_stop` — stop intrabar
- `test_queue_priority` — prioridade de fila

## Impacto nos Resultados

O replay realista tende a mostrar:
- **Menor win rate** — devido a slippage e execução parcial
- **Menor profit factor** — custos de execução reduzem PnL
- **Maior drawdown** — execuções piores aumentam perdas
- **Mais trades rejeitados** — circuit breakers atuam mais frequentemente

**IMPORTANTE:** Não alterar parâmetros do modelo para compensar a diferença. O replay realista deve ser conservador por design.

## Status

✅ Projeto documentado
⏳ Implementação em andamento
⏳ Testes em desenvolvimento
