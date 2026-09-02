# Política Temporal — Sistema de Trading AlphaZ

**Data:** 30/08/2026
**Fases aplicadas:** Fase 1 (Dedup T&T), Fase 2 (Timestamp do Mercado), Fase 3 (Ordenamento Temporal), Fase 4 (Overflow e Perda de Eventos)
**Arquivos criados:** `core/temporal.py`, `core/event_ordering.py`
**Arquivos alterados:** `core/contracts.py`, `adapters/profit_rtd.py`, `adapters/replay.py`, `core/app.py`, `core/capture_daemon.py`

---

## 1. CONTRATO TEMPORAL TRIPLO

O sistema preserva três timestamps distintos para cada evento de mercado. **Nenhum deles pode substituir outro.**

| Timestamp | Campo | Origem | Unidade | Descrição |
|-----------|-------|--------|---------|-----------|
| **event_ts_ms** | `TradeEvent.timestamp_ms` | `DAT` do Profit (`HH:MM:SS.mmm` → epoch ms) | epoch ms | Momento em que o trade ocorreu no mercado. Nunca wall clock. |
| **receive_ts_ns** | `TradeEvent.received_at_ns` | `time.time_ns()` no Python | epoch ns | Momento em que o processo Python recebeu/processou o evento. |
| **sequence_id** | `TradeEvent.sequence_id` | Contador monotônico global thread-safe | int crescente | Orem determinística local. Garante ordenação mesmo se timestamps colidem. |

### Regra absoluta

```
NUNCA: event_ts_ms = receive_ts_ns
NUNCA: event_ts_ms = int(time.time() * 1000)
SEMPRE: event_ts_ms = dat_to_epoch_ms(dat_str)
```

Se o `DAT` for inválido (vazio, malformado), o sistema usa `receive_ts` como fallback **com warning explícito no log**. O fallback é documentado e auditável.

### Exemplo

```
Evento Profit: 10:35:21.127
Recebido pelo Python às: 10:35:21.481

Resultado:
  event_ts_ms   = epoch correspondente a 10:35:21.127  ← do DAT
  received_at_ns = epoch correspondente a 10:35:21.481  ← do Python
  sequence_id    = 42                                   ← contador global

NUNCA:
  event_ts_ms = 10:35:21.481  ← PROIBIDO (wall clock)
```

---

## 2. TIMEZONE

- **Timezone oficial:** `America/Sao_Paulo` (UTC-3)
- **DST:** O Brasil não tem DST desde 2019, mas o `ZoneInfo` lida corretamente se reativado.
- **Conversão DAT → epoch:** `dat_to_epoch_ms(dat_str)` usa a data de hoje no timezone BR como referência. Se o `DAT` for apenas `HH:MM:SS.mmm` (sem data), a data é inferida do dia corrente.

### Tratamento de milissegundos

O `DAT` do Profit vem como `HH:MM:SS.mmm` (3 dígitos de milissegundos). O parser:

1. Faz `partition(".")` para separar hora de milissegundos.
2. Se não houver milissegundos, assume `ms=0`.
3. Se houver mais de 3 dígitos, trunca para 3 (microssegundos → milissegundos).

### Virada de dia

Se o `DAT` for `23:59:59.000` e o próximo for `00:00:01.000`, o sistema trata como virada de dia automaticamente (a data de referência avança). Testado em `test_virada_de_dia_nao_causa_problema`.

---

## 3. VALIDAÇÃO DE TIMESTAMP

Antes de emitir um `TradeEvent`, o adapter valida o timestamp via `validate_event_ts()`:

| Regra | Limiar | Ação |
|-------|--------|------|
| `event_ts_ms == 0` | — | REJEITAR (`timestamp_zero`) |
| `event_ts_ms > receive + 30s` | 30s futuro | REJEITAR (`timestamp_futuro`) |
| `event_ts_ms < receive - 300s` | 5min passado | REJEITAR (`timestamp_passado`) |
| Caso contrário | — | ACEITAR |

