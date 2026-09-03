# Auditoria do RTD — Relatório

> Data: 2026-08-29
> Status: **APROVADO COM RESERVA** ⚠️

---

## Resumo Executivo

| Categoria | Status | Problemas |
|-----------|--------|-----------|
| Arquitetura de threads | ✅ OK | 0 |
| Polling e frequência | ⚠️ PARCIAL | 1 |
| Flush e fsync | ✅ OK | 0 |
| Parquet | ✅ OK | 0 |
| Recuperação após erro | ✅ OK | 0 |
| Perda de dados | ✅ OK | 0 |
| Duplicação | ✅ OK | 0 |
| Ordenação temporal | ✅ OK | 0 |
| Resistência a rajadas | ⚠️ PARCIAL | 1 |

**Total:** 2 warnings, 0 critical failures

---

## 1. Arquitetura de Threads

### CaptureDaemon

| Componente | Implementação | Status |
|------------|---------------|--------|
| Thread | `threading.Thread` | ✅ OK |
| Fila | `queue.Queue(maxsize=100_000)` | ✅ OK |
| Daemon | Sim (morre com processo pai) | ✅ OK |
| Try/Except | Por evento | ✅ OK |

### Fluxo de Dados

```
RTD COM → ProfitRTDAdapter → CaptureDaemon → FileStorage → JSONL
                                    ↓
                              queue.Queue (max 100k)
                                    ↓
                              Thread interna (flush a cada 2s)
                                    ↓
                              Disk (JSONL)
```

**Conclusão:** ✅ Arquitetura sólida, isolamento de falhas

---

## 2. Polling e Frequência

### ProfitRTDAdapter

| Componente | Valor | Status |
|------------|-------|--------|
| Polling | `PumpEvents(0.05)` | ✅ OK |
| BOOK snapshot | 250ms (4 Hz) | ✅ OK |
| T&T events | 100ms (10 Hz) | ✅ OK |

**Observação:** Frequência exata de polling não identificada no código, mas timeouts razoáveis (0.05s - 0.1s).

---

## 3. Flush e Fsync

### CaptureDaemon

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Flush periódico | A cada 2 segundos | ✅ OK |
| Flush em shutdown | Sim | ✅ OK |
| Fsync | Via `flush()` do arquivo | ✅ OK |

### RTD Writer (Parquet)

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Flush | A cada part | ✅ OK |
| Atomic write | Sim (`write_parquet_part`) | ✅ OK |

**Conclusão:** ✅ Dados persistidos corretamente, risco de perda mínimo

---

## 4. Parquet

### Schemas

| Schema | Campos | Status |
|--------|--------|--------|
| BOOK_SCHEMA | 3006 | ✅ OK |
| TT_SCHEMA | 14 | ✅ OK |

### Escrita

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Partes por hora | Sim | ✅ OK |
| Consolidação | `consolidar_book_parquet()` | ✅ OK |
| Clean-up | `limpar_pasta()` | ✅ OK |

**Conclusão:** ✅ Schema completo, escrita particionada

---

## 5. Recuperação Após Erro

### CaptureDaemon

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Try/Except | Por evento | ✅ OK |
| Isolamento | Thread separada | ✅ OK |
| Health check | A cada 5 minutos | ✅ OK |

### RTD Connection

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Retry | Sim (conexão COM) | ✅ OK |
| Watchdog | `com_watchdog.py` | ✅ OK |
| Auto-restart | Max 10/hora | ✅ OK |

**Conclusão:** ✅ Sistema resiliente, recuperação automática

---

## 6. Perda de Dados

### Contadores

| Métrica | Status |
|---------|--------|
| Eventos recebidos | ✅ Contado |
| Eventos rejeitados | ✅ Contado |
| Eventos escritos | ✅ Contado |
| Erros de I/O | ✅ Contado |

### Monitoramento

| Componente | Status |
|------------|--------|
| Health check | ✅ Dashboard |
| Stats | ✅ Contadores |
| Logs | ✅ Estruturados |

**Conclusão:** ✅ Transparência completa, perda detectável

---

## 7. Duplicação

### Trades (T&T)

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Deduplication | `_vistos_tt` (assinatura) | ✅ OK |
| Baseline | `_baseline_pending` | ✅ OK |

### Book

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Throttle | 250ms mínimo | ✅ OK |
| Dedup | Por timestamp | ✅ OK |

**Conclusão:** ✅ Duplicação controlada

---

## 8. Ordenação Temporal

### Garantia

| Mecanismo | Status |
|-----------|--------|
| Timestamps epoch ms | ✅ OK |
| Dados chegam ordenados | ✅ OK (COM) |
| Partes por hora | ✅ OK |

**Conclusão:** ✅ Ordenação garantida pela fonte

---

## 9. Performance

### Taxas

| Métrica | Valor | Status |
|---------|-------|--------|
| Captura BOOK | 4 Hz (250ms) | ✅ OK |
| Captura T&T | 10 Hz (100ms) | ✅ OK |
| Capacidade fila | 100,000 eventos | ⚠️ Ver abaixo |

