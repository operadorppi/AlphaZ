import pandas as pd
df = pd.read_parquet(r'D:\MarketData\mimo\26\dataset_final_completo.parquet', columns=['vwap', 'dist_vwap_pts', 'cruzou_vwap', 'ajuste_anterior_oficial', 'dist_ajuste_oficial_pts', 'regime_realiz_vol'])
print('Columns:', df.columns.tolist())
print('Shape:', df.shape)