**Motivo:** timestamps no futuro indicam clock drift ou dados corrompidos. Timestamps muito no passado indicam replay ou dados antigos sendo reprocessados.

> **v14.8:** o limite de passado (300s) é o MESMO no adapter e no
> `FileStorage`. Antes havia divergência (adapter 600s, storage 300s) —
> eventos entre 300-600s passavam no adapter e eram descartados
> silenciosamente na gravação. A captura é sempre tempo real; o baseline
> já absorve o 1º ciclo do RTD.

---

## 4. DETECÇÃO DE ANOMALIAS TEMPORAIS (Fase 3)

O `EventOrderingDetector` (`core/event_ordering.py`) classifica cada evento em 5 categorias de anomalia temporal:

### 4.1 Evento Atrasado (`is_late`)

| Condição | `lag_ms = receive_ts - event_ts > 500ms` |
|----------|------------------------------------------|
| **Causa típica** | Latência de rede, COM lento, RefreshData demorado |
| **Ação** | ACCEPT (log debug) |
| **Métrica** | `events_late`, `max_event_lag_ms` |

### 4.2 Evento Fora de Ordem (`is_out_of_order`)

| Condição | `event_ts_ms < último event_ts_ms do mesmo ativo` |
|----------|---------------------------------------------------|
| **Causa típica** | RTD entrega linhas T&T fora de ordem em refreshes diferentes |
| **Ação** | ACCEPT (log warning) — não descartar |
| **Métrica** | `events_out_of_order` |

### 4.3 Timestamp Duplicado (`is_duplicate`)

| Condição | `event_ts_ms já visto para o mesmo ativo` |
|----------|------------------------------------------|
| **Causa típica** | Mesmo trade reenviado pelo RTD em refreshes subsequentes |
| **Ação** | **REJECT** — não reprocessar |
| **Métrica** | `events_duplicate` |

### 4.4 Salto Temporal Anormal (`is_forward_jump`)

| Condição | `gap_ms = event_ts - último > 60_000ms (60s)` |
|----------|----------------------------------------------|
| **Causa típica** | Gap de mercado (almoço, leilão), parada de RTD, mudança de contrato |
| **Ação** | LOG_ONLY (registrado, não descartado) |
| **Métrica** | `events_forward_jump` |

### 4.5 Sequência Regressiva (`is_backward_sequence`)

| Condição | `3+ eventos seguidos com timestamp < anterior (mesmo ativo)` |
|----------|-------------------------------------------------------------|
| **Causa típica** | RTD reordenando linhas, buffer circulaire, replay com dados antigos |
| **Ação** | LOG_ONLY (registrado, não descartado) |
| **Métrica** | `events_backward_sequence` |

Se um evento em ordem chega, o contador de regressivos é resetado.

---

## 5. POLÍTICA DE DESCARTE/REORDENAÇÃO

### Princípio

> **NÃO descartar eventos fora de ordem automaticamente.**
> Primeiro registrar, classificar e medir.
> O consumidor decide o que fazer com a classificação.

### Tabela de decisões

| Anomalia | `action` | Descarta? | Motivo |
|----------|----------|-----------|--------|
| Timestamp inválido (zero) | `REJECT` | Sim | Dado corrompido |
| Timestamp no futuro (>30s) | `REJECT` | Sim | Clock drift/corrupção |
| Timestamp no passado (>5min) | `REJECT` | Sim | Replay/dados antigos |
| Duplicado (mesmo ts, mesmo ativo) | `REJECT` | Sim | Já processado |
| Fora de ordem isolado | `ACCEPT` | Não | RTD pode entregar fora de ordem |
| Atrasado (lag >500ms) | `ACCEPT` | Não | Latência é esperada |
| Salto temporal (>60s gap) | `LOG_ONLY` | Não | Pode ser gap legítimo de mercado |
| Sequência regressiva (3+) | `LOG_ONLY` | Não | Investigar causa, não descartar |
| Evento normal | `ACCEPT` | Não | — |

### Reordenação

