# Auditoria Documental — Freebuff

**Data:** 26/08/2026
**Escopo:** Comparação entre documentação existente e código-fonte real.
**Metodologia:** Inspeção direta do código + comparação com DOCUMENTACAO.md + execução de testes.

---

## 1. Arquivos Documentados vs. Existentes

### 1.1 Arquivos documentados que existem

| Arquivo | Documentado em | Status |
|---------|---------------|--------|
| `motor_rt_alphaz.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (4155 linhas) |
| `motor_web.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (2586 linhas) |
| `features_lib.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (982 linhas) |
| `scorer.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (314 linhas) |
| `captura_eventos_ms.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (239 linhas) |
| `watchdog.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (391 linhas) |
| `config.py` | DOCUMENTACAO.md | ✅ Existe (105 linhas) |
| `config.json` | DOCUMENTACAO.md | ✅ Existe |
| `treino_lib.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (207 linhas) |
| `preco_context_tracker.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (125 linhas) |
| `volatility_tracker.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (26 linhas) |
| `returns_tracker.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (26 linhas) |
| `session_time_tracker.py` | DOCUMENTACAO.md (Referencia completa) | ✅ Existe (28 linhas) |
| `dashboard_pro.html` | DOCUMENTACAO.md | ✅ Existe (5001) |
| `labeler_vectorizado.py` | DOCUMENTACAO.md | ⚠️ Documentado como "labeler.py" (nome antigo) |
| `walk_forward_otimizado.py` | DOCUMENTACAO.md (v9.38) | ✅ Existe |
| `feature_cache.py` | DOCUMENTACAO.md (v9.38) | ✅ Existe |

### 1.2 Arquivos existentes sem documentação detalhada

| Arquivo | Tipo | Módulo | Observação |
|---------|------|--------|------------|
| `ml/features_contexto_preco.py` | .py | ml | ~48 features de contexto |
| `ml/features_contexto_avancado.py` | .py | ml | VWAP + ajuste oficial |
| `ml/features_expansao.py` | .py | ml | 33 features batch |
| `ml/calcular_ajuste_diario.py` | .py | ml | Ajuste B3 |
| `ml/calcular_vwap_diaria.py` | .py | ml | VWAP diária |
| `ml/integrar_base.py` | .py | ml | Pipeline integração |
| `ml/dataset_builder.py` | .py | ml | Montagem dataset |
| `ml/batch_processor.py` | .py | ml | Processamento batch |
| `ml/ablation_test.py` | .py | ml | Teste ablacao |
| `ml/analise_contextual_completa.py` | .py | ml | Analise contextual |
| `ml/analise_redundancia.py` | .py | ml | Analise redundancia |
| `ml/validacao_rigorosa.py` | .py | ml | Validacao rigorosa |
| `ml/validar_contexto_preco.py` | .py | ml | Validacao contexto |
| `ml/validar_v914.py` | .py | ml | Validacao v914 |
| `ml/retreinar_lgbm_limpo.py` | .py | ml | Retreino limpo |
| `ml/calibrar_modelo.py` | .py | ml | Calibracao Platt |
| `ml/lightgbm_tune.py` | .py | ml | Hyperparameter tuning |
| `ml/labelar_offline.py` | .py | ml | Wrapper labeler |
| `ml/replay_temporal.py` | .py | ml | Replay temporal |
| `ml/diag_regime.py` | .py | ml | Diagnostico regime |
| `ml/analisar_features.py` | .py | ml | Analise features |
| `ml/batch_historico.py` | .py | ml | Batch historico |
| `ml/importar_historico.py` | .py | ml | Importacao historico |
| `ml/walk_forward.py` | .py | ml | Walk-forward original |
| `ml/walk_forward_completo.py` | .py | ml | Walk-forward completo |
| `ml/walk_forward_v914_limpo.py` | .py | ml | Walk-forward limpo |
| `scripts/atualizar_documentacao.py` | .py | scripts | Gerador de docs |
| `scripts/observability.py` | .py | scripts | Structured logging |
| `scripts/servidor_proxy_dashboard.py` | .py | scripts | Proxy dashboard |
| `scripts/pipeline_diario.py` | .py | scripts | Pipeline diario |
| `scripts/relatorio_diario.py` | .py | scripts | Relatorio diario |

### 1.3 Referências a arquivos inexistentes

| Referência no doc | Arquivo real | Status |
|-------------------|-------------|--------|
| `labeler.py` (DOCUMENTACAO.md) | `ml/labeler_vectorizado.py` | ⚠️ Nome desatualizado |
| `iniciar_watchdog.bat` (DOCUMENTACAO.md) | `scripts/iniciar_motor.bat` | ⚠️ Nome desatualizado |
| `auto_retreinar.bat` (DOCUMENTACAO.md) | Não existe | ❌ Referência órfã |
| `retreinar_sem_leak.py` (DOCUMENTACAO.md) | `ml/retreinar_lgbm_limpo.py` | ⚠️ Nome desatualizado |
| `smoke_test_v96.py` (DOCUMENTACAO.md) | Não existe | ❌ Referência órfã |

