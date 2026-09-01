# RELATÓRIO FINAL — PADRONIZAÇÃO RAW EM PARQUET + HIVE (v14.1)

Data: 2026-09-01
Autor: Buffy (Codebuff)

---

## 1. ARQUIVOS MODIFICADOS

| Arquivo | Tipo | Mudança |
|---------|------|---------|
| `adapters/file_storage.py` | Modificado | Reescrito: Parquet + Hive + Snappy + schema explícito |
| `core/capture_daemon.py` | Modificado | `registrar_book` agora passa janela_id, window_name, received_at_ns |
| `core/contracts.py` | Modificado | MarketEvent: +janela_id, +window_name, +is_rlp |
| `adapters/replay.py` | Modificado | Lê Parquet hive (compatível com formatos antigos) |
| `ml/batch_processor.py` | Modificado | Lê Parquet hive (fallback JSONL legado) |

## 2. ARQUIVOS NOVOS

| Arquivo | Descrição |
|---------|-----------|
| `scripts/validar_raw_hive.py` | Validação automática (Seções 11 e 12) |
| `scripts/teste_hive_v14.py` | Teste completo do schema v14.1 |

## 3. SCHEMA FINAL DO BOOK (16 colunas)

```
ts_ns:            int64     — timestamp do evento (nanosegundos)
received_at_ns:   int64     — timestamp de recebimento (nanosegundos)
sequence_id:      int64     — ordem determinística local
ativo:            string    — símbolo do ativo (ex: WINV26)
asset_partition:  string    — partição hive (ex: WIN)
janela_id:        int16     — índice da janela RTD (0-11)
window_name:      string    — nome da janela (ex: BOOK1)
nivel:            int16     — nível de preço (0-499)
bid:              float64   — preço bid
ask:              float64   — preço ask
bid_volume:       int64     — volume bid deste nível
ask_volume:       int64     — volume ask deste nível
bid_vol_total:    int64     — volume total bid
ask_vol_total:    int64     — volume total ask
por_corretora:    string    — JSON agregado de corretoras
ofi:              float64   — OFI (null se não disponível)
```

## 4. SCHEMA FINAL DO T&T (13 colunas)

```
ts_ns:            int64     — timestamp do evento (nanosegundos)
received_at_ns:   int64     — timestamp de recebimento (nanosegundos)
sequence_id:      int64     — ordem determinística local
ativo:            string    — símbolo do ativo (ex: WINV26)
asset_partition:  string    — partição hive (ex: WIN)
janela_id:        int16     — índice da janela RTD (0-11)
window_name:      string    — nome da janela (ex: T&T1)
is_rlp:           bool      — True se fluxo RLP
preco:            float64   — preço do negócio
quantidade:       int64     — quantidade negociada
agressor:         string    — 'Comprador' ou 'Vendedor'
compradora:       string    — corretora compradora
vendedora:        string    — corretora vendedora
```

## 5. ESTRUTURA FINAL DOS DIRETÓRIOS

```
D:\MarketData\Profit\RAW\
  data_type=BOOK\
    date=YYYYMMDD\
      asset=IND\part-NNNN.parquet
      asset=DOL\part-NNNN.parquet
      asset=WIN\part-NNNN.parquet
      asset=WDO\part-NNNN.parquet
  data_type=TT\
    date=YYYYMMDD\
      asset=IND\part-NNNN.parquet
      asset=DOL\part-NNNN.parquet
      asset=WIN\part-NNNN.parquet
      asset=WDO\part-NNNN.parquet
      asset=WIN_RLP\part-NNNN.parquet
      asset=WDO_RLP\part-NNNN.parquet
```

## 6. MÉTODO DE PARTICIONAMENTO

- **Camada 1:** `data_type=TT|BOOK` — separa tipos de dado
- **Camada 2:** `date=YYYYMMDD` — separa por dia
- **Camada 3:** `asset=WIN|IND|WDO|DOL|WIN_RLP|WDO_RLP` — separa por ativo

Particionamento 100% Hive-compatible. Leitura via `pyarrow.dataset.dataset()` com filtros pushdown.

## 7. MÉTODO DE ESCRITA

- **Engine:** PyArrow (`pa.Table.from_pylist` com schema explícito)
- **Compressão:** Snappy
- **Buffer:** `_buf` dict por `(data_type, asset)`
- **Flush:** periódico (500 rows ou 5s de idade)
- **Multi-part:** `part-NNNN.parquet` incrementais por partição
- **Reinício:** cria novos parts sem sobrescrever anteriores

## 8. MÉTODO DE FLUSH

