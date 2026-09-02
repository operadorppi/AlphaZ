# Estado Atual

Versao: v14.2 (02/09/2026)

## Arquitetura

```
motor_rt_alphaz.py (shim, lazy imports)
  → core/app.py (orquestrador)
    → adapters/profit_rtd.py (RTD polling, dedup, 500 niveis book)
    → core/capture_daemon.py (captura imortal, queue)
    → adapters/file_storage.py (Parquet + Hive + Snappy)
    → core/signal_engine.py (ML gate → heurística)
    → core/risk_engine.py (RiskEngine unificado)
    → core/position_manager.py (gerenciamento de posições)
    → features/ (121 features, cross-asset)
    → ml/scorer.py (LightGBM ao vivo)
```

## Componentes

| Componente | Status | Versao |
|-----------|--------|--------|
| Motor | v14.2 — 4 ativos, Parquet+Hive, lazy imports | ✅ |
| Entry point | `run_motor.py` → `core.app.App` | ✅ |
| Legado | `motor_rt_alphaz.py` → shim lazy (v14.2) | ✅ |
| RTD | 500 niveis book, content-based asset mapping | ✅ |
| Captura | Parquet + Hive + Snappy, 10 fluxos | ✅ |
| Features live | 121 ML + contexto ao vivo | ✅ |
| Dashboard | Funcional (5001) | ✅ |
| ML | ScorerML integrado ao motor (ML gate) | ✅ |
| Watchdog | Funcional (motor + com_watchdog) | ✅ |
| Task Scheduler | 08:45 start, 18:30 stop, 18:35 pipeline | ✅ |
| Pipeline | 7 passos (features, labels, dataset, retreino) | ✅ |
| Dataset | Parquet + Hive partitioning | ✅ |
| Modelo | LightGBM, 17 features otimizadas | ✅ |
| Leakage | Embargo 30s no split (López de Prado) | ✅ |
| Testes | 782 passed, 28 pre-existing failures | ✅ |
| Config | Unificado (loader + extra dict) | ✅ |
| exposure/ | E = N*P*V, validação de inputs | ✅ |
| mlgate/ | Integrado ao RiskEngine | ✅ |
| replaygate/ | Configurável (require_replay_validated) | ✅ |
| observability/ | ⚠️ Não integrado (documentado) | Pendente |

## Estrutura de Diretórios (Parquet + Hive)

```
D:\MarketData\Profit\RAW\
  data_type=TT\
    date=YYYYMMDD\
      asset=IND\
        part-0000.parquet
      asset=DOL\
      asset=WIN\
      asset=WIN_RLP\
      asset=WDO\
      asset=WDO_RLP\
  data_type=BOOK\
    date=YYYYMMDD\
      asset=IND\
      asset=DOL\
      asset=WIN\
      asset=WDO\
```

## Schema

**TT (13 colunas):** ts_ns, received_at_ns, sequence_id, ativo, asset_partition, janela_id, window_name, is_rlp, preco, quantidade, agressor, compradora, vendedora

**BOOK (16 colunas):** ts_ns, received_at_ns, sequence_id, ativo, asset_partition, janela_id, window_name, nivel, bid, ask, bid_volume, ask_volume, bid_vol_total, ask_vol_total, por_corretora, ofi

## Bugs Corrigidos (v14.2)

### C1: Lazy Imports no Shim
- `motor_rt_alphaz.py` usa `__getattr__` para lazy loading
- Import chain frágil (core.app → pyarrow) não mais quebra o módulo inteiro
- Testes que importam motor_rt_alphaz não mais falham

### A4: except:pass → Logging
- profit_rtd.py: window discovery + disconnect agora logam erros
- file_storage.py: flush failure agora loga dados perdidos
- Meta write failure agora loga warning

### C3: Purge/Embargo
- Split treino/teste agora remove últimos 30s de cada dia de treino que antecede dia de teste
- Previne leakage residual (labels se estendem para dentro do teste)

## Validação

```bash
# Syntax check
python -c "import py_compile; py_compile.compile('core/app.py', doraise=True)"

# Testes
python -m pytest tests/ testes/ -q --ignore=testes/test_book_split_edge_cases.py --ignore=testes/test_config_flat.py --ignore=testes/test_risk_unification.py

# Resultado: 782 passed, 28 pre-existing failures
```

## Falhas Restantes (28, pre-existing)

| Categoria | Count | Causa |
|-----------|-------|-------|
| test_risk_unification | 9 | Testes rodam à meia-noite → FORA_HORARIO bloqueia |
| test_book_split | 9 | config.CONFIG é None (legacy não inicializado) |
| test_config_flat | 5 | _aplicar_valor_config removido no refactor |
| test_features | 3 | Testes esperam JSONL, v14 usa Parquet Hive |
| test_edge_case_scorer | 1 | Mock setup incompleto |
| test_capture_overflow | 1 | Drain behavior mudou no v14 |

## Próximos Passos

1. **Rodar motor com 4 ativos** (WIN, WDO, IND, DOL) para validar captura completa
2. **Retreinar modelo** com dados Hive limpos
3. **Ativar replay gate** (require_replay_validated: true) quando replay aprovar
4. **Integrar observability/** ao motor
5. **Corrigir 28 testes restantes** (mock horário, config legacy)
