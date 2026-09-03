# FASE 20 P1 — DECISION JOURNAL

## Problema Resolvido

O sistema de trading precisava de um **audit trail completo** para todas as decisões tomadas durante a operação. Sem isso, era impossível responder: *"Por que o sistema comprou PETR4 às 10:30:15 com score 0.85?"*

## Solução Implementada

### Módulo: `core/decision_journal.py`

Sistema de journal de decisões que registra **cada ação do motor de trading** com contexto completo para auditoria.

#### TradeDecision

Dataclass imutável (após `__post_init__`) que armazena:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `timestamp_do_evento` | float | Unix timestamp quando o evento RTD foi recebido |
| `timestamp_de_processamento` | float | Unix timestamp quando a decisão foi tomada |
| `ativo` | str | Símbolo do ativo (PETR4, VALE3, etc.) — normalizado para UPPERCASE |
| `sinal` | str | `BUY` / `SELL` / `HOLD` / `BLOCKED` |
| `score` | float | Pontuação do modelo ML (0.0-1.0) — arredondado para 6 decimais |
| `features_schema_version` | str | Versão do schema de features usado |
| `model_version` | str | Versão do modelo ML |
| `risk_decision` | str | `ALLOWED` / `BLOCKED_BY_RISK` / `BLOCKED_BY_ML` / `BLOCKED_BY_REPLAY` |
| `motivo` | str | Razão detalhada da decisão |
| `posicao` | float | Posição atual antes da decisão |
| `quantidade` | int | Tamanho da ordem proposta |
| `preco` | float | Preço alvo de execução |
| `estado_sistema` | dict | Snapshot do estado (environment, replay_validated, ml_available) |

#### DecisionJournal

Classe principal com:

- **`record()`** — Registra uma nova decisão no journal
- **`query()`** — Query com filtros (ativo, sinal, timestamps, risk_decision)
- **`explain_decision()`** — Gera explicação formatada de uma decisão específica
- **`get_stats()`** — Estatísticas agregadas do journal
- **`save_to_file()` / `load_from_file()`** — Persistência em JSON
- **Thread-safe** — Lock por operações de leitura/escrita

#### Propriedades Úteis

```python
decision.is_trade    # True se BUY ou SELL
decision.is_blocked  # True se BLOCKED
decision.human_motivo  # String legível: "BUY | score=0.850 | sinal forte"
```

## Uso

```python
from core.decision_journal import DecisionJournal, get_journal

# Uso via instância
journal = DecisionJournal()
decision = journal.record(
    ativo="PETR4",
    sinal="BUY",
    score=0.85,
    motivo="aggr_imb > 0.3 e book_imb > 0.2",
    risk_decision="ALLOWED",
    posicao=0.0,
    quantidade=100,
    preco=25.50,
)

# Uso via instância global
from core.decision_journal import record_decision
record_decision("PETR4", "BUY", 0.85, "sinal forte")

# Explicar decisão
print(journal.explain_decision(0))
# ===== DECISAO #1 =====
# Timestamp Evento:     2026-08-30T10:30:15
# Timestamp Processamento: 2026-08-30T10:30:15
# Ativo:                PETR4
# Sinal:                BUY
# Score ML:             0.850000
# ...

# Query por ativo
petr4_decisions = journal.query(ativo="PETR4")

# Query combinada
recent_trades = journal.query(sinal="BUY", desde=since_timestamp)

# Estatísticas
stats = journal.get_stats()
# {
#   "total_decisions": 1523,
#   "trades_executed": 342,
#   "blocked_decisions": 89,
#   "avg_ml_score": 0.723,
#   ...
# }
```

## Testes

Arquivo: `tests/test_decision_journal.py`

**57 testes** cobrindo:

