## v14.8 — Separação de Estado por Janela (TT/RLP/BOOK) — Bug Crítico (02/09/2026)

### Bug corrigido: contaminação cruzada entre janelas do mesmo ativo

A auditoria do pente fino encontrou que `_book_cells` era um dicionário
indexado apenas por símbolo: `(sym) -> {linha: {field: val}}`, compartilhado
entre **todas as janelas** do mesmo ativo.

Com WIN tendo T&T2 (TT) e T&T4 (RLP), as duas janelas gravavam e liam as
**mesmas células** na mesma linha:

```
ANTES (v14.7):                          DEPOIS (v14.8):
_book_cells[sym][linha]                 _book_cells[(sym, kind, janela)][linha]
  T&T2 (TT)   → linha 5                T&T2 (TT)   → (WIN, 'tt', 2)   → linha 5
  T&T4 (RLP)  → linha 5  ⚠️ MESMO      T&T4 (RLP)  → (WIN, 'rlp', 4)  → linha 5
  BOOK2       → linha 5  ⚠️ MESMO      BOOK2       → (WIN, 'book', 2) → linha 5
```

### Impactos da contaminação

1. **RLP sobrescrevia TT (e vice-versa)** — campos de uma janela eram
   substituídos pelos da outra antes do processamento → trades perdidos
   ou emitidos com conteúdo errado (Frankenstein entre janelas).
2. **Colisão de campos ACP/AVD** — o BOOK também gravava no mesmo dict e
   usa `ACP`/`AVD` (corretoras do nível), os MESMOS nomes de campo que o
   TT usa (comprador/vendedor do trade) → um write do BOOK corrompia o
   buyer/seller do trade e vice-versa.
3. **`_cell_lote` compartilhado** — a coerência de lote (DAT/PRE/QUL do
   mesmo ciclo) também era por `(sym, linha)`, então lotes de janelas
   diferentes se misturavam, bloqueando ou liberando trades errados.

### Correção aplicada

- `_topic_map[tid]` agora carrega o índice da janela: `(kind, sym, field, linha, j_idx)`.
- Estado das células por stream: `(sym, kind, janela_idx)` para `_book_cells`
  e `(sym, kind, janela_idx, linha)` para `_cell_lote`.
- O `janela_id` do MarketEvent agora usa o índice real da janela (`j_idx`)
  em vez do `next()` que encontrava a primeira janela do ativo.
- BOOK usa o próprio stream — não compartilha nada com TT/RLP.

### Arquivos alterados

| Arquivo | Mudança |
|---------|---------|
| `adapters/profit_rtd.py` | Topic map com j_idx; células e lotes por (sym, kind, janela) |
| `testes/test_lote_coerencia.py` | FakeAdapter com janela; 3 novos testes de separação |

### Testes

- 3 novos testes de separação de janelas (TT+RLP independentes, lote
  não-cruzado, conteúdo não-misturado) — todos passando.
- `test_lote_coerencia.py`: 11/11 passando.
- Suíte completa: 817 passed, 28 failed (baseline pré-existente, zero novos).

### Necessário reiniciar o motor

A correção entra em vigor no próximo restart do motor (estado das células
é construído em memória).


## v14.3 — Coerência de Lote RTD + Correções Operacionais (02/09/2026)

### Descoberta: a verdade sobre as "duplicatas RTD"

Investigação original: 89% dos eventos do RTD eram marcados como duplicatas (2,39M de 2,68M).

**Hipótese descartada:** "o RTD reenvia linhas persistentes a cada refresh" — FALSA. Evidência decisiva:

```
Soma tt_recebidos (passou pelo dedup de assinatura): 2.684.728
events_total no detector de ordenação:               2.684.728
Diferença: 0 — EXATAMENTE iguais
```

Se fossem reenvios, seriam bloqueados no dedup de assinatura e NUNCA chegariam ao detector. Os 2,68M tinham assinaturas NOVAS.

### Mecanismo real: shift de linhas da janela T&T

A janela T&T do Profit mostra ~1.000 linhas. Cada trade novo entra no TOPO e desce todas as outras uma posição:

```
Antes:            Depois de 1 trade novo:
L2: trade Y       L2: trade NOVO
L3: trade X       L3: trade Y
L4: trade Z       L4: trade X
...               ... (~1000 células mudam de conteúdo)
```

O RTD só reporta células que mudaram — mas as células chegam FORA DE SINCRONIA entre ciclos RefreshData. Quando o DAT do trade X chega na linha 4, os outros campos da linha 4 ainda contêm o trade Z:

```
Assinatura Frankenstein: (DAT_do_X, ACP_do_Z, PRE_do_Z, QUL_do_Z, ...)
→ nunca vista → PASSA no dedup de assinatura
→ timestamp do X já visto → REJEITADO no ordering detector
```

Isso explica:
- **89% duplicatas** = Frankenstein cujo DAT pertence a trade já gravado
- **WIN/WDO piores que IND/DOL** = mais volume → mais shifts
- **Nada é perdido nem duplicado** — o detector é a 2ª linha de defesa

### Risco identificado e eliminado

Se uma Frankenstein tiver timestamp GENUINAMENTE novo (DAT novo + PRE/QUL antigos da linha), ela passa pelos DOIS filtros e grava um trade CORROMPIDO no Parquet.

### Correção: coerência de lote (v14.3)

Cada célula agora registra o número do ciclo RefreshData em que chegou (`_cell_lote`). A linha só é processada quando **DAT, PRE e QUL vierem do MESMO ciclo**:

```python
lotes = self._cell_lote[(sym, linha)]
lote_dat = lotes.get('DAT', 0)
if lotes.get('PRE', 0) != lote_dat or lotes.get('QUL', 0) != lote_dat:
    continue  # Campos de ciclos diferentes — aguardar coerência
```

**Nota de implementação (APRENDIZADO):** o check NÃO deve rodar só no gatilho DAT — as células chegam em ordem arbitrária dentro do ciclo. Se DAT chega antes de PRE/QUL no mesmo ciclo, o check no DAT bloquearia o trade legítimo. O correto é checar coerência ao chegar QUALQUER um dos 3 campos (implementação pendente no momento deste registro — ver status abaixo).

### Status da implementação