O sistema **não reordena eventos**. Eventos fora de ordem são processados na ordem em que chegam. A classificação (`is_out_of_order`, `is_backward_sequence`) permite que o consumidor decida se quer aplicar lógica de reordenação ou buffering.

---

## 6. MÉTRICAS

### Métricas do detector de ordenamento

```python
{
    'events_total': int,              # total de eventos processados
    'events_accepted': int,          # eventos aceitos (inclui LOG_ONLY)
    'events_out_of_order': int,      # eventos com timestamp < anterior
    'events_duplicate': int,         # timestamps duplicados rejeitados
    'events_timestamp_invalid': int, # timestamps zero/negativo
    'events_late': int,              # eventos com lag > 500ms
    'max_event_lag_ms': int,         # maior lag observado
    'events_forward_jump': int,      # saltos temporais > 60s
    'events_backward_sequence': int, # sequências regressivas (3+)
}
```

### Acesso

- **Adapter:** `adapter._ordering_detector.get_stats_for_dashboard()`
- **App:** `app.get_ordering_stats()` → exposto no dashboard
- **Log:** warnings para `out_of_order`, `forward_jump`, `backward_sequence`

---

## 7. DEDUPLICAÇÃO DE T&T (Fase 1)

### Assinatura determinística

Cada trade é identificado por uma assinatura de 7 campos:

```python
sig = (DAT, ACP, PRE, QUL, AVD, AGR, AGAG)
```

| Campo | Significado |
|-------|-------------|
| `DAT` | Timestamp do trade (`HH:MM:SS.mmm`) |
| `ACP` | Corretora compradora |
| `PRE` | Preço |
| `QUL` | Quantidade |
| `AVD` | Corretora vendedora |
| `AGR` | Agressor (`Comprador` / `Vendedor`) |
| `AGAG` | Agressor agregado (direto vs carteira) |

### Por que AGAG?

Dois trades podem ter o mesmo `DAT + ACP + PRE + QUL + AVD + AGR` mas `AGAG` diferente. O Profit classifica esses como operações distintas (ex: direto vs carteira). Sem `AGAG`, trades distintos seriam colapsados.

### Política de expiração (LRU)

- Estrutura: `OrderedDict[ativo] → OrderedDict[signature → True]`
- Limite: 50.000 assinaturas por ativo
- Eviction: LRU (`popitem(last=False)` remove o mais antigo)
- Trades evictados podem ser reemitidos se reenviados pelo RTD (aceitável — o RTD não reenvia trades de horas atrás)

### Baseline (primeiro refresh)

O primeiro `RefreshData` absorve todos os trades visíveis como baseline, sem emitir eventos. Isso evita emitir histórico acumulado na primeira chamada. Após o primeiro refresh, o baseline é desativado para todos os ativos.

---

## 8. TESTES

### Fase 1 — Dedup T&T (17 testes)

`testes/test_dedup_tt.py`

| Teste | Cenário |
|-------|---------|
| `test_mesmo_trade_1x_gera_1_evento` | 1 trade → 1 evento |
| `test_mesmo_trade_10x_gera_1_evento` | 10 repetições → 1 evento |
| `test_10_trades_distintos_geram_10_eventos` | 10 distintos → 10 eventos |
| `test_trade_repetido_entre_distintos` | Intercalação de novos e repetidos |
| `test_trades_iguais_exceto_timestamp_sao_distintos` | DAT diferente → distintos |
| `test_mesmo_dat_mesmo_trade` | Mesmo DAT → mesma operação |
| `test_trades_iguais_exceto_agag_nao_eliminados` | AGAG diferente → distintos |
| `test_agag_vazio_vs_preenchido` | AGAG '' vs preenchido → distintos |
| `test_dedup_independente_por_ativo` | WIN e WDO independentes |
| `test_repeticao_em_um_ativo_nao_afeta_outro` | 10x WIN não bloqueia WDO |
| `test_reset_dedup_permite_reemissao` | Reset permite reemissão |
| `test_lru_eviction_mantem_limite` | Limite de memória respeitado |
| `test_lru_eviction_nao_bloqueia_trades_novos` | Eviction não bloqueia novos |
| `test_lru_eviction_descarta_mais_antigo` | LRU remove o mais antigo |
| `test_baseline_descarta_primeiro_refresh` | Baseline absorve sem emitir |
| `test_apos_baseline_segundo_refresh_emite` | Pós-baseline emite novos |
| `test_baseline_independente_por_ativo` | Baseline por ativo |

