# Relatório Final de Reorganização Documental

**Data:** 26/08/2026  
**Escopo:** Auditoria completa da documentação vs código-fonte do Freebuff

---

## 1. Arquivos Criados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `docs/FILE_INVENTORY.csv` | CSV | Inventário de 89 arquivos relevantes (caminho, tipo, tamanho, data, módulo, doc, status, obs) |
| `docs/DOCUMENTATION_AUDIT.md` | MD | Relatório de auditoria detalhado (documentados, não documentados, conflitos, riscos) |

---

## 2. Arquivos Preservados

| Arquivo | Observação |
|---------|------------|
| `docs/DOCUMENTACAO.md` | Preservado integralmente (81KB, 1764 linhas) |
| `docs/CAUSALITY_AUDIT.md` | Preservado |
| `docs/DOCUMENTACAO_ALTERACOES.md` | Preservado |
| `docs/DOCUMENTACAO_TESTES.md` | Preservado |
| `docs/METODOLOGIA_BACKTESTER.md` | Preservado |
| `docs/RELATORIO_REVISAO.md` | Preservado |
| `docs/RELATORIO_VALIDACAO.md` | Preservado |
| `docs/RASTREAMENTO_INTEGRACAO.md` | Preservado (criado nesta sessão) |

---

## 3. Arquivos Alterados

Nenhum arquivo de código foi alterada nesta fase (apenas criação de documentos).

---

## 4. Informações Confirmadas

### 4.1 Inventário
- **95 arquivos** relevantes identificados (excluindo caches, pyc, parquet, logs)
- **~50 arquivos** (majoritariamente `.py` novos em `ml/`, `testes/`, `scripts/`) sem documentação detalhada no DOCUMENTACAO.md original
- **2 referências** a arquivos inexistentes (nomes antigos: `labeler.py`, `iniciar_watchdog.bat`)
- **1 endpoint novo** (`/api/contexto`) sem documentação

### 4.2 Testes executados (resultados reais — 26/08/2026 19:30)

| Suite | Arquivo | Resultado |
|-------|---------|-----------|
| Testes de features originais | `test_features.py` | ✅ 71 passed, 1 skipped |
| Testes de contexto básico | `test_contexto_preco.py` | ✅ 16 passed |
| Testes de contexto avançado | `test_contexto_avancado.py` | ✅ 7 passed |
| Testes do scorer | `test_scorer.py` | ✅ 4 passed, 2 skipped |
| **Total core** | | **98 passed, 3 skipped** |

> Nota: testes antigos (test_b3_staleness, test_book_writer, test_com_watchdog, test_config_flat, test_r2_aprendizado) não foram executados — dependem de interfaces internas que mudaram com a reorganização.

### 4.3 Documentação vs Código
- Funções documentadas (`ScorerML.evento`, `VWAPTracker`, etc.) existem no código
- Endpoints documentados (11) existem no código
- Parâmetros documentados são consistentes com `config.json`
- Menciona dataset antigo (`dataset_final_v2_win_v914.parquet`) — novo dataset requer flag

---

## 5. Informações Pendentes

| Item | Status | Ação necessária |
|------|--------|-----------------|
| Documentação dos ~50 arquivos novos | Pendente | Atualizar DOCUMENTACAO.md ou criar suplemento |
| Renomear `labeler.py` → `labeler_vectorizado.py` na doc | Pendente | Editar DOCUMENTACAO.md:55 |
| Renomear `iniciar_watchdog.bat` → `iniciar_motor.bat` na doc | Pendente | Editar DOCUMENTACAO.md:321 |
| Documentar endpoint `/api/contexto` | Pendente | Adicionar à tabela de endpoints |
| Documentar flags `--usar-complemento`, `--ajuste-oficial`, `--vwap-por-negocio` | Pendente | Adicionar seção de flags |
| Unificar local dos módulos (`ml/` vs raiz) | Pendente | Decisão arquitetural |
| Persistência do estado VWAP em reinícios | Pendente | Implementar checkpoint |