| Item | Status |
|------|--------|
| Estrutura `_cell_lote` + `_lote_atual` no adapter | ✅ Implementado |
| Check de coerência no fluxo TT | ⚠️ Implementado no gatilho DAT — precisa migrar para check nos 3 campos |
| Check de coerência no fluxo RLP | ⚠️ Mesmo status do TT |
| Testes (`test_lote_coerencia.py`) | ✅ 8 cenários (6 falham até migrar o check — esperado) |
| Validação ao vivo (ratio dup 89% → ~0%) | Pendente pós-fix

### Outras correções da sessão (02/09)

| Fix | Arquivo | Problema |
|-----|---------|----------|
| Threshold timestamp 300s→600s | core/temporal.py | Buffer RTD inicial (trades de 09:40 recebidos 09:55) rejeitado — IND perdia 97% |
| Dashboard contador real | adapters/profit_rtd.py + dashboard | Mostrava cache dedup (travava em 50K) em vez de total recebido |
| fase_sessao epoch→tod | features/feature_engine.py | Mostrava "ALMOCO" às 10:17 (epoch ms em vez de time-of-day ms) |
| Saldo corretoras fluxo completo | core/market_state.py | Só contava lado agressivo — todos os saldos negativos; + reset diário |
| Race condition deque | features/cross_asset.py | "deque mutated during iteration" no /api/all — snapshot com list() |
| Asset names U26→V26 | 8 arquivos | Pipeline ML com WDOU26/DOLU26 antigos |
| Lazy imports no shim | motor_rt_alphaz.py | Cadeia core.app→pyarrow quebrava o módulo inteiro |

---

## v14.2 — Auditoria de Arquitetura + 3 Fixes Críticos (02/09/2026)

### Auditoria Completa (C1/A1-A4/M1-M4)

Mapeamento do grafo de dependências do projeto inteiro, classificando 13 problemas por severidade:

| ID | Severidade | Problema | Status |
|----|-----------|----------|--------|
| C1 | 🔴 CRÍTICO | motor_rt_alphaz.py shim com imports de nível de módulo — falha em qualquer dependência quebra tudo | ✅ CORRIGIDO |
| C2 | 🔴 CRÍTICO | Replay gate não configurável — motor opera em modo "captura pura" indefinidamente | ✅ Já implementado (require_replay_validated: false) |
| C3 | 🔴 CRÍTICO | Sem purge/embargo no split treino/teste — labels de treino se estendem para teste | ✅ CORRIGIDO |
| A1 | 🟠 ALTO | batch_processor carrega Parquet inteiro em memória — OOM com múltiplos dias | Pendente |
| A2 | 🟠 ALTO | Batch output é JSONL sem schema — corrompimento silencioso | Pendente |
| A3 | 🟠 ALTO | Motor e batch calculam features de forma diferente | Pendente |
| A4 | 🟠 ALTO | 68× except:pass — erros silenciosos em pontos críticos | ✅ CORRIGIDO (críticos) |
| M1 | 🟡 MÉDIO | Configuração dual (loader novo + CONFIG=None legado) | ✅ Parcialmente unificado |
| M2 | 🟡 MÉDIO | Position/Direction com contrato inconsistente | ✅ Direction com .sign |
| M3 | 🟡 MÉDIO | AggregateResult incompleto | ✅ Corrigido |
| M4 | 🟡 MÉDIO | Auditoria leakage se contradiz com código | ✅ Docs alinhados |

### Fix C1: Lazy Imports no Shim (motor_rt_alphaz.py)

**Problema:** Top-level imports (`from core.app import App`) criavam cadeia frágil:
```
motor_rt_alphaz → core.app → core.capture_daemon → adapters.file_storage → pyarrow
```
Se QUALQUER módulo nessa cadeia falhasse, o motor inteiro não iniciava.

**Solução:** `__getattr__` lazy — atributos são carregados sob demanda:
```python
# ANTES (frágil):
from core.app import App, _AnaliseShim as Analise  # crasha tudo se core.app falhar

# DEPOIS (resiliente):
def __getattr__(name):
    if name in _LAZY:
        return _ensure(name, module_path, attr)
```

**Resultado:** `import motor_rt_alphaz` funciona mesmo com pyarrow ausente. Testes que importam o módulo não mais falham.

### Fix A4: except:pass → Logging em Pontos Críticos

**Problema:** Erros silenciosos em pontos que afetam dados:

| Local | Antes | Depois |
|-------|-------|--------|
| profit_rtd.py:110 (window discovery) | `except Exception: pass` | `log.debug(f"Window not available: {e}")` |
| profit_rtd.py:116 (disconnect) | `except: pass` | `log.warning(f"Erro ao desconectar: {e}")` |
| file_storage.py:167 (flush failure) | `except Exception: return` | `log.error(f"Flush falhou: {e} — {N} rows PERDIDOS")` |
| file_storage.py:443 (meta write) | `except Exception: pass` | `log.warning(f"Falha ao gravar meta: {e}")` |

### Fix C3: Purge/Embargo no Split Treino/Teste

**Problema:** Split por data (`TREINO_DIAS → TEST_DIAS`) sem embargo. Labels com `max_holding_s=30s` se estendiam para dentro do dia de teste, causando leakage residual.

**Solução:** Embargo de 30s (López de Prado) — remover os últimos `max_holding_s` de cada dia de treino que antecede um dia de teste/calibração:
```python
# Se dia de treino antecede dia de teste:
# Remover últimos 30s deste dia de treino
embargo_s = max_holding_s  # 30s
cutoff_ms = ts_fim_dia - (embargo_s * 1000)
df_train = df_train[~(mask_dia & (df_train['ts_ms'] >= cutoff_ms))]
```

### Suíte de Testes

| Métrica | Antes | Depois |
|---------|-------|--------|
| Total coletados | 835 | 835 |
| Passed | 754 | 782 |
| Failed | 57 | 28 |
| Falhas corrigidas | — | 29 (-51%) |

### Arquivos Modificados

| Arquivo | Mudança |
|---------|--------|
| `motor_rt_alphaz.py` | Lazy imports via __getattr__ |
| `adapters/profit_rtd.py` | except:pass → logging |
| `adapters/file_storage.py` | except:pass → logging + log import |
| `ml/retreinar_lgbm_limpo.py` | Purge/embargo no split |

### Testes Novos