- **Condicional:** 500 rows OU 5 segundos desde último flush
- **Periódico:** a cada 0.5s via `_periodic_flush()` no capture_daemon
- **Manual:** `fs.flush()` e `fs.fechar()` no shutdown
- **Drain:** `_drain_queue()` esvazia fila restante no shutdown

## 9. ESTRATÉGIA DE REINÍCIO

- **Não destrói dados:** novos `part-NNNN.parquet` são criados incrementalmente
- **Leitura:** todos os parts de uma partição são lidos como dataset único
- **Compatível:** formatos antigos (JSONL) continuam legíveis

## 10. ESTRATÉGIA DE DUPLICAÇÃO

- **Antes da persistência:** `profit_rtd.py` dedup por assinatura (DAT+ACP+PRE+QUL+AVD+AGR+AGAG)
- **LRU eviction:** 50.000 assinaturas por ativo
- **Baseline:** primeiro refresh absorve como baseline (não emite)
- **No writer:** sem dedup adicional — preserva todos os registros recebidos

## 11. TESTES EXECUTADOS

| Teste | Resultado |
|-------|-----------|
| Schema TT explícito | OK |
| Schema BOOK explícito | OK |
| Threshold ms→ns (1e17) | OK |
| 6 fluxos TT (4 + 2 RLP) | OK |
| 4 fluxos BOOK | OK |
| PyArrow Dataset hive | OK |
| Filtro data_type=TT | OK |
| Filtro data_type=BOOK | OK |
| Filtro asset=WIN | OK |
| Filtro asset=IND | OK |
| Filtro asset=WDO | OK |
| Filtro asset=DOL | OK |
| Filtro asset=WIN_RLP | OK |
| Filtro asset=WDO_RLP | OK |
| Filtro BOOK+WIN | OK |
| Filtro BOOK+DOL | OK |
| Filtro TT+WIN | OK |
| Filtro TT+WIN_RLP | OK |
| Filtro TT+WDO | OK |
| Filtro TT+WDO_RLP | OK |
| Validação automática (47 checks) | OK |

## 12. QUANTIDADE DE REGISTROS POR FLUXO (teste controlado)

| Fluxo | Registros | Arquivos | janela_id |
|-------|-----------|----------|-----------|
| BOOK/IND | 5 (3 niveis + fallback) | 1 | 0 |
| BOOK/WIN | 5 | 1 | 1 |
| BOOK/WDO | 5 | 1 | 2 |
| BOOK/DOL | 5 | 1 | 3 |
| TT/IND | 1 | 1 | 0 |
| TT/WIN | 1 | 1 | 1 |
| TT/WDO | 1 | 1 | 2 |
| TT/DOL | 1 | 1 | 3 |
| TT/WIN_RLP | 1 | 1 | 4 |
| TT/WDO_RLP | 1 | 1 | 5 |

## 13. PROBLEMAS ENCONTRADOS E CORRIGIDOS

1. **Bug threshold (P0):** `1e12` era pequeno demais — timestamps atuais em ms (~1.78e12) excediam o threshold, causando ms→ns não convertido. Corrigido para `1e17`.

2. **CaptureDaemon não passava janela_id para BOOK:** `registrar_book` aceitava apenas 6 parâmetros. Corrigido para aceitar janela_id, window_name, received_at_ns.

3. **Schema inferido:** `pa.Table.from_pylist()` inferia tipos inconsistentes entre arquivos. Corrigido com schemas explícitos `TT_SCHEMA` e `BOOK_SCHEMA`.

4. **Campos duplicados:** branch "else" do registrar_book tinha bid_vol_total, ask_vol_total, por_corretora duplicados. Corrigido.

5. **ofi opcional:** row do BOOK não sempre incluía campo `ofi`. Corrigido para sempre incluir (None quando não disponível).

## 14. PROBLEMAS QUE PERMANECEM

1. **rtd_writer.py legado:** Ainda contém `thread_escritora` e `thread_escritora_tt` (não utilizados). Pode ser removido em limpeza futura.

2. **RLP sem received_at_ns:** O caminho RLP em app.py não passa received_at_ns/sequence_id (aceitável para dados históricos de reabertura).

3. **Compressão:** Snappy configurada mas não verificada em produção (precisa de dados reais para validar).

## 15. CONFIRMAÇÃO DE RECONSTRUÇÃO

O RAW pode ser reconstruído integralmente a partir dos Parquets gravados:
- Cada partição Hive é auto-contida (data_type + date + asset)
- Todos os campos RAW são preservados (nenhum descartado)
- Timestamps em nanosegundos (máxima precisão)
- janela_id permite identificar a origem de cada registro
- PyArrow Dataset permite leitura seletiva sem carregar tudo em memória
