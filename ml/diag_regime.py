import pandas as pd
df = pd.read_parquet(r'D:\MarketData\mimo\dataset_final.parquet')
tot = (df['ativo'] == 'WINV26').sum()
w = df[(df['ativo'] == 'WINV26') & (df['preco_ultimo'] > 100000)].copy()
w = w.sort_values('ts_ms')
print(f'WIN snaps: {tot} | validos: {len(w)} | descartados (preco 0): {tot - len(w)}')
w['dia'] = pd.to_datetime(w['ts_ms'], unit='ms').dt.date
for dia, g in w.groupby('dia'):
    p = g['preco_ultimo']
    vol = p.max() - p.min()
    ret = (p.iloc[-1] - p.iloc[0]) / p.iloc[0] * 100
    regime = 'LATERAL' if abs(ret) < 0.5 and vol < 150 else 'TENDENCIA/VOL'
    print(f'{dia}: range={vol:.0f}pts | retorno={ret:+.2f}% | -> {regime}')