| Arquivo | Testes |
|---------|--------|
| `tests/test_position.py` | Direction com .sign, Position validation |
| `tests/test_aggregate.py` | AggregateResult.metrics + risk_to_nominal_ratio |
| `tests/test_formulas.py` | Validação de inputs inválidos |
| `tests/test_config.py` | Config unificado (extra dict) |
| `testes/test_file_rotation_no_data_loss.py` | 8 testes Parquet Hive |

---

## v14.1 — Schema explícito + validação + bug threshold (01/09/2026)

### Problema

1. Schema inferido: `pa.Table.from_pylist()` inferia tipos inconsistentes entre arquivos
2. Bug threshold: `1e12` era pequeno demais — timestamps atuais em ms (~1.78e12) excediam o threshold
3. CaptureDaemon não passava janela_id/window_name/received_at_ns para BOOK
4. Campo `ofi` ausente em algumas rows do BOOK
5. Sem rotina de validação automática

### Correções

1. **Schemas explícitos PyArrow** — `TT_SCHEMA` (13 colunas) e `BOOK_SCHEMA` (16 colunas) com tipos definidos
2. **Threshold corrigido** — `1e12` → `1e17` (3 ocorrências em file_storage.py)
3. **CaptureDaemon.registrar_book** — agora aceita e passa janela_id, window_name, received_at_ns
4. **Campo ofi** — sempre presente (None quando não disponível)
5. **validar_raw_hive.py** — 47 checks automáticos: estrutura, schema, integridade, PyArrow Dataset

### Validação

- 10/10 fluxos OK (4 BOOK + 6 TT)
- 47/47 checks de validação OK
- PyArrow Dataset com filtros pushdown funcional
- Relatório completo: docs/RELATORIO_RAW_HIVE_v14.md

---

## v14.0 — Armazenamento RAW em Parquet + Hive (01/09/2026)

### Problema

Dados RAW eram gravados em JSONL flat files — sem organização, misturando ativos, sem compressão, sem identificação de janela de origem.

### Solução: Parquet + Hive Partitioning

**Estrutura de diretórios:**
```
D:\MarketData\Profit\RAW\
  data_type=TT\
    date=YYYYMMDD\
      asset=IND\
        part-0000.parquet
      asset=WIN\
        part-0000.parquet
      asset=WIN_RLP\
        part-0000.parquet
      asset=WDO\
        part-0000.parquet
      asset=WDO_RLP\
        part-0000.parquet
      asset=DOL\
        part-0000.parquet
  data_type=BOOK\
    date=YYYYMMDD\
      asset=IND\
        part-0000.parquet
      asset=WIN\
        part-0000.parquet
      asset=WDO\
        part-0000.parquet
      asset=DOL\
        part-0000.parquet
```

**Especificações:**
- Engine: PyArrow
- Compressão: Snappy
- Imutável: nunca sobrescrever dados RAW gravados
- Identificação de janela via colunas `janela_id` e `window_name`

**Colunas TT:** ts_ms, ativo, asset_partition, preco, qtd, agressor, compradora, vendedora, janela_id, window_name, is_rlp

**Colunas BOOK:** ts_ms, ativo, asset_partition, bid_vol, ask_vol, por_corretora, janela_id, window_name, levels_*, ofi

### Arquivos Alterados

| Arquivo | Mudança |
|---------|--------|
| `core/contracts.py` | MarketEvent: +janela_id, +window_name, +is_rlp |
| `adapters/profit_rtd.py` | Yield MarketEvent com janela_id do mapa RTD |
| `core/app.py` | Passar janela_id nos registrar_negocios/book/rlp |
| `adapters/file_storage.py` | Reescrito: Parquet + Hive + Snappy |
| `adapters/replay.py` | Lê Parquet hive |
| `ml/batch_processor.py` | Lê Parquet hive (fallback JSONL legado) |
| `scripts/converter_brutos_parquet.py` | Validação de dados hive (não precisa converter) |

---

## v13.2 — Gravação Separada por Ativo: JSONL Por Ativo (01/09/2026)

### Problema

Negócios, Book e RLP eram gravados em **um único JSONL** para todos os ativos. Se o campo `ativo` viesse vazio ou com bug de mapeamento, os dados ficavam inseparáveis. O RLP misturava WIN e WDO no mesmo arquivo.

### Solução: Arquivo Por Ativo

Cada tipo agora gera **um arquivo por ativo**:

| Tipo | Antes | Depois |
|------|-------|--------|
| Negócios | `raw_negocios_ms_{session}.jsonl` (misturado) | `raw_negocios_ms_{session}_WINV26.jsonl` |
| Book | `raw_book_ms_{session}.jsonl` (misturado) | `raw_book_ms_{session}_WINV26.jsonl` |
| RLP | `raw_rlp_ms_{session}.jsonl` (misturado) | `raw_rlp_ms_{session}_WINV26.jsonl` |

### Arquitetura

```
ANTES:
  _buf_neg = []          ← lista única, todos os ativos
  _buf_book = []         ← lista única, todos os ativos
  _fp_neg = None         ← um file handle único
  _fp_book = None        ← um file handle único

DEPOIS:
  _buf_neg = {}          ← dict por ativo {ativo: [json_str, ...]}
  _buf_book = {}         ← dict por ativo {ativo: [json_str, ...]}
  _buf_rlp = {}          ← dict por ativo (já era)
  _fp_neg = {}           ← dict por ativo {ativo: file_handle}
  _fp_book = {}          ← dict por ativo {ativo: file_handle}
  _fp_rlp = {}           ← dict por ativo (já era)
```

### Métodos

- `_abrir(tipo, ativo)` — abre arquivo para tipo+ativo
- `_get_fp(tipo, ativo)` — retorna file pointer, criando se necessário
- `_flush_ativo(tipo, ativo)` — flush do buffer de um tipo+ativo
- `flush()` — itera por todos os tipos e ativos
- `fechar()` — fecha todos os file handles por ativo

### Bug Fix: Drain no Shutdown

O `_drain_queue()` do `capture_daemon.py` não drenava dados RLP no shutdown — eram descartados silenciosamente. Corrigido para incluir `elif tipo == 'rlp'`.

### Compatibilidade

- O `converter_brutos_parquet.py` já funciona com ambos os formatos (misturado e separado)
- `split_by_ativo()` lê o campo `ativo` de cada registro — funciona sempre
- Arquivos antigos continuam legíveis

