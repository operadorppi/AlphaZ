# Machine Learning

## Walk-Forward Otimizado
n_jobs=-1, float32, col selection. Benchmark: 458s vs >600s

## Features (top 10)
1. vp_vp_total (682) 2. vpin (510) 3. cvd_total (504) 4. preco_ultimo (440) 5. vp_poc_dist (381) 6. vp_val_dist (372) 7. vp_vah_dist (340) 8. kyle_lambda (302) 9. realized_vol_bps (236) 10. ewma_imb_longa (224)

## Resultados (v939)
Baseline: -10 pts/trade. AUC medio: 0.755

## RF vs LGBM
RF: acc 57.7%, AUC 0.616, PF 2.73 | LGBM: acc 52.2%, AUC 0.534, PF 2.19
Vencedor: RandomForest

## Ablacao
Fluxo (8 features): AUC 0.6175, PF 2.79 > Todas 29: AUC 0.6048, PF 2.63