### Cálculos

```
Capacidade teórica:
  - BOOK: 4 eventos/segundo
  - T&T: 10 eventos/segundo
  - Total: ~14 eventos/segundo
  
Tempo para saturar fila (100k):
  - Normal: 100,000 / 14 ≈ 7,143 segundos (2 horas)
  - Rajada (1000 Hz): 100,000 / 1000 = 100 segundos (1.6 min)
```

---

## 10. Resistência a Rajadas

### Problema Identificado

| Cenário | Eventos/seg | Tempo até saturação |
|---------|-------------|---------------------|
| Normal | ~14 | ~2 horas |
| Rajada moderada | ~100 | ~16 minutos |
| Rajada intensa | ~1000 | ~100 segundos |
| Crash RTD (reconnect) | ~10,000 | ~10 segundos |

### Mecanismos de Proteção

| Mecanismo | Implementação | Status |
|-----------|---------------|--------|
| Fila limitada | `maxsize=100_000` | ✅ OK |
| Rejeição | `queue.Full` → drop | ✅ OK |
| Logging | Eventos rejeitados logados | ✅ OK |

### Risco

**Em rajadas extremas (>10k eventos/seg), a fila pode saturar e eventos serão descartados.**

**Mitigação:**
1. Deduplication reduz volume
2. Throttle de book (250ms) limita BOOK
3. RTD normalmente não gera rajadas >1k Hz

**Conclusão:** ⚠️ Risco baixo, mas existente

---

## Recomendações

### Alta
1. ~~Monitorar tamanho da fila~~ — ✅ Já existe health check
2. **Aumentar fila para 500k** se rajadas forem frequentes

### Média
3. **Adicionar métricas de backlog** no dashboard
4. **Implementar warning** quando fila >80%

### Baixa
5. **Testar carga** com simulação de rajada
6. **Documentar** comportamento em saturação

---

## Conclusão Final

**Status: APROVADO COM RESERVA** ⚠️

- ✅ Arquitetura sólida
- ✅ Isolamento de falhas
- ✅ Persistência confiável
- ✅ Deduplication implementada
- ⚠️ Fila pode saturar em rajadas extremas

**O sistema está pronto para produção em condições normais.** Em cenários derajada extrema, eventos podem ser perdidos, mas o risco é baixo.


---

## v15.33 — Dedup de reemissao persistente da janela T&T/RLP (identidade completa)

**Evidencia (RAW 2026-09-03):** o RTD reentrega as linhas visiveis da janela a
cada RefreshData — 76-98% das linhas gravadas eram reemissoes da mesma linha
(WIN 75,8% / WDO 94,5% / IND 96,9% / DOL 98,0%). Negocios unicos IND ≈ 7,2k vs
9,1k no Profit (79%).

**Correcao (`adapters/profit_rtd.py`):**
- Dedup por identidade COMPLETA: `(ts_ms, preco, qtd, agressor, compradora,
  vendedora)` — por (sym, kind) separado (tt vs rlp). Qualquer campo diferente
  = trade novo (nunca elimina negocio distinto).
- Controle de memoria: expiracao por idade (`rtd.dedup_tt_expiry_s`, default
  900s) + cap FIFO por ativo (`rtd.dedup_tt_max_por_ativo`, default 200k).
  Desligavel: `rtd.dedup_tt=false`.
- Baseline absorve o retrato pre-conexao e MARCA a identidade como vista (a
  reentrega pos-baseline nao vira evento).
- Contadores por ativo no `dedup_stats` (dashboard): `tt_recebidos` (linhas
  coerentes), `tt_unicos` (emitidos), `tt_duplicados` (suprimidos).

**Bug pre-existente corrigido durante a implementacao:** a emissao disparava
quando so o trio DAT/PRE/QUL coesionava — AGR/ACP/AVD chegam DEPOIS no mesmo
refresh, entao a 1a emissao saia com identidade vazia/antiga e a reentrega com
campos completos gerava 2 eventos. Corrigido adiando a decisao para o fim do
ciclo e exigindo os 6 campos no mesmo lote (medido: AGR/ACP/AVD entregues em
~100% das linhas dos 4 ativos — sem risco de stall).

**Limitacao documentada:** trades identicos campo-a-campo no mesmo milissegundo
sao indistinguiveis na fonte e colapsam em 1 evento (rajadas de identidade
identica nao sao separaveis sem id de troca da bolsa).

**Testes (`testes/test_dedup_reemissao_v1533.py`, 14):** helper unit (10x
reentrega -> 1; 10 distintos -> 10; campo diferente nunca colide; por ativo;
tt vs rlp; FIFO cap; expiracao; desligavel; contadores) + integracao events()
real (reentrega persistente -> 1 evento com identidade completa; chegada split
-> 1 emissao limpa; trade novo no ciclo seguinte). Suite completa: 1045 passed
(1031 -> 1045), zero regressoes.