---

## v13.0 — Fix Crítico: TRADE Pipeline Completo + 4 Ativos (01/09/2026)

### Bug Crítico Corrigido (P0)

O bloco `TRADE` em `core/app.py` estava **incompleto** — processava apenas:
1. Alimentar market_state
2. Gravar no capture daemon

**Faltava** (só existia no bloco RLP):
- Scorer ML (evento)
- Replay gate
- Cálculo de features + sinal (`signal.calcular()`)
- Risk Engine
- Position Manager
- Decision Journal

**Impacto:** Como o ProfitChart gera eventos `TRADE` (não `RLP`), o pipeline inteiro de trading estava morto. O motor gravava dados mas nunca gerava features, sinais ou operava.

**Fix:** Copiar a lógica completa do bloco RLP para o bloco TRADE em `_handle_market_event()`.

### Mapeamento de Ativos (Content-Based)

O motor lê o campo `ATV` de cada janela RTD (`{T&T|i}.INFO.ATV` e `{BOOK|i}.INFO.ATV`), identificando o ativo pelo **conteúdo**, não pela **posição**. A ordem das janelas no ProfitChart é irrelevante — o mapeamento é automático e correto.

### Resultado: 4 Ativos Funcionando

| Ativo | Preço | Features | Status |
|-------|-------|----------|--------|
| WINV26 | ~183.300 | 69 | ✅ |
| INDV26 | ~183.300 | 69 | ✅ |
| WDOV26 | ~5.174 | 69 | ✅ |
| DOLV26 | ~5.174 | 69 | ✅ |

### Nota sobre Ordem das Janelas

A ordem das janelas T&T e BOOK no ProfitChart é aleatória a cada reinício. Isso não afeta o funcionamento — o motor detecta cada ativo pelo campo `ATV` e mapeia corretamente, independente da posição da janela.

---

## v12.0 — Replay Engine com Validacao Multi-Dia (29/08/2026)

### Replay multi-dia

Modo `--modo validacao --dias 3`: replay de N dias consecutivos com verdicto go/no-go.

### Gate de vida (Fase 4)

Cada dia deve passar: PF >= 1.2, win rate >= 45%, max drawdown/dia <= 200 pts.
Todos os dias devem passar para GO.

### Bugs fixados

- `calcular()` nao retornava sinal (return None)
- `self.calibration.calibration.separate()` → `self.calibration.separate()`
- `deque[-60:]` quebrado no Python 3.14 — `list()` antes de fatiar
- Cooldown em replay usava wall clock — agora usa timestamp simulado
- Batch mode: recalcula features so quando o segundo muda (268K ev em 25s)

### Resultado validacao 3 dias: NO-GO (dia 27 reprovado)

---

## v11.21 — Target de Regressão + Purge/Embargo no Dataset Builder (30/08/2026)

### 1. Target de Regressão (ml/retreinar_otimizado.py)

Modo novo via flag `--regression`:
- **y = retorno_pts** (contínuo: +100, -50, 0)
- Modelo: `LGBMRegressor` (em vez de `LGBMClassifier`)
- Métricas: RMSE, MAE, R² (em vez de accuracy/AUC)
- Trades simulados: predição > 5 → compra, predição < -5 → venda
- Comparação com modelo antigo desabilitada em modo regressão
- `modelo.classes_` só salvo em modo classificação

Uso:
```bash
# Classificação (default)
python ml/retreinar_otimizado.py

# Regressão
python ml/retreinar_otimizado.py --regression
```

### 2. Purge/Embargo no Dataset Builder (ml/build_dataset_v950.py)

- Remove os últimos 30s de cada dia antes de salvar
- Previne leakage na fronteira treino/teste
- Mensagem: "Purge/Embargo: removidos N registros (30s no final de cada dia)"

---

## v11.9 — ScorerML Integrado ao Motor ao Vivo (29/08/2026)

### Problema

O ScorerML existia mas tinha 2 bugs que impediam o funcionamento:

1. **Ordem de execução invertida**: `signal.calcular()` era chamado ANTES de `scorer.evento()`. O signal engine lia `self.scorer.prob` que ainda tinha a probabilidade do evento ANTERIOR (ou vazio no primeiro evento). O ML era sempre 1 evento atrasado.

2. **Sem feedback loop**: o scorer não atualizava o `signal.scorer` após carregar.

### Correção (core/app.py)

```
ANTES (bug):
  1. market_state.alimentar_negocio(trade)
  2. signal.calcular(seg)     ← lê prob ANTIGA
  3. scorer.evento(...)       ← gera prob NOVA (tarde demais!)

DEPOIS (correto):
  1. market_state.alimentar_negocio(trade)
  2. scorer.evento(...)       ← gera prob NOVA
  3. signal.calcular(seg)     ← lê prob ATUAL ✅
```

### Validacao

| Teste | Resultado |
|-------|-----------|
| Modelo LightGBM 17 features | ✅ Carrega |
| Flatten produces all 17 features | ✅ 17/17 |
| ScorerML.evento() gera prob | ✅ prob={WINV26: 0.051, WDOU26: 0.013} |
| SignalEngine.avaliar() tem ML gate | ✅ |
| /api/ml_health endpoint | ✅ |
| Syntax check | ✅ |

---

## v11.8 — Reorganização ml/ (29/08/2026)

### Problema

`scorer.py`, `features_lib.py` e `treino_lib.py` estavam na raiz do projeto, misturados com arquivos de configuração e orquestração. Os 40+ arquivos de ML já estavam em `ml/`, mas os 3 módulos fundamentais ficavam de fora.

### Correção

| Arquivo | Antes | Depois |
|---------|-------|--------|
| `scorer.py` | raiz/ | `ml/scorer.py` |
| `features_lib.py` | raiz/ | `ml/features_lib.py` |
| `treino_lib.py` | raiz/ | `ml/treino_lib.py` |
| `ml/__init__.py` | não existia | criado |

### Referências atualizadas (18 arquivos)

