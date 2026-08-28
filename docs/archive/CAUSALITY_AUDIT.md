# CAUSALITY AUDIT v3 — Freebuff v9.14

**Data:** 23/08/2026
**Auditor:** Codebuff (automated)
**Dataset:** dataset_final_v2_win_v914.parquet
**Dias testados:** 11/08, 13/08, 14/08 (3 dias distintos)
**Raw data:** raw_negocios_ms_*.jsonl (50K eventos WIN por dia)
**Labeler:** labeler_vectorizado.py v9.14 (first-barrier-wins, 4 outcomes)

---

## Resumo

| Teste | Descrição | Dia 11 | Dia 13 | Dia 14 | Multi-dia |
|-------|-----------|--------|--------|--------|-----------|
| T1 | Labeler ref == vectorizado | ✅ | — | — | 100/100 |
| T2 | Leakage direto (X_cols) | ✅ | — | — | 26 features |
| T3 | Feature causality (snapshot) | ✅ (14 ts) | ✅ (16 ts) | ✅ (15 ts) | **PASS** |
| T4 | Replay determinístico | ✅ (19480 snaps) | ✅ (13130 snaps) | ✅ (16140 snaps) | **PASS** |
| T5 | Brute-force vs label-consumer | — | — | — | **PASS** (5 dias, 0 div) |
| T6 | Perturbação futuro (3 tipos) | ✅ (16 ts) | ✅ (13 ts) | ✅ (14 ts) | **PASS** |

**VEREDITO: PASS — Pipeline causal, determinístico e consistente em 3 dias distintos.**

---

## Detalhes por Teste

### T1: Labeler referência == vectorizado

- **Status:** PASS
- **Cenários:** 100
- **Concordância:** 100%
- Labeler core e vectorizado produzem resultados idênticos

### T2: Leakage direto

- **Status:** PASS
- **Features:** 26
- **Violações:** 0
- Nenhuma feature contém palavras de leakage

### T3: Feature causality (full vs truncated)

- **Status:** PASS (3/3 dias)
- **Método:** Para cada timestamp T, roda engine com TODOS os eventos e com apenas eventos ≤ T. Compara 26 features.
- **Divergências:** 0 em todos os dias
- **Timestamps testados:** 14-16 por dia (distribuídos uniformemente)
- **Tempo:** 25-30s por dia

### T4: Replay determinístico

- **Status:** PASS (3/3 dias)
- **Runs:** 3
- **Snapshots:** 13.130-19.480 por dia
- **Divergências:** 0

### T5: Brute-force vs label-consumer (multi-dia)

- **Status:** PASS (5 dias)
- **Dias testados:** 20669, 20670, 20671, 20672, 20675
- **Trades por dia:** 64-104
- **Divergências:** 0 em todos os dias
- **Label-consumer e brute-force produzem exatamente os mesmos trades**

### T6: Perturbação do futuro (3 tipos)

- **Status:** PASS (3/3 dias)
- **Tipos:** A (ruído), B (reordenação), C (truncamento)
- **Divergências:** 0 em todos os tipos e dias
- **Modificar eventos futuros não altera features, probabilidade, sinal ou entrada em T**

---

## Nota sobre amplitudes dos testes

O CAUSALITY AUDIT v2 (anterior) usava checkpoints que não salvavam o estado
dos trackers de Volume Profile e Kyle Lambda, causando falsos positivos.
O v3 usa execução direta (engine do zero para cada sample), que é correto
mas mais lento. As amplitudes por dia são menores (14-16 timestamps) mas
os testes são executados em 3 dias distintos, cobrindo diferentes regimes.

Ampliação futura: implementar checkpoint completo (salvar VP/Kyle trackers)
para permitir 10K+ samples por dia.

---

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| CAUSALITY_AUDIT_v3.json | Resultado estruturado |
| CAUSALITY_AUDIT.md | Este relatório |
| testes_causalidade_v3.py | Script dos 6 testes |
| METODOLOGIA_BACKTESTER.md | Especificação completa |

---

## Veredito

O pipeline está **tecnicamente validado**:
- **Causal:** features não dependem de dados futuros (3 dias, 0 divergências)
- **Determinístico:** mesmos dados = mesmos resultados (3 runs por dia)
- **Consistente:** brute-force e label-consumer concordam (5 dias, 0 divergências)
- **Robusto a perturbação:** ruído/reordenação/truncamento futuro não afeta o presente

**Próximos passos (foram do escopo deste audit):**
1. Walk-forward com purge+embargo
2. Baselines (threshold=0, aleatório, momentum)
3. Calibração (Brier, ECE)
4. Stress de custo
5. Sensibilidade do threshold
6. Forward test (dias futuros)

---

*Gerado por Codebuff em 23/08/2026*
