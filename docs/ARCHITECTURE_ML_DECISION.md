# Decisão Arquitetural: ML vs Heurística (v11.13)

## Contexto

O signal engine tem dois sistemas de scoring:
1. **ML (ScorerML)** — LightGBM com 17 features, AUC 0.648
2. **Heurística (PESOS_INICIAIS)** — 30+ pesos arbitrários, nenhum validado estatisticamente

## Dados

| Métrica | ML | Heurística |
|---------|-----|------------|
| AUC | 0.648 | N/A (não medido) |
| ECE | 0.263 | N/A |
| Features | 17 (VP, CVD, book) | 30+ (overlap com ML) |
| Validado | Walk-forward | Nunca |

## Decisão

**Não matar a heurística ainda.** Razões:

1. **ML mal calibrado** (ECE=0.263) — probabilidades não são confiáveis para threshold
2. **Heurística gera direção** — mesmo com pesos arbitrários, `aggr_imb` é um sinal direcional válido
3. **Overlap** — VP e CVD dominam ambos, mas a heurística tem features que o ML não tem (book level, spoof, etc)

## Arquitetura atual (v11.13)

```
ML gate (threshold calibrado por regime)
  → decide QUANDO operar
  → peso dinâmico baseado no ECE:
    ECE > 0.15: ml_weight=0.3 (heurística domina)
    ECE 0.05-0.15: ml_weight=0.5 (igual)
    ECE < 0.05: ml_weight=0.7 (ML domina)

Heurística
  → decide QUEM (direção)
  → confirma ou discorda do ML
```

## Quando matar a heurística

Quando:
- ECE < 0.05 (probabilidades calibradas)
- AUC > 0.70 (discriminação boa)
- Replay com ML-sozinho mostra edge positivo

Até lá: ML como gate, heurística como confirmação, peso dinâmico.