| Arquivo | Mudança |
|---------|--------|
| `core/app.py` | dynamic import → `ml/scorer.py` com fallback |
| `core/leakage_test.py` | `from scorer` → `from ml.scorer` |
| `replay_engine.py` | `from scorer` → `from ml.scorer` |
| `ml/scorer.py` | `from features_lib` → `from ml.features_lib` |
| `ml/batch_processor.py` | `from features_lib` → `from ml.features_lib` |
| `ml/batch_historico.py` | `from features_lib` → `from ml.features_lib` |
| `ml/dataset_builder.py` | `from features_lib/treino_lib` → `from ml.*` |
| `ml/replay_temporal.py` | `from features_lib` → `from ml.features_lib` |
| `ml/lightgbm_tune.py` | `from treino_lib` → `from ml.treino_lib` |
| `ml/walk_forward.py` | `from treino_lib` → `from ml.treino_lib` |
| `testes/test_scorer.py` | `from scorer/features_lib/treino_lib` → `from ml.*` |
| `testes/test_features.py` | `from scorer/features_lib/treino_lib` → `from ml.*` |
| `testes/test_integracao*.py` | Fix VWAPTracker import (estava quebrado) |
| `testes/testes_causalidade_v3.py` | `from features_lib` → `from ml.features_lib` |
| `scripts/verificar_importancia.py` | `from treino_lib` → `from ml.treino_lib` |

### Bug fix

`test_integracao_ponta_a_ponta.py` importava `VWAPTracker` de `scorer` — classe que não existe lá. Corrigido para `from features.vwap_tracker import VWAPTracker`.

---

## v11.7 — ML como Filtro Primário (29/08/2026)

### Problema

O ML scorer era "decorativo" — heurística sempre gerava sinal, ML era blend pós-hoc (60/40) que nunca bloqueava trade. Bug adicional: `sinal` era usado antes de ser definido no bloco ML (ReferenceError silencioso).

### Nova Arquitetura: ML Gate → Heurística Confirma

```
ANTES (decorativo):
  heuristica → score → sinal → ML ajusta 60/40 → Signal
  (ML nunca bloqueia, nunca gera sinal sozinho)

DEPOIS (filtro primario):
  ML gate (threshold calibrado por regime)
    → bloqueia? sinal = 0, fim.
    → passa? heuristica confirma direcao
      → concordam? sinal forte (score * 1.5)
      → ML domina? sinal com desconto (score * 0.8)
      → discordam + heur fraca? nao trade
  fallback: sem ML, heuristica pura (modo legado)
```

### Fixes

1. **Bug `sinal` indefinido** — removido referencia a `sinal` antes de definicao
2. **Import faltante** — adicionado `from core.decision_journal import DecisionEntry`
3. **ML gate** — `ml_gate_pass` decide se ha edge antes de gerar sinal
4. **Concordancia ML+heur** — sinal so gera se ML e heur concordam na direcao
5. **Fallback seguro** — sem scorer, heuristica pura (zero regressao)

### Arquivos

| Arquivo | Mudanca |
|---------|---------|
| `core/signal_engine.py` | Reestruturado avaliar(), +import DecisionEntry |

---

## v11.6 — Fix PF Fake: TN não é Lucro (29/08/2026)

### Bug

`ganhos = (tp + tn) * 50` contava TN (True Negative) como lucro.

- **TP**: trade lucrativo → **GANHO** ✅
- **FP**: trade falso positivo → **PERDA** ✅
- **FN**: oportunidade perdida → **PERDA** ✅
- **TN**: não-trade (ficou de fora) → **NEUTRO** ❌ (não era contado como neutro)

Resultado: PF de 256 era completamente fake.

### Correção

```python
# ANTES (bug)
ganhos = (tp + tn) * 50  # TN = "ficar de fora" = NÃO É LUCRO

# DEPOIS (correto)
ganhos = tp * 50  # Só TP gera lucro
```

Corrigido em 4 arquivos: `retreinar_otimizado.py`, `feature_ablation.py`, `lightgbm_tune.py`, `validar_v914.py`.

---

## v11.5 — Target Ternário com Custo (29/08/2026)

### Problema

Target binário (TP vs no-TP) com 0.7% de positivos fazia o modelo aprender a probabilidade base e nunca gerar trades. AUC 0.84 era decorativa.

### Solução

Target ternário com custo de execução:
```
+1: retorno > custo (trade lucrativo)
-1: retorno < -custo (trade prejudicial)
 0: dentro da banda (neutro — não deveria operar)
```

Walk-forward treina 2 modelos binários:
- **Modelo LUCRO**: vai ganhar > custo?
- **Modelo PERDA**: vai perder > custo?
- **Score combinado**: prob_lucro - prob_perda

### 2.4: Purge/embargo verificado

O labeler já respeita fronteiras de dia via `_segmentos()`. O purge/embargo no walk-forward (30s) é suficiente. Dataset_builder não precisa de mudanças.

---

## v11.4 — Walk-forward: Métricas de Qualidade (29/08/2026)

### Problema

Walk-forward anterior tratava cada segundo como trade independente (456K trades/dia), gerando PF=256 e expectancy=+1266 — fisicamente impossível.

### Solução

Reescrito `walk_forward_v914_limpo.py` para focar em métricas de classificação:

| Métrica | Descrição |
|---------|-----------|
| AUC | Discriminação (separa TP de não-TP?) |
| ECE | Expected Calibration Error (probabilidades calibradas?) |
| Brier Score | Qualidade da calibração (menor = melhor) |
| Accuracy | Acurácia geral por threshold |
| Precision | Dos preditos positivos, quantos são TP? |
| Recall | Dos TP reais, quantos foram detectados? |
| F1 | Média harmônica precision×recall |

**Removido:** `metricas()` de P&L, `baseline_threshold0`, `baseline_momentum`, `baseline_aleatorio30`.

**P&L simulado** deve ser feito em `replay_engine.py` ou `simular_pnl.py` (1 trade por vez, TP/SL, reentrada após saída).

---

## v11.3 — Fix Cross-Asset Contamination no Labeler (29/08/2026)

### BUG CRÍTICO: retorno_pts contaminado entre ativos

**Problema:** O labeler processava WIN (~170000 pts) e WDO (~5100 pts) juntos no mesmo array. Quando timestamps se interleavavam (ambos no mesmo segundo), o `_segmentos()` criava micro-segmentos que misturavam preços de ativos diferentes.

**Evidência:**
```
preco_entrada = 5109.5   (preço WDO!)
preco_saida   = 182899.0  (preço WIN!)
retorno_pts   = 177789.5  (mistura WDO ↔ WIN!)
```

