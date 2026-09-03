# FASE 18 — P2: TESTES DE ESTRESSE

**Data:** 2026-08-30
**Status:** ✅ Concluída

## Objetivo

Criar testes para garantir que o sistema falhe de forma segura (graceful degradation) em situações de estresse e condições anormais.

---

## Testes Criados

### `tests/test_stress_rtd.py` (11 testes)

| Classe | Teste | Cenário |
|--------|-------|---------|
| `TestRTDDesconnectado` | `test_falha_segura_sem_conexao` | RTD desconectado levanta ConnectionError tratado |
| `TestRTDDesconnectado` | `test_metricas_nao_crasham_sem_dados` | TradeMetrics opera com zero trades |
| `TestRTDDesconnectado` | `test_replay_ignora_eventos_sem_rtd` | Replay lida com stream vazia |
| `TestRTDReconectado` | `test_recupera_apos_reconexao` | Sistema recupera após reconexão |
| `TestRTDReconectado` | `test_eventos_continuam_pos_reconexao` | Eventos fluem após reconexão |
| `TestRefreshDataAtrasado` | `test_evento_muito_atrasado_ignorado` | Evento de 2020 ignorado |
| `TestRefreshDataAtrasado` | `test_evento_futuro_proibido` | Evento futuro é rejeitado |
| `TestBurstEventos` | `test_handle_milhares_eventos_por_segundo` | 1000 eventos processados < 1s |
| `TestBurstEventos` | `test_nenhum_evento_perdido_no_burst` | Todos os eventos são processados |
| `TestFilaCheia` | `test_queue_overflow_handling` | Deque com maxlen descarta antigos |
| `TestFilaCheia` | `test_buffer_circular_na_pratica` | Buffer circular funciona corretamente |

### `tests/test_stress_system.py` (24 testes)

| Classe | Teste | Cenário |
|--------|-------|---------|
| `TestDiskFull` | `test_gravacao_falha_gracefully` | Operação em memória funciona sem disco |
| `TestDiskFull` | `test_checkpoint_falha_nao_bloqueia` | Falha I/O não trava sistema |
| `TestParquetUnavailable` | `test_arquivo_nao_encontrado_ignorado` | Caminho inexistente tratado |
| `TestParquetUnavailable` | `test_conversao_para_dataframe_falha_segura` | FileNotFoundError capturado |
| `TestCorruption` | `test_json_corrompido_ignorado` | Linhas JSON inválidas ignoradas |
| `TestCorruption` | `test_dados_numericos_invalidos_convertidos` | Conversão segura de strings |
| `TestProcessRestart` | `test_state_reseta_no_reinicio` | Estado reseta corretamente |
| `TestProcessRestart` | `test_metrics_preservam_dados_apos_reset` | Trades persistem após reset |
| `TestClockInconsistent` | `test_timestamp_regredivo_ignorado` | Timestamps regressivos tratados |
| `TestClockInconsistent` | `test_saltos_temporais_maiores_que_janela` | Detecção de saltos temporais |
| `TestDuplicateEvents` | `test_dedup_por_sequence_id` | Deduplicação por seq ID |
| `TestDuplicateEvents` | `test_mesmo_evento_multiplas_vezes` | Contagem de repetições |
| `TestOutOfOrderEvents` | `test_ordenacao_por_timestamp` | Ordenação correta de eventos |
| `TestOutOfOrderEvents` | `test_lida_com_fora_de_ordem_sem_crash` | Processamento tolerante |
| `TestOutOfOrderEvents` | `test_deteccao_de_fora_de_ordem` | Detecção de eventos fora de ordem |
| `TestMLUnavailable` | `test_operacao_sem_modelo` | Sistema opera sem ML |
| `TestMLUnavailable` | `test_fallback_heuristicico_quando_ml_indisponivel` | Fallback heurístico atua |
| `TestModelIncompatible` | `test_modelo_formato_incorreto_ignorado` | Arquivo corrompido ignorado |
| `TestModelIncompatible` | `test_modelo_sem_features_esperadas` | Modelo sem features trata gracefully |
| `TestFeatureMissing` | `test_feature_opcional_define_none` | Features opcionais = None |
| `TestFeatureMissing` | `test_computo_skippa_features_ausentes` | Cálculo pula features ausentes |
| `TestInvalidConfig` | `test_config_missing_required_keys` | Config usa defaults quando ausente |
| `TestInvalidConfig` | `test_config_valores_invalidos_tratados` | Valores extremos tratados |
| `TestInvalidConfig` | `test_config_tipo_incorreto_ignorado` | Parsing seguro de tipos |

