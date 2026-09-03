# Risk Governance — Mapeamento de Responsabilidades (Fase 6)

## 1. Estado Atual — 3 Componentes de Risco

### 1.1 RiskManager (`core/risk_manager.py`)

**Função original:** Gatekeeper legado + cálculo de barreiras dinâmicas.

| Responsabilidade | Método | Sobreposição? |
|-----------------|--------|---------------|
| Stop diário (max drawdown) | `pode_abrir()` → `self.pnl_dia <= self.stop_diario` | ⚠️ Duplicada no RiskEngine |
| Perdas consecutivas | `pode_abrir()` → `self.perdas_consecutivas >= max` | ⚠️ Duplicada no RiskEngine |
| Horário de operação | `horario_permite_abrir()` | ⚠️ Duplicada no RiskEngine |
| Kill switch | `self.kill_switch_ativo` | ⚠️ Duplicada no RiskEngine |
| Slippage circuit breaker | `registrar_execucao()` | ✅ Único (não no RiskEngine) |
| Sanidade de alvos (TP/SL) | `validar_sanidade_alvos()` | ✅ Único (não no RiskEngine) |
| Position sizing | `pode_abrir()` → `size = round(target_risk / signal.sl)` | ⚠️ Duplicada no RiskEngine |
| Barreiras dinâmicas (TP/SL) | `calcular_barreiras_dinamicas()` | ✅ Único (cálculo de TP/SL por regime) |
| Custo de execução | `custo_execucao()` (função livre) | ✅ Único |
| Registrar resultado | `registrar_resultado()` | ⚠️ Duplicada no RiskEngine |

### 1.2 RiskEngine (`core/risk_engine.py`)

**Função original:** Risk Engine v2 com 14 proteções (Fase 12).

| Responsabilidade | Método | Sobreposição? |
|-----------------|--------|---------------|
| 1. Daily loss limit | `_check_daily_loss()` | ⚠️ Duplicada no RiskManager |
| 2. Max exposure | `_check_exposure()` | ✅ Único |
| 3. Max position | `_check_position()` | ⚠️ Duplicada no RiskManager |
| 4. Max trades | `_check_max_trades()` | ✅ Único |
| 5. Cooldown | `_check_cooldown()` | ⚠️ Parcialmente no PositionManager (`_cooldown_until`) |
| 6. Consecutive loss | `_check_consecutive_loss()` | ⚠️ Duplicada no RiskManager |
| 7. Stale data | `_check_stale_data()` | ✅ Único |
| 8. Spread protection | `_check_spread()` | ✅ Único |
| 9. Volatility protection | `_check_volatility()` | ✅ Único |
| 10. Model unavailable | `_check_model_availability()` | ✅ Único |
| 11. Confidence protection | `_check_confidence()` | ⚠️ Parcialmente no PositionManager (`sinal_valido`) |
| 12. Session protection | `_check_session()` | ⚠️ Duplicada no RiskEngine |
| 13. Kill switch | `_check_kill_switch()` | ⚠️ Duplicada no RiskManager |
| 14. Circuit breaker | `_check_circuit_breaker()` | ⚠️ Parcialmente no RiskManager |
| Sizing | `_check_position()` → `size` | ⚠️ Duplicada no RiskManager |
| Registrar resultado | `registrar_resultado()` | ⚠️ Duplicada no RiskManager |
| Estado de mercado | `atualizar_mercado()` | ✅ Único |

### 1.3 PositionManager (`core/position_manager.py`)

**Função original:** Gerir posições abertas (abrir/manter/fechar).