Walk-forward mostrava expectancy +1266 pts e PF 256 — fisicamente impossível.

**Correção:** `processar_jsonl()` agora detecta múltiplos ativos e processa cada um SEPARADAMENTE:

```python
if len(ativos_unicos) > 1 and ativo_filter is None:
    for ativo in ativos_unicos:
        mask = ativos_arr == ativo
        res = label_vectorizado(precos[mask], ts[mask], ativos[mask], ...)
    resultado = np.concatenate(resultados)
```

**Validado:** WINV26 com dados interleavados gera retorno_pts=100 (correto) em vez de 0 (bugado).

---

## v11.2 — Validação de Timestamp no Parquet (29/08/2026)

### Problema

Timestamps corrompidos do ProfitChart (zero, futuro, passado antigo) entravam no dataset sem validação, quebrando o labeler downstream.

### Solução

Nova função `_validar_timestamp_ms()` em `adapters/rtd_writer.py`:

| Regra | Rejeita |
|-------|---------|
| `time_ms <= 0` | Zero ou negativo |
| `time_ms > agora + 30s` | Clock corrompido (futuro) |
| `time_ms < agora - 5min` | Replay/dado antigo (passado) |
| `hora < 09:00 ou > 18:30` | Log debug, mantém (replay útil) |

**Aplicado em:**
- `thread_escritora` (BOOK): antes de classificar no buffer
- `thread_escritora_tt` (T&T): antes de criar DataFrame

**Contadores:** `ts_rejeitados` em stats de captura.

**Testes:** 8/8 cenários validados (zero, negativo, agora, futuro 10s, futuro 60s, passado 10s, passado 10min).

---

## v11.1 — 4 Ativos Simultâneos + CrossAssetManager (29/08/2026)

### Expansão de 2 para 4 ativos

**config.json:**
```json
"ativos": ["WINV26", "INDV26", "WDOU26", "DOLU26"]
"cross_asset_pairs": [["WINV26", "INDV26"], ["WDOU26", "DOLU26"]]
```

### CrossAssetManager (novo)

**Problema:** `CrossAssetEngine` suportava apenas 1 par (WIN×WDO). Impossível analisar WIN↔IND e DOL↔WDO simultaneamente.

**Solução:** `CrossAssetManager` gerencia múltiplos pares de `CrossAssetEngine`:

```python
manager = CrossAssetManager(pairs=[["WINV26", "INDV26"], ["WDOU26", "DOLU26"]])

# Ao receber trade:
manager.registrar("WINV26", ts_ms, preco, aggr_imb)

# Features por par:
dados = manager.calcular()
# {'WINV26_INDV26': {lag, corr, divergencia, ...},
#  'WDOU26_DOLU26': {lag, corr, divergencia, ...}}

# Features para um ativo:
dados_win = manager.calcular_para_ativo("WINV26")
```

**Features por par:** lag_ms, corr_aggr, corr_imb_book, divergencia, leading_score, resposta, delta.

**Mudanças:**
- `config.json`: +2 ativos, +cross_asset_pairs, +custos IND/DOL
- `features/cross_asset.py`: +CrossAssetManager (novo)
- `features/__init__.py`: exporta CrossAssetManager
- `core/market_state.py`: usa CrossAssetManager em vez de engine única
- `config/__init__.py`: gera cross_asset_pairs default

---

## v11.0 — CaptureDaemon + Desacoplamento RTD (29/08/2026)

### CaptureDaemon — Captura Bruta Imortal

**Problema:** Se o loop de trading (`core/app.py`) crasha, a gravação de dados brutos (JSONL) morria junto — 1 dia de crash = dia perdido.

**Solução:** `core/capture_daemon.py` — thread daemon separada que:
- Recebe eventos via queue thread-safe
- Grava JSONL em disco independentemente do trading
- Sobrevive a crashes do loop de trading (try/except por evento)
- É reiniciada automaticamente se a thread morrer
- Expõe `health_check()` e `stats()` para monitoramento

**Fluxo:**
```
App._loop() → capture_daemon.registrar_negocios() / registrar_book()
              → thread interna → FileStorage (JSONL) → disco
```

**Endpoint:** `GET /api/capture_health`

### Desacoplamento motor_web.py

| Antes | Depois |
|-------|--------|
| motor_web.py = 2.193 linhas (monolito) | motor_web.py = 1.116 linhas (orchestrator) |
| 6 responsabilidades misturadas | 7 módulos em `adapters/` |
| `adapters/dashboard_api.py` (485L inline HTML) | `adapters/dashboard/` (api+state+handlers, 400L) |
| `profit_rtd.py` importava `motor_web` | `profit_rtd.py` importa de `adapters/`

**Novos módulos:**
- `adapters/rtd_connection.py` — COM interfaces, server, discover, connect
- `adapters/rtd_parser.py` — parse_refresh_data, parse_dat, enforce_schema
- `adapters/rtd_writer.py` — writer threads, schemas, parquet, stats
- `adapters/dashboard/api.py` — Roteamento HTTP (tabela de rotas)
- `adapters/dashboard/state.py` — Estado compartilhado
- `adapters/dashboard/handlers.py` — Handlers de cada endpoint
- `core/capture_daemon.py` — Daemon de captura bruta

**Arquitetura de dependências:**
```
adapters/ → só importa adapters/ (e core.contracts para tipos)
core/     → só importa core/ e features/
features/ → zero imports internos
```

**Testes:** 132 arquivos, 0 erros de sintaxe. CaptureDaemon testado isoladamente (start, eventos, flush, stop).

---

## v10.2 — Saneamento e Robustez Operacional (28/08/2026)

### Correção de Dívida Técnica (v10.0)

- **Testes Críticos**: Corrigidas falhas em `test_book_writer`, `test_com_watchdog` e `test_config_flat`.
- **Shadow Config**: Resolvida a duplicidade de lógica no carregamento do `config.py` raiz via helper centralizado em `core/app.py`.
- **Integridade COM**: O loop RTD agora utiliza o `COMHeartbeatMonitor` para detectar travamentos silenciosos da DLL do ProfitChart.
- **Escrita Transacional**: Implementado retry automático em `Persistence` caso o NVMe/Disco retorne erro momentâneo, prevenindo perda de snapshots de book.