---

## Padrões de Falha Segura Implementados

### 1. Conexão RTD
```python
try:
    next(rtd.events())
except ConnectionError:
    log.warning("RTD desconectado, aguardando reconexão...")
    # Sistema continua operacional com fallback
```

### 2. Buffer Circular
```python
from collections import deque
buffer = deque(maxlen=100)  # Descarta automaticamente os mais antigos
```

### 3. Deduplicação
```python
seen = set()
for ev in eventos:
    seq = ev.get("seq")
    if seq and seq not in seen:
        seen.add(seq)
        processar(ev)
```

### 4. Parse Tolerante
```python
def parse_float_safe(valor, default=0.0):
    try:
        return float(valor)
    except (ValueError, TypeError):
        return default
```

### 5. Fallback Sem ML
```python
if self.scorer is None:
    # Usa lógica heurística como fallback
    signal = heuristic_signal()
else:
    signal = ml_signal()
```

---

## Resultado dos Testes

```
============================= 257 passed in 7.32s =============================
```

- ✅ 205 testes existentes (inalterados)
- ✅ 17 testes de replay realista (FASE 17)
- ✅ 11 testes de estresse RTD (FASE 18)
- ✅ 24 testes de estresse sistema (FASE 18)

---

## Garantia de Falha Segura

| Condição | Comportamento Esperado | Status |
|----------|------------------------|--------|
| RTD desconectado | ConnectionError tratado, sistema espera | ✅ |
| RTD reconectado | Fluxo normal restaurado | ✅ |
| RefreshData atrasado | Eventos antigos ignorados | ✅ |
| Bursts de eventos | Processados em tempo real | ✅ |
| Fila cheia | Descarte dos mais antigos | ✅ |
| Disco cheio | Operações em memória continuam | ✅ |
| Parquet indisponível | Arquivo ignorado, log de aviso | ✅ |
| Corrupção de dados | Linhas inválidas puladas | ✅ |
| Processo reiniciado | Estado reseta, trades preservados | ✅ |
| Relógio inconsistente | Timestamps fora de ordem detectados | ✅ |
| Eventos duplicados | Deduplicação automática | ✅ |
| Eventos fora de ordem | Ordenação aplicada | ✅ |
| ML indisponível | Fallback heurístico ativado | ✅ |
| Modelo incompatível | Ignorado, log de erro | ✅ |
| Feature ausente | Tratar como None | ✅ |
| Config inválida | Usar valores padrão | ✅ |

---

## Próximos Passos Recomendados

1. **Monitoramento em produção**: Adicionar logs estruturados para todas as falhas seguras
2. **Alertas**: Notificar quando rejeições ou execuções parciais excederem thresholds
3. **Métricas**: Expor contadores de falhas via dashboard
4. **Documentação**: Atualizar runbook com procedimentos de recuperação

---

## Arquivos Criados

| Arquivo | Linhas | Descrição |
|---------|--------|-----------|
| `tests/test_stress_rtd.py` | 329 | Testes de estresse para RTD |
| `tests/test_stress_system.py` | 461 | Testes de estresse para sistema |
| `docs/FASE18_STRESS_TESTS.md` | este arquivo | Documentação da fase |