| Responsabilidade | Método | Sobreposição? |
|-----------------|--------|---------------|
| Confiança mínima (limiar) | `sinal_valido = abs(confianca) >= limiar` | ⚠️ Duplicada no RiskEngine (#11) |
| Streak mínimo | `self._sinal_streak >= 2` | ✅ Único (estabilidade de sinal) |
| Cooldown pós-fechamento | `self._cooldown_until` | ⚠️ Duplicada no RiskEngine (#5) |
| ML sizing | `ml_prob > 0.7 → size+1; < 0.55 → size-1` | ⚠️ Sobrepõe RiskEngine sizing |
| Max position (piramidação) | `pos['quantidade'] < max_qty` | ⚠️ Duplicada no RiskEngine (#3) |
| Stop/preço médio (piramidação) | Atualiza `stop_preco` e `preco_medio` | ✅ Único (gestão de posição) |
| Fallback: chama RiskManager | `if not decision: decision = self.risk.pode_abrir()` | ⚠️ Usa RiskManager legado em vez de RiskEngine |
| `self.risk.trades_dia += 1` | Incrementa contador no RiskManager | ⚠️ Deveria ser no RiskEngine |
| `self.risk.registrar_execucao()` | Registra slippage no RiskManager | ⚠️ Deveria ser no RiskEngine |

## 2. Quem Decide O Quê (Estado Atual)

| Regra | RiskManager | RiskEngine | PositionManager |
|-------|:-----------:|:----------:|:---------------:|
| Max drawdown dia | ✅ `pode_abrir` | ✅ `_check_daily_loss` | ❌ |
| Max exposure | ❌ | ✅ `_check_exposure` | ❌ |
| Tamanho (sizing) | ✅ `pode_abrir` | ✅ `_check_position` | ⚠️ `ml_sizing` sobrepõe |
| Stop (SL) | ✅ `calcular_barreiras` | ❌ | ❌ |
| Bloqueio (kill switch) | ✅ `kill_switch_ativo` | ✅ `_check_kill_switch` | ❌ |
| Cooldown | ❌ | ✅ `_check_cooldown` | ⚠️ `_cooldown_until` separado |
| ML indisponível | ❌ | ✅ `_check_model_availability` | ❌ |
| Staleness | ❌ | ✅ `_check_stale_data` | ❌ |
| Confiança mínima | ❌ | ✅ `_check_confidence` | ⚠️ `sinal_valido` separado |
| Perdas consecutivas | ✅ `pode_abrir` | ✅ `_check_consecutive_loss` | ❌ |
| Max trades | ❌ | ✅ `_check_max_trades` | ❌ |
| Spread | ❌ | ✅ `_check_spread` | ❌ |
| Volatilidade | ❌ | ✅ `_check_volatility` | ❌ |
| Horário | ✅ `horario_permite_abrir` | ✅ `_check_session` | ❌ |
| Circuit breaker | ⚠️ `circuit_breaker_nivel` | ✅ `_check_circuit_breaker` | ❌ |

**Conclusão:** 8 regras estão duplicadas entre RiskManager e RiskEngine. 3 regras estão triplicadas (também no PositionManager).

## 3. Arquitetura Desejada

```
Signal
  ↓
RiskEngine.avaliar()  ← ÚNICA fonte de verdade para decisão de risco
  ↓
RiskDecision (canônico: allowed, reason, size, tp, sl, risk_score)
  ↓
PositionManager.gerenciar()  ← Executa a decisão, não reinventa regras
  ↓
Action
```

### Princípios:
1. **RiskEngine.avaliar()** é a única função que decide se um trade pode abrir
2. **PositionManager** executa a decisão — não valida risco novamente
3. **RiskManager** torna-se um adapter de compatibilidade (thin wrapper) que delega ao RiskEngine
4. Estado de risco (pnl_dia, perdas, circuit breaker) vive no RiskEngine apenas
5. Cooldown, sizing, e confidence check saem do PositionManager

## 4. Plano de Migração

### Passo 1: PositionManager para de chamar RiskManager
- Remover `if not decision: decision = self.risk.pode_abrir(signal)`
- Se `decision` não foi injetado, retornar `Action(tipo='REJEITADO', motivo='SEM_RISK_DECISION')`
- Remover `self.risk.trades_dia += 1` (RiskEngine já incrementa em `avaliar()`)
- Remover `self.risk.registrar_execucao()` (mover para RiskEngine ou app.py)

### Passo 2: PositionManager para de reinventar regras
- Remover `sinal_valido` check (RiskEngine `_check_confidence` já faz)
- Remover `_cooldown_until` (RiskEngine `_check_cooldown` já faz)
- Remover `ml_sizing` (RiskEngine `_check_position` já calcula size)
- Manter: piramidação (é gestão de posição, não risco), suavização, streak

### Passo 3: RiskManager vira thin wrapper
- `pode_abrir()` delega para `RiskEngine.avaliar()`
- `registrar_resultado()` delega para `RiskEngine.registrar_resultado()`
- Manter: `calcular_barreiras_dinamicas()`, `custo_execucao()`, `validar_sanidade_alvos()` (únicos)

### Passo 4: Estado unificado no RiskEngine
- `pnl_dia`, `trades_dia`, `perdas_consecutivas`, `circuit_breaker_nivel` vivem no RiskEngine
- `app.py` lê de `self.risk_engine.get_estado()` em vez de `self.risk.*`
- `registrar_resultado()` chamado uma vez (no RiskEngine), não duas vezes

## 5. Riscos da Migração

| Risco | Mitigação |
|-------|-----------|
| RiskManager.pode_abrir tem lógica que RiskEngine não tem (sanidade, slippage) | Mover sanidade e slippage para RiskEngine ou chamá-los antes de `avaliar()` |
| PositionManager.risk é referenciado em outros arquivos | Manter `self.risk` como propriedade que delega ao RiskEngine |
| Dashboard lê de `self.risk.pnl_dia` | Atualizar para `self.risk_engine.get_estado()` |
| Testes existentes usam RiskManager | Criar testes novos para RiskEngine e manter compat |

## 6. BUG 4 — Fórmula de Exposição Errada (Não Corrigida em Produção)

### Problema
A fórmula de exposição no `risk_engine.py` (linha 509) está **errada** e **nunca foi corrigida**:

```python
# core/risk_engine.py, linha 509
nova_exposure = self.exposure_atual + signal.tp + signal.sl
```

Esta fórmula mede a **faixa de resultado** (TP + SL em pontos), não o **tamanho da posição**.

### Fórmula Correta (Documentada mas Não Implementada)
A documentação (`docs/FORMULAS.md`) e os testes (`tests/test_formulas.py`) descrevem a fórmula correta:

```
E = N · P · V
```

Onde:
- `N` = Quantidade de contratos
- `P` = Preço de entrada
- `V` = Valor do ponto em moeda

### Status de Implementação

**Opção (b) — Sistema Paralelo Não Integrado (DECISÃO ATUAL)**

A FASE 7 implementou:
1. Módulo `exposure` (não existente no filesystem — apenas nos testes)
2. Documentação completa em `docs/FORMULAS.md`
3. Testes matemáticos em `tests/test_formulas.py`, `tests/test_aggregate.py`, etc.

Porém:
- O módulo `exposure` **não foi criado** no projeto
- O `risk_engine.py` **continua usando a fórmula errada**
- Os 205 testes novos validam uma implementação paralela que **nunca foi conectada ao motor ao vivo**

### Por que não conectar agora (opção a)?

Para corrigir o bug conectando o RiskEngine à fórmula correta, seria necessário:

1. **Criar o módulo `exposure`** com as classes `Position`, `Direction`, e funções `nominal_exposure()`, `risk_at_stop()`, etc.
2. **Adicionar campos ao Signal**: `quantidade` (N), `preco_ref` (P já existe), `valor_ponto` (V)
3. **Alterar o cálculo de exposure** no RiskEngine para usar `nominal_exposure(Position(...))` em vez de `signal.tp + signal.sl`
4. **Atualizar todos os chamadores** que criam Signals para passar os novos campos
5. **Converter `max_exposure_pts`** de pontos para valor em moeda (ou criar um novo limite `max_exposure_moeda`)

Esta é uma mudança de arquitetura significativa que afetaria:
- `core/signal_engine.py` (criação de signals)
- `core/position_manager.py` (gestão de posições)
- `adapters/rtd_writer.py` (escrita de book)
- Testes que criam signals manualmente

### Correção Implementada (v10.4)

**Status: BUG CORRIDO**

A correção foi implementada em 30/08/2026:

1. **Signal expandido** (`core/contracts.py`):
   - Adicionado campo `quantidade: int = 1` (N)
   - Adicionado campo `valor_ponto: float = 0.20` (V)

2. **RiskEngine atualizado** (`core/risk_engine.py`):
   - Novo método `_calcular_exposure_nominal()` que calcula E = N * P * V
   - `_check_exposure()` agora usa a fórmula correta
   - Mensagem de detail mostra os valores de N, P, V para debugging

3. **SignalEngine atualizado** (`core/signal_engine.py`):
   - Novo método `_get_valor_ponto()` que retorna o valor do ponto por ativo
   - Criação de Signal agora passa `quantidade=1` e `valor_ponto` correto

### Valores Padrão por Ativo

| Ativo | Valor do Ponto |
|-------|----------------|
| WIN/IND | R$ 0,20 |
| WDO/DOL | R$ 0,10 |

### Limites

- `max_exposure_pts` permanece como limite em **pontos de preço** (1000 pts default)
- O cálculo agora mede exposição nominal correta: `exposure = quantidade * preco_ref * valor_ponto`
- Para WIN: 1 contrato a 150.000 pts = 30.000 pts de exposure (1 * 150.000 * 0.20)

### Testes

Os 205 testes em `tests/test_formulas.py` continuam validando a lógica matemática correta. O módulo `exposure` ainda não foi criado como pacote separado, mas a funcionalidade está implementada diretamente no RiskEngine.