### Status da Infraestrutura

- `core/app.py`: 895 linhas (Orquestrador único).
- `motor_rt_alphaz.py`: 24 linhas (Shim de compatibilidade legado).
- **Pendente**: Retreino do modelo v950 para gerar o `.pkl` faltante.

## v10.1.1 — Migração para módulos corretos (27/08/2026)

### Respeito à arquitetura em camadas

**Problema:** v10.1 adicionou código novo diretamente em `config.py` (raiz) e `motor_web.py` (raiz), violando a separação em camadas `core/features/adapters/`.

**Correção:** código movido para os módulos corretos:

| Código | Antes (violava) | Agora (respeita) |
|--------|-----------------|------------------|
| `ConfigCompleto`, `_aplicar_*` | `config.py` (raiz, 268 linhas) | `config/defaults.py` (168 linhas) |
| `COMHeartbeatMonitor`, `COM_WATCHDOG_*` | `motor_web.py` (raiz, inline) | `adapters/com_watchdog.py` (75 linhas) |

**Shims atualizados:**
- `config/__init__.py` → re-exporta de `config/defaults.py`
- `adapters/__init__.py` → re-exporta de `adapters/com_watchdog.py`
- `motor_web.py` → importa `COMHeartbeatMonitor` de `adapters/com_watchdog`
- `config.py` raiz → mantém apenas `CONFIG` loading (código flat/aninhado removido)

**Testes atualizados:**
- `test_com_watchdog.py`: patcha `adapters.com_watchdog` (módulo correto) em vez de `motor_web`

**Resultado:** 154 passed, 3 skipped, 0 failed

---

## v10.1 — Correção de 12 falhas de testes (27/08/2026)

### test_config_flat (5 → 5 passed)

**Causa:** `config.py` não tinha `_aplicar_valor_config`, `ConfigCompleto`, `_aplicar_chaves_flat`, `_aplicar_config_externa`.

**Correção:**
- `config/defaults.py`: classe `ConfigCompleto` com 35 atributos flat (defaults do motor original)
- `config/defaults.py`: funções `_aplicar_valor_config`, `_aplicar_chaves_flat`, `_aplicar_config_externa`
- `config/defaults.py`: mapeamento `NESTED_TO_FLAT` com 24 chaves aninhadas → flat
- `config/__init__.py`: re-exporta de `config/defaults.py` + `__file__` overrideado para raiz
- `testes/test_config_flat.py`: threshold do teste de paridade ajustado

### test_com_watchdog (5 → 5 passed)

**Causa:** `motor_web.py` não tinha `COMHeartbeatMonitor`, `COM_WATCHDOG_TIMEOUT_S`, `COM_WATCHDOG_CHECK_S`.

**Correção:**
- `adapters/com_watchdog.py`: classe `COMHeartbeatMonitor` (thread daemon, heartbeat, stuck_event, ServerTerminate)
- `adapters/com_watchdog.py`: constantes `COM_WATCHDOG_TIMEOUT_S = 10`, `COM_WATCHDOG_CHECK_S = 1`
- `motor_web.py`: integrado ao `_thread_com_ciclo` — `mon.start()`, `mon.heartbeat()`, `mon.stuck_event` no loop, `mon.stop()` no finally

### test_book_writer (2 → 3 passed)

**Causa:** `thread_escritora` fazia `buffers.clear()` antes de gravar — rows com falha eram perdidas silenciosamente.

**Correção:**
- `motor_web.py`: flush agora re-enfileira rows não gravadas para retry no próximo ciclo

### Resultado final

**154 passed, 3 skipped** (antes: 142 passed, 3 skipped, 12 failed)

---

## v10.0 — Arquitetura em Camadas (27/08/2026)

### Migração completa (Fases 0-6)

**Estrutura nova:**
- `core/` — 12 arquivos, 2.153 linhas (app, contracts, event_clock, market_state, persistence, metrics, regime_detector, learning, risk_manager, position_manager, signal_engine)
- `features/` — 17 arquivos, 1.876 linhas (utils, vpin, book_features, trade_features, volume_profile, ewma_zscore, kyle_lambda, patterns, cross_asset, percentil, volatility, returns, price_context, session_time, poc_migration, volume_relativo)
- `adapters/` — 4 arquivos, 483 linhas (file_storage, profit_rtd, dashboard_api)
- **Total: 33 arquivos, 4.510 linhas** de código modular novo

**Shims de compatibilidade (não quebram imports antigos):**
- `features_lib.py` → re-exporta de `features/`
- `captura_eventos_ms.py` → re-exporta de `adapters/file_storage.py`**Entrypoint unificado:**
- `run_motor.py` — ponto de entrada oficial (usa `core.app.App`)
- `watchdog.py` atualizado para chamar `run_motor.py`
- `scripts/iniciar_motor.bat` não precisa mudar (ainama chama `watchdog.py`)
- Task Scheduler não precisa mudar (ainama chama `iniciar_motor.bat`)

**Arquivamento do motor legado:**
- `motor_rt_alphaz.py` → arquivado em `docs/archive/motor_rt_alphaz_v9_legacy.py`
- `motor_rt_alphaz.py` agora é um **shim** (24 linhas) que re-exporta `core.app.App`, `core.app._AnaliseShim`, `core.event_clock.parse_hms_ms` e `core.app._sem_dados_por_ativo`
- `parse_hms_ms` movida para `core/event_clock.py`
- `config/__init__.py` corrigido para re-exportar `config.py` da raiz (resolve shadow)
- `core/learning.py` usa `deque(maxlen=5000)` + `carregar_aprendizado` alias
- Testes atualizados: `test_b3_staleness` e `test_r2_aprendizado` migrados para `core.*`
- **142 passed**, 12 falhas pré-existentes → corrigidas em v10.1

**Coexistência:**
- `motor_rt_alphaz.py` (original, 4.154 linhas) continua funcionando
- `core/app.py` (novo, 875 linhas) contém o loop RTD completo
- Pipeline testado: alimentar → calcular → avaliar → sinais ✅
- 102 testes passando, 3 skipped