| Categoria | Testes |
|-----------|--------|
| TradeDecision | is_trade, is_blocked, uppercase, rounding, serialization |
| DecisionJournal | record buy/sell/hold/blocked, timestamps, estado_sistema, position/quantity |
| Query | por ativo, sinal, risk_decision, filtros combinados, ordenação temporal |
| Stats | total, trades, blocks, buys/sells, avg score |
| Blocked Decisions | get_blocked, get_trades_only |
| History | mais recente primeiro, limit |
| Explain | índice válido, bloqueado, fora do range |
| Persistence | save/load, arquivo inexistente, clear |
| Thread Safety | registros concorrentes, queries concorrentes |
| Facade | get_journal, reset, record_decision, query_decisions |
| Integrity | valores preservados, campos presentes, truncamento |
| Edge Cases | journal vazio, score zero/máximo, posição negativa, batch 1000 |

## Integração com o Motor

O motor deve chamar `record_decision()` em três pontos:

1. **Antes de cada ciclo de feature extraction** — para capturar estado do sistema
2. **Após avaliação do ML gate** — registrar decisão com score e source
3. **Após avaliação do risk engine** — registrar bloco ou permissão

```python
# Exemplo de integração no signal_engine.py
def process_book_snapshot(self, snapshot):
    from core.decision_journal import record_decision
    
    ts_evento = time.time()
    
    # 1. Feature extraction
    features = self.feature_engine.extract(snapshot)
    
    # 2. ML decision
    ml_result = self.mlgate.evaluate(features)
    
    # 3. Risk check
    risk_result = self.risk_engine.check(ml_result)
    
    # 4. Registrar no journal
    record_decision(
        ativo=snapshot.ativo,
        sinal=ml_result.sinal,
        score=ml_result.score,
        motivo=risk_result.motivo,
        risk_decision=risk_result.risk_decision,
        posicao=self.position_manager.current_position(snapshot.ativo),
        quantidade=ml_result.quantidade,
        preco=snapshot.preco,
        estado_sistema={
            "environment": self.config.environment.value,
            "replay_validated": self.replay_status.validated,
            "ml_available": ml_result.ml_available,
        },
    )
```

## Persistência

O journal suporta persistência em JSON:

```python
journal = DecisionJournal()
journal.save_to_file("/caminho/journal.json")

# Recarregar depois
journal2 = DecisionJournal()
count = journal2.load_from_file("/caminho/journal.json")
```

Formato do arquivo:

```json
{
  "metadata": {
    "features_schema_version": "v2.3.1",
    "model_version": "xgb-v4.2",
    "saved_at": "2026-08-30T10:30:00",
    "total_entries": 1523,
    "total_decisions": 1523,
    "trades_executed": 342,
    "blocks_applied": 89
  },
  "entries": [
    {
      "timestamp_do_evento": 1725012600.0,
      "timestamp_de_processamento": 1725012600.05,
      "ativo": "PETR4",
      "sinal": "BUY",
      "score": 0.85,
      ...
    }
  ]
}
```

## Invariantes

1. **Ativo sempre em UPPERCASE** — normalizado no `__post_init__`
2. **Score com 6 casas decimais** — arredondado no `__post_init__`
3. **Thread-safety** — todas as operações usam lock
4. **Imutabilidade após registro** — entradas não podem ser modificadas
5. **Persistência automática** — se `save_to_file()` foi chamado, salva em cada `record()`

## Métricas do Journal

```python
stats = journal.get_stats()
# {
#     "total_decisions": 1523,
#     "trades_executed": 342,
#     "blocks_applied": 89,
#     "buy_orders": 201,
#     "sell_orders": 141,
#     "hold_signals": 892,
#     "blocked_decisions": 89,
#     "blocked_by_risk": 67,
#     "avg_ml_score": 0.723456,
#     "features_schema_version": "v2.3.1",
#     "model_version": "xgb-v4.2"
# }
```

## Arquivos Criados

- `core/decision_journal.py` — implementação
- `tests/test_decision_journal.py` — 57 testes

## Referências

- [FASE 8 MLGATE](./FASE8_MLGATE.md) — decisão de ML
- [FASE 9 REPLAYGATE](./FASE9_REPLAYGATE.md) — validação de replay
- [FASE 19 OBSERVABILITY](./FASE19_OBSERVABILITY.md) — métricas do sistema