---

## 2. Funções Mencionadas mas Não Encontradas

| Função | Documentada em | Status |
|--------|---------------|--------|
| `get_learning()` | DOCUMENTACAO.md (v9.6) | ✅ Removida (substituída por `get_estatisticas()`) |
| `labeler.py::label()` | DOCUMENTACAO.md | ⚠️ Agora é `labeler_vectorizado.py::label_vectorizado()` |

---

## 3. Endpoints Mencionados mas Não Encontrados

| Endpoint | Documentado em | Status |
|----------|---------------|--------|
| `/api/features` | DOCUMENTACAO.md | ✅ Existe |
| `/api/sinais` | DOCUMENTACAO.md | ✅ Existe |
| `/api/posicao` | DOCUMENTACAO.md | ✅ Existe |
| `/api/book_level` | DOCUMENTACAO.md | ✅ Existe |
| `/api/memoria` | DOCUMENTACAO.md | ✅ Existe |
| `/api/metricas` | DOCUMENTACAO.md | ✅ Existe |
| `/api/saldo_corretoras` | DOCUMENTACAO.md | ✅ Existe |
| `/api/rtd_health` | DOCUMENTACAO.md | ✅ Existe |
| `/api/padroes` | DOCUMENTACAO.md | ✅ Existe |
| `/api/learning` | DOCUMENTACAO.md | ✅ Existe |
| `/api/contexto` | DOCUMENTACAO.md | ⚠️ Não verificado no código |
| `/health` | DOCUMENTACAO.md | ✅ Existe |

---

## 4. Parâmetros Mencionados mas Não Encontrados

| Parâmetro | Documentado em | Status |
|-----------|---------------|--------|
| `max_holding_s: 0` | config.json | ⚠️ Default 0 = sem timeout (labeler usa 30s como fallback) |
| `normalizar_score` | config.json | ✅ Existe (default false) |
| `desligar_horarios_ruins` | config.json | ✅ Existe (default false) |

---

## 5. Conflitos de Versão

| Conflito | Detalhe |
|----------|---------|
| `labeler.py` vs `labeler_vectorizado.py` | DOCUMENTACAO.md menciona ambos; o real é o vectorizado |
| `walk_forward.py` vs `walk_forward_otimizado.py` | Ambos existem; otimizado é o novo |
| `retreinar_sem_leak.py` vs `retreinar_lgbm_limpo.py` | Nome mudou, docs não atualizados |
| v9.36-v9.39 vs v9.15 | Versões novas documentadas mas com menos detalhe que v9.15 |

---

## 6. Conflitos de Configuração

| Conflito | Detalhe |
|----------|---------|
| `max_holding_s: 0` | Config diz 0, labeler usa 30 como fallback — comportamento implícito |
| Task Scheduler path | Apontava para raiz, bat movido para scripts/ — CORRIGIDO em 26/08 |

---

## 7. Riscos

| Risco | Severidade | Detalhe |
|-------|-----------|---------|
| Labels corrompidos no dataset | 🔴 CRÍTICO | `labels_WINV26_4-17_final.jsonl` misturava WIN/WDO — CORRIGIDO |
| Task Scheduler com path errado | 🟠 ALTO | Pipeline não rodava — CORRIGIDO |
| `mask_valido` bug no labeler | 🟠 ALTO | Labeler crashava com dados misturados — CORRIGIDO |
| Documentação desatualizada | 🟡 MÉDIO | 30+ arquivos ML sem docs detalhadas |
| Referências órfãs | 🟡 MÉDIO | 3 arquivos referenciados não existem |
| Modelo treinado com dados corrompidos | 🔴 CRÍTICO | Precisa retreino — PENDENTE |

---

## 8. Recomendações

| # | Prioridade | Recomendação |
|---|-----------|--------------|
| 1 | 🔴 CRÍTICO | Retreinar LightGBM com dataset v939 (labels corretos) |
| 2 | 🟠 ALTO | Atualizar nomes na DOCUMENTACAO.md (labeler.py → labeler_vectorizado.py) |
| 3 | 🟠 ALTO | Remover referências órfãs (auto_retreinar.bat, smoke_test_v96.py) |
| 4 | 🟡 MÉDIO | Documentar módulos ML (30 arquivos) |
| 5 | 🟡 MÉDIO | Verificar endpoint /api/contexto |
| 6 | 🟢 BAIXO | Adicionar testes para os módulos ML |