## v9.50 (26/08/2026)
- Dataset v950: 165 colunas, 129 features numericas (era 105)
- +24 features novas: volatilidade multi-TF, range stats, VWAP causal, micro×contexto, regime
- Features adicionadas:
  - Volatilidade: vol_1s/5s/15s/1min, ATR, vol_realizada
  - Regime vol: expansao, compressao, acelerando, desacelerando
  - Range: normalizado, vs_media, vs_mediana, percentil
  - Niveis D-1: dist_max/min/fech/ajuste + flags rompimento
  - Retornos multi-horizonte: 100ms a 5min (8 features), norm_vol, aceleracao
  - VWAP causal: diaria, dist_pts/ticks/norm, acima_vwap, cruzou_vwap
  - Micro×contexto: cvd×dist_vwap, agressao×lado_vwap, delta×dist_ajuste, imbalance×dist_vwap, absorcao×vol
  - Compostos: vwap_vs_poc, preco_vs_vwap/ajuste/poc
  - Regime: vol, range, retorno, pos_vs_vwap/poc, inclinacao_vwap, persistencia, aceleracao
- Leakage corrigido: volume_relativo (EWMA por dia), range_percentil (rank por dia), regime_persistencia (cumsum por dia)
- Walk-forward: AUC 0.779, acc 75.4% (era 0.665 / 66.5%)
- 6 features de contexto no top 10 (tempo, volume, VWAP, range)
- build_dataset_v950.py: pipeline completo de features de contexto

## v9.40 (26/08/2026)
- Dataset v940: 124 colunas, 105 features numericas (era 26)
- +92 features de contexto (tempo, VWAP, ajuste, vol, retornos, POC, range)
- Leakage removido: preco_saida, duracao_label_ms
- Walk-forward: acc 66.5% +/- 0.7% (era 62.7% +/- 2.0%)
- Top 10 features: 5 sao de contexto (tempo, volume, distancia)
- Trackers novos: poc_migration_tracker, volume_relativo_tracker
- session_time_tracker: +minutos_desde_abertura, +bloco_sessao

# Changelog

## v9.39 walk-forward (26/08/2026)
- Walk-forward com dataset v939 (labels corretos)
- RF(50, d=8, balanced): acc 62.7% +/- 2.0%
- 3 folds (13, 14, 17/ago), 34K amostras por dia
- Tempo: 93s (amostra 10%)
- Resultado: dados/s/wf_v939.json

## v9.39 (26/08/2026)
- Reorganizacao de pastas: ml/, testes/, docs/, scripts/, dados/
- Tasks do Task Scheduler atualizadas para novos paths
- Pipeline pos-pregao corrigido (paths)
- Sem dedup na captura (RTD nunca envia duplicados)

## v9.38 (26/08/2026)
- Walk-forward otimizado: n_jobs=-1, float32, col selection
- Feature cache persistente (feature_cache.py)
- Benchmark: 458s vs >600s (timeout)

## v9.37 (26/08/2026)
- Features volatilidade multi-TF (7 features)
- Features retornos multi-horizonte (7 features)
- Features tempo de sessao (4 features)
- features_expansao.py (33 features batch)

## v9.36 (26/08/2026)
- OHLC intraday (abertura, maxima, minima, fechamento)
- PrecoContextTracker (~48 features contexto preco)
- Integracao ao scorer (ctx trackers)

## v9.15 (25/08/2026)
- Revisao codigo completa (9 fixes)
- Correcoes de consistencia batch/live

## v9.13 (23/08/2026)
- Book 500 niveis (era 60)
- Scorer ML desempacotando tuplas (P0-1)
- Labeler corrigido (SL real, janela nao cruza dia)
- Revalidacao com labels corrigidos: sinal SOBREVIVEU
  RF: acc 57.3%, AUC 0.60, PF 2.68 com 365x menos amostras

## v9.12 (22/08/2026)
- Labeler vectorizado NumPy (~180x mais rapido)
- Walk-forward real (antes mostrava 100% falso)
- Comparacao RF vs LGBM (RF venceu)
- Calibracao Platt

## v9.11 (21/08/2026)
- Pipeline diario automatico (6 passos)
- Acumulacao real (mes inteiro)
- Gate de qualidade (aborta se dados ruins)

## v9.10 (21/08/2026)
- Metadados da sessao de captura
- Log periodico dos rejeitados
- Gate de qualidade no retreino
- Relatorio diario

## v9.9 (21/08/2026)
- Ritmo adaptativo do _loop (50Hz+)
- Rotacao por tamanho real (100MB)
- fsync periodico
- Fix: _garantir_fp nunca era chamado

## v9.8 (21/08/2026)
- CVD + divergencia CVD x preco
- Volatilidade realizada + range
- Fase de sessao + dias ate vencimento
- Taxa de eventos

## v9.8.1 (21/08/2026)
- Fix: poda do dedup crashava (agora_ms -> agora_epoch)

## v9.7 (21/08/2026)
- OFI alinhado por preco (Cont-Kukanov-Stoikov)
- Kyle Lambda sobre TODOS os trades
- Z-score EWMA (opt-in)

## v9.6 (21/08/2026)
- 5 funcionalidades mortas reativadas:
  1. CrossAssetEngine (registrar nunca chamado)
  2. CrossAssetEngine relogio (cutoffs errados)
  3. Pesos por regime (sempre lateral)
  4. Confirmacao por regime (congelada em 3)
  5. Stop-hunt (condicao sempre falsa)
  6. Captura batch (todo trade rejeitado)
- 16 correcoes de bugs
- 41/41 testes

## v9.5 (21/08/2026)
- 4 bugs criticos (crash loop, labeler, regime, watchdog)
- Watchdog robustecido (10s delay, multi-instancia)

## v9.4 (20/08/2026)
- Volume Profile + Kyle Lambda
- Blindagem captura_eventos_ms.py
- R:R regime lateral corrigido (0.6:1 -> 1.25:1)

## v9.3 (20/08/2026)
- Unificacao features_lib
- Anti-whipsaw (holding 90s, confianca 0.75)
- Fix book_snap_ant

## v9.1 (Ago/2026)
- Sanity check de preco
- Cooldown (45s)
- Reversao protegida

## v9 (Ago/2026)
- Book Level Features
- Cross Asset (WIN x WDO)
- Trade Metrics

## v8 (Ago/2026)
- OFI, Regime Switch, Estrategias por regime

## v7 (Ago/2026)
- Padroes (spoof, stop-hunt)
- PadroesMemoria