### Fase 2 — Timestamp do Mercado (26 testes)

`testes/test_temporal.py`

| Teste | Cenário |
|-------|---------|
| `test_event_ts_preserva_milissegundo_do_profit` | 10:35:21.127 → event_ts tem .127 |
| `test_receive_ts_diferente_de_event_ts` | event_ts ≠ receive_ts |
| `test_event_ts_nao_e_wall_clock` | 09:00:00 não vira 10:35:21 |
| `test_dois_trades_mesmo_segundo_ms_diferente` | .127 e .250 distintos |
| `test_dez_trades_com_timestamps_crescentes` | 10 trades crescentes |
| `test_monotonicidade_detecta_volta_no_tempo` | Voltou 10s → detectado |
| `test_monotonicidade_aceita_mesmo_timestamp` | Mesmo ts → aceito |
| `test_monotonicidade_independente_por_ativo` | WIN e WDO independentes |
| `test_virada_de_segundo` | 21.999 → 22.001 |
| `test_virada_de_minuto` | 35:59.999 → 36:00.001 |
| `test_virada_de_dia` | 23:59:58 → 23:59:59 |
| `test_dat_vazio_retorna_zero` | DAT vazio → 0 |
| `test_validacao_rejeita_timestamp_zero` | ts=0 rejeitado |
| `test_validacao_rejeita_timestamp_futuro` | +60s rejeitado |
| `test_validacao_rejeita_timestamp_passado` | -10min rejeitado |
| `test_sequence_id_e_incremental` | s1 < s2 < s3 |
| `test_tradeevent_tem_tres_timestamps` | 3 campos presentes |

### Fase 3 — Ordenamento Temporal (23 testes)

`testes/test_event_ordering.py`

| Teste | Cenário |
|-------|---------|
| `test_evento_atrasado_detectado` | lag > 500ms → `is_late=True` |
| `test_evento_sem_atraso_nao_classificado` | lag < 500ms → `is_late=False` |
| `test_max_event_lag_ms_atualizado` | max lag reflete o maior observado |
| `test_fora_de_ordem_detectado` | ts < anterior → `is_out_of_order=True` |
| `test_fora_de_ordem_isolado_e_aceito` | fora de ordem isolado → ACCEPT |
| `test_duplicado_detectado_e_rejeitado` | mesmo ts → REJECT |
| `test_mesmo_ts_em_ativos_diferentes_nao_e_duplicado` | WIN vs WDO → não duplicado |
| `test_salto_frente_detectado` | gap > 60s → `is_forward_jump=True` |
| `test_gap_normal_nao_e_salto` | gap < 60s → não é salto |
| `test_sequencia_regressiva_detectada` | 3+ no passado → `is_backward_sequence=True` |
| `test_sequencia_interrompida_por_evento_normal` | evento em ordem reseta contador |
| `test_duplicada_rejeitada` | duplicata → REJECT |
| `test_fora_de_ordem_aceito` | fora de ordem → ACCEPT |
| `test_sequencia_regressiva_log_only` | sequência → LOG_ONLY |
| `test_salto_temporal_log_only` | salto → LOG_ONLY |
| `test_timestamp_invalido_rejeitado` | ts=0 → REJECT |
| `test_reset_limpa_estado` | reset zera métricas |
| `test_reset_permite_reprocessar_duplicatas` | reset permite reemissão |

**Total: 85 testes temporais** (17 + 26 + 23 + 19)

---

## 9. OVERFLOW E PERDA DE EVENTOS (Fase 4)