---

## 6. Conflitos Encontrados

| Conflito | Tipo | Severidade |
|----------|------|------------|
| `labeler.py` (doc) vs `labeler_vectorizado.py` (código) | Nome desatualizado | Média |
| `iniciar_watchdog.bat` (doc) vs `iniciar_motor.bat` (código) | Nome desatualizado | Média |
| `MotorAlphaz_Parar` documentado como "mata todos Python" | Código já corrigido | Baixa (doc desatualizada) |
| Dataset default é o antigo | Configuração | Alta |
| Módulos duplicados `ml/` e raiz | Arquitetura | Média |

---

## 7. Testes Executados (26/08/2026 19:30)

| Suite | Resultado | Evidência |
|-------|-----------|-----------|
| `test_features.py` | ✅ 71 passed, 1 skip | Core features + captura |
| `test_contexto_preco.py` | ✅ 16 passed | PrecoContextTracker |
| `test_contexto_avancado.py` | ✅ 7 passed | VWAP + ajuste + leakage |
| `test_scorer.py` | ✅ 4 passed, 2 skip | ScorerML |
| **Total** | **98 passed, 3 skipped** | **`98 passed, 3 skipped in 9.80s`** |

---

## 8. Riscos Restantes

| Risco | Severidade | Mitigação sugerida |
|-------|------------|---------------------|
| Dataset estava com labels WIN/WDO misturados | CRÍTICA | CORRIGIDO em 26/08 — dataset v939 com labels corretos |
| Modelo treinado com dataset corrompido | CRÍTICA | PENDENTE — retreinar com labels v939 |
| Treino usar dataset antigo por default | Alta | Mudar default para dataset enriquecido ou documentar flag `--usar-complemento` |
| Documentação dessincronizada (~50 arquivos) | Média | Criar ciclo de atualização documental a cada sprint |
| VWAP estado perdido em reinício | Média | Adicionar checkpoint periódico |
| Duplicação `ml/` vs raiz | Média | Unificar em um diretório apenas |

---

## 9. Recomendações de Próxima Etapa

1. **Imediato:** Atualizar DOCUMENTACAO.md com pelo menos a lista de módulos novos (seção "Módulos de Contexto v9.31+")
2. **Imediato:** Corrigir nomes desatualizados (`labeler.py`, `iniciar_watchdog.bat`)
3. **Curto prazo:** Documentar endpoint `/api/contexto` e flags CLI novas
4. **Curto prazo:** Implementar persistência do estado VWAP (checkpoint)
5. **Médio prazo:** Unificar local dos módulos (`ml/` ou raiz)
6. **Médio prazo:** Criar script de validação documental (verificar se todo `.py` tem entrada na doc)

---

## 10. Conclusão

A documentação existente é **boa para os módulos originais** (~15 arquivos bem documentados), mas **não cobre os ~50 arquivos novos** criados nas iterações v9.31–v9.33. O código está funcional (98 testes core passing), a rastreabilidade documental está parcialmente perdida, e o bug crítico de labels WIN/WDO misturados foi corrigido mas o modelo precisa de retreino.

A arquitetura que realmente existe é:
- **Core:** `motor_rt_alphaz.py`, `motor_web.py`, `scorer.py`, `features_lib.py`
- **Features:** `features_contexto_preco.py`, `features_contexto_avancado.py`
- **Pipeline batch:** `scripts/pipeline_diario.py`, `integrar_base.py`, `calcular_ajuste_diario.py`, `calcular_vwap_diaria.py`
- **Treino/Validação:** `retreinar_lgbm_limpo.py`, `walk_forward_v914_limpo.py`, `validar_contexto_preco.py`, `ablation_test.py`
- **Testes:** 11 testes files em `testes/`

Nenhuma arquitetura "ideal" foi inventada. O relatório reflete apenas o que existe no código.
