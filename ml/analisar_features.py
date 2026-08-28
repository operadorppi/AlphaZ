import pandas as pd
df = pd.read_parquet(r'D:\MarketData\mimo\dataset_final.parquet')
df = df[df['ativo'] == 'WINV26']
y = df['label']
X = df[y != 0]
yy = X['label']
num = X.select_dtypes(include=['number']).drop(columns=['label', 'ts_ms'], errors='ignore')
print(f'linhas: {len(df)} | com label: {len(X)} | alta(1): {(yy==1).sum()} | baixa(-1): {(yy==-1).sum()}')
rows = []
for c in num.columns:
    a, b = X.loc[yy==1, c], X.loc[yy==-1, c]
    if a.count() < 100 or b.count() < 100: continue
    sa = a.std() or 1
    sep = (a.mean() - b.mean()) / sa
    rows.append((c, sep))
rows.sort(key=lambda r: -abs(r[1]))
print('\nTop features que ANTECEDEM o movimento (sep em desvios):')
for c, sep in rows[:25]:
    print(f'  {c:28s} {sep:+.3f}')
print('\n( |sep| > 0.1 = sinal útil; ~0 = não prediz )')