### Problema

O `CaptureDaemon` (`core/capture_daemon.py`) usava `put_nowait` para enfileirar eventos. Quando a fila interna (`_MAX_QUEUE = 100.000`) enchia, eventos eram **descartados silenciosamente**:

- `fila_max_atingido` contava **vezes** que a fila encheu, não **quantos eventos** foram perdidos.
- O log usava `log.warning` (perda de dados tratada como warning, não error).
- Não havia `data_loss_detected` no `health_check` — o watchdog não conseguia detectar a perda.
- Não havia watermark (pico da fila) nem backlog (latência de gravação).

### Correção

O `CaptureDaemon` foi reescrito com:

#### 9.1 Contadores detalhados (20 campos)

| Contador | Descrição |
|----------|-----------|
| `events_received` | Total de eventos recebidos (negócios + book) |
| `events_processed` | Total de eventos gravados com sucesso |
| `events_dropped` | Total de eventos DESCARTADOS por overflow |
| `events_error` | Total de eventos com erro de gravação |
| `negocios_recebidos` / `negocios_processados` / `negocios_dropped` / `negocios_erro` | Por tipo (negócios) |
| `book_recebidos` / `book_processados` / `book_dropped` / `book_erro` | Por tipo (book) |
| `overflow_count` | Número de vezes que a fila encheu |
| `watermark_max` | Maior tamanho da fila observado (pico) |
| `watermark_current` | Tamanho atual da fila |
| `backlog_ms_max` | Maior latência fila → disco (ms) |
| `flushes` | Número de flushes para disco |
| `erros_flush` | Número de erros de flush |
| `daemon_crashes` | Número de crashes do loop do daemon |
| `data_loss_detected` | **True** se qualquer evento foi descartado |

#### 9.2 Política de descarte explícita (drop-on-overflow)

Quando a fila está cheia (`_MAX_QUEUE`), novos eventos são **DESCARTADOS**.
Isso é **explícito e auditável**:

- `log.error` (não `log.warning`) — perda de dados é erro, não warning.
- `events_dropped` incrementado pelo número exato de eventos perdidos.
- `data_loss_detected = True` no `health_check`.
- `last_drop_reason` e `last_drop_ts` registram o motivo e timestamp do último descarte.

#### 9.3 Backpressure mode (opcional)

Se `backpressure=True` no construtor, a thread produtora é **bloqueada** (`put` com timeout de 5s) em vez de descartar. Isso pode causar latência no loop de trading, mas garante que nenhum evento seja perdido.

- **Default:** `backpressure=False` (drop-on-overflow) — prioriza o trading.
- **Captura histórica:** `backpressure=True` — prioriza integridade dos dados.

#### 9.4 Watermark

O watermark (`watermark_max`) registra o pico da fila — o maior número de eventos que estiveram aguardando gravação em algum momento. É atualizado:

- No loop do daemon (a cada iteração).
- No `health_check` (quando o dashboard/watchdog consulta).

O watermark **só cresce, nunca diminui** — permite identificar se a fila chegou perto do limite mesmo que já tenha esvaziado.

#### 9.5 Backlog

O backlog (`backlog_ms_max`) mede a latência entre o momento em que um evento é consumido da fila e o momento em que a gravação é concluída. É atualizado a cada evento processado.

#### 9.6 health_check para o watchdog

O `health_check()` retorna:

```python
{
    'alive': bool,              # thread do daemon está viva?
    'queue_size': int,          # tamanho atual da fila
    'queue_pct': float,        # % da fila usada
    'watermark_max': int,      # pico histórico da fila
    'data_loss_detected': bool, # True se eventos foram descartados
    'started': bool,
    'shutdown': bool,
    'stats': dict,              # todos os 20 contadores
}
```

O watchdog (ou dashboard) pode verificar `data_loss_detected` e `queue_pct > 80` para identificar condições de overflow.

### Testes (19 testes)

`testes/test_capture_overflow.py`

| Teste | Cenário |
|-------|---------|
| `test_fila_cheia_descarta_e_registra` | Fila cheia → descarta + `data_loss_detected=True` |
| `test_motivo_do_descarte_registrado` | `last_drop_reason` contém motivo |
| `test_contador_recebidos_vs_processados` | `received > processed` quando há descarte |
| `test_overflow_detectado_com_produtor_rapido` | 1000 eventos em fila de 5 → overflow |
| `test_book_snapshot_descartado_com_fila_cheia` | Book descartado com fila cheia |
| `test_estado_nao_corrompido_apos_overflow` | Daemon continua funcionando após overflow |
| `test_data_loss_detected_no_health_check` | `health_check` reporta perda |
| `test_log_error_nao_warning` | Overflow gera `log.error` (não warning) |
| `test_contadores_somam_corretamente` | `received = processed + dropped + error` |
| `test_watermark_max_registrado` | Pico da fila registrado |
| `test_backlog_ms_registrado` | Latência de gravação medida |
| `test_daemon_sobrevive_a_overflow` | Daemon não morre após overflow |
| `test_daemon_sobrevive_a_erro_de_gravacao` | Daemon não morre se I/O falha |
| `test_watermark_atualiza_no_health_check` | `health_check` atualiza watermark |
| `test_watermark_nao_diminui` | Watermark só cresce |
| `test_backpressure_nao_descarta` | `backpressure=True` não descarta |
| `test_drain_grava_eventos_restantes` | Drain no shutdown grava tudo |
| `test_drain_com_fila_parcial` | Drain com fila parcialmente processada |
| `test_health_check_para_watchdog` | Campos que o watchdog pode verificar |

---

## 10. GRAVAÇÃO SEPARADA POR ATIVO (v13.2)

### Arquitetura (01/09/2026)

Cada tipo de dado gera **um arquivo JSONL por ativo**, eliminando contaminação cruzada:

```
raw_negocios_ms_{session}_WINV26.jsonl    ← só WIN
raw_negocios_ms_{session}_INDV26.jsonl    ← só IND
raw_negocios_ms_{session}_WDOV26.jsonl    ← só WDO
raw_negocios_ms_{session}_DOLV26.jsonl    ← só DOL
raw_book_ms_{session}_WINV26.jsonl        ← só WIN
raw_book_ms_{session}_INDV26.jsonl        ← só IND
raw_book_ms_{session}_WDOV26.jsonl        ← só WDO
raw_book_ms_{session}_DOLV26.jsonl        ← só DOL
raw_rlp_ms_{session}_WINV26.jsonl         ← só WIN
raw_rlp_ms_{session}_WDOV26.jsonl         ← só WDO
```

### Regras de Gravação

1. **Zero contaminação:** cada arquivo contém apenas registros do ativo correspondente
2. **Flush por ativo:** buffer e flush são independentes por ativo
3. **Rotação por ativo:** `max_bytes_por_arquivo` é verificado por ativo individual
4. **Compatibilidade:** o `converter_brutos_parquet.py` aceita tanto arquivos misturados (antigos) quanto separados (novos)

---

## 11. DADOS HISTÓRICOS

### Migração

Os dados históricos existentes (`raw_negocios_ms_*.jsonl`) **não foram alterados**. Eles contêm `ts_ms` que era wall clock (antes da Fase 2). A Fase 2 só afeta novos dados capturados a partir de agora.

Para usar dados antigos no replay, o `replay_engine.py` lê `ts_ms` do JSONL e usa como `event_ts_ms`. O `adapters/replay.py` converte para o schema 2.0 (`received_at_ns = ts_ms * 1_000_000`).

### Compatibilidade

- `TradeEvent.received_at_ms` (property) retorna `received_at_ns // 1_000_000` para compatibilidade com código que esperava ms.
- `BookSnapshot` agora tem `received_at_ns` (era `received_at`). O campo `received_at` foi removido.
- `schema_version` dos contratos subiu de `"1.0"` para `"2.0"`.
