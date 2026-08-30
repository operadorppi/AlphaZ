
import pandas as pd, numpy as np, lightgbm as lgb, warnings, time
from sklearn.metrics import roc_auc_score
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
warnings.filterwarnings('ignore')
print('='*70)
print('  ANALISE CONTEXTUAL COMPLETA - Freebuff v9.37')
print('='*70)
t0 = time.time()
print('[1/6] Carregando dataset...')
PF = 'D:/MarketData/mimo/dataset_final.parquet'  # v12.1: pipeline multi-ativo
df = pd.read_parquet(PF)
# Amostra para viabilidade
df = df.sample(200000, random_state=42)
print(f'  Shape: {df.shape}')
EXCL = ['label','ts_ms','outcome','duracao_label_ms','duracao_ms','preco_entrada','preco_saida','sl_atingido','tp_atingido','ativo','fase_sessao']
MICRO = [c for c in df.columns if c not in EXCL and df[c].dtype in ['float64','int64']]
print(f'  Micro features: {len(MICRO)}')
print('[2/6] Adicionando contexto...')
from features_contexto_preco import adicionar_contexto_preco
from features_expansao import adicionar_expansao
df = adicionar_contexto_preco(df)
df = adicionar_expansao(df)
CTX = [c for c in df.columns if c not in MICRO + EXCL and df[c].dtype in ['float64','int64']]
print(f'  Contexto features: {len(CTX)}')
ALL = MICRO + CTX
df[ALL] = df[ALL].fillna(0).replace([np.inf, -np.inf], 0)
df['y_bin'] = (df['label'] == 1).astype(int)
hit_g = df['y_bin'].mean() * 100
print(f'  Hit global: {hit_g:.1f}%')
print('[3/6] Analise contextual...')
ctx_defs = {
    'vs_vwap': lambda d: np.where(d.get('dist_vwap_pts', 0) > 0, 'acima', 'abaixo'),
    'vs_ajuste': lambda d: np.where(d.get('dist_ajuste_pts', 0) > 0, 'acima', 'abaixo'),
    'vs_poc': lambda d: np.where(d.get('vp_poc_dist', 0) < 0, 'acima', 'abaixo'),
    'vol': lambda d: pd.qcut(d['realized_vol_bps'].clip(0, 100), 3, labels=['baixa','media','alta'], duplicates='drop'),
}
rows = []
for feat in ['aggr_imb', 'cvd_total', 'ewma_imb_curta', 'range_vol_bps', 'vp_poc_dist']:
    if feat not in df.columns: continue
    for cn, cf in ctx_defs.items():
        try: df['_ctx'] = cf(df)
        except: continue
        for v in df['_ctx'].dropna().unique():
            s = df[df['_ctx'] == v]
            if len(s) < 1000: continue
            y = s['y_bin'].values
            h = y.mean() * 100
            try: a = roc_auc_score(y, s[feat].values) if len(np.unique(y)) > 1 else 0.5
            except: a = 0.5
            rows.append({'feat': feat, 'ctx': cn, 'val': str(v), 'n': len(s), 'hit': round(h,2), 'auc': round(a,4), 'delta': round(h - hit_g, 2)})
dfc = pd.DataFrame(rows)
if len(dfc) > 0:
    print()
    print('  TOP FAVORAVEIS:')
    for _, r in dfc.nlargest(8, 'delta').iterrows():
        print(f'    {r[chr(102)+chr(101)+chr(97)+chr(116)]:20s} {r[chr(99)+chr(116)+chr(120)]:12s} {r[chr(118)+chr(97)+chr(108)]:10s} N={r[chr(110)]:>8} Hit={r[chr(104)+chr(105)+chr(116)]:.1f}% AUC={r[chr(97)+chr(117)+chr(99)]:.4f} D={r[chr(100)+chr(101)+chr(108)+chr(116)+chr(97)]:+.1f}%')
    print()
    print('  TOP DESFAVORAVEIS:')
    for _, r in dfc.nsmallest(8, 'delta').iterrows():
        print(f'    {r[chr(102)+chr(101)+chr(97)+chr(116)]:20s} {r[chr(99)+chr(116)+chr(120)]:12s} {r[chr(118)+chr(97)+chr(108)]:10s} N={r[chr(110)]:>8} Hit={r[chr(104)+chr(105)+chr(116)]:.1f}% AUC={r[chr(97)+chr(117)+chr(99)]:.4f} D={r[chr(100)+chr(101)+chr(108)+chr(116)+chr(97)]:+.1f}%')
print('[4/6] Walk-forward...')
df['_dia'] = pd.to_datetime(df['ts_ms'], unit='ms').dt.date
dias = sorted(df['_dia'].unique())
print(f'  Dias: {len(dias)}')
def wf(dframe, feats, ns=4):
    res = []
    for i in range(ns, len(dias)):
        tr = dframe[dframe['_dia'].isin(dias[:i])]
        te = dframe[dframe['_dia'] == dias[i]]
        if len(tr) < 1000 or len(te) < 100: continue
        Xtr, ytr = tr[feats].values, tr['y_bin'].values
        Xte, yte = te[feats].values, te['y_bin'].values
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2: continue
        m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_samples=50, verbose=-1, random_state=42)
        m.fit(Xtr, ytr)
        p = m.predict_proba(Xte)[:,1]
        pr = (p > 0.55).astype(int)
        try: au = roc_auc_score(yte, p)
        except: au = 0.5
        hi = (pr == yte).mean() * 100 if pr.sum() > 0 else 0
        nt = pr.sum()
        w = ((pr == 1) & (yte == 1)).sum()
        lo = ((pr == 1) & (yte == 0)).sum()
        res.append({'dia': str(dias[i]), 'auc': au, 'hit': hi, 'n': nt, 'pf': w/max(lo,1)})
    return pd.DataFrame(res)
wfa = wf(df, MICRO)
wfb = wf(df, MICRO + CTX)
ICOLS = []
for m in ['aggr_imb', 'cvd_total']:
    for c in ['dist_vwap_pts', 'dist_ajuste_pts', 'vp_poc_dist']:
        if m in df.columns and c in df.columns:
            col = f'{m}_x_{c}'
            df[col] = df[m] * df[c]
            ICOLS.append(col)
wfc = wf(df, MICRO + CTX + ICOLS)
print()
print('  RESULTADO WALK-FORWARD:')
hdr = f'  {chr(77)+chr(111)+chr(100)+chr(101)+chr(108)+chr(111):25s} | {chr(65)+chr(85)+chr(67):>6s} | {chr(72)+chr(105)+chr(116)+chr(37):>6s} | {chr(84)+chr(114)+chr(97)+chr(100)+chr(101)+chr(115):>7s} | {chr(80)+chr(70):>5s}'
print(hdr)
print('  ' + '-'*60)
for n, w in [('A) Baseline micro', wfa), ('B) Micro+contexto', wfb), ('C) Micro+ctx+inter', wfc)]:
    if len(w) > 0:
        print(f'  {n:25s} | {w[chr(97)+chr(117)+chr(99)].mean():.4f} | {w[chr(104)+chr(105)+chr(116)].mean():.1f} | {w[chr(110)].mean():7.0f} | {w[chr(112)+chr(102)].mean():.2f}')
    else:
        print(f'  {n:25s} | (sem dados)')
print('[5/6] Regimes...')
rf = ['realized_vol_bps', 'range_vol_bps', 'aggr_imb', 'cvd_total', 'ewma_imb_curta', 'vol_total', 'taxa_eventos']
rf = [c for c in rf if c in df.columns]
Xr = df[rf].fillna(0).replace([np.inf, -np.inf], 0).values
Xrs = StandardScaler().fit_transform(Xr)
df['regime'] = KMeans(4, random_state=42, n_init=10).fit_predict(Xrs)
print()
hdr2 = f'  {chr(82)+chr(101)+chr(103)+chr(105)+chr(109)+chr(101):8s} | {chr(78):>10s} | {chr(70)+chr(114)+chr(101)+chr(113):>5s} | {chr(86)+chr(111)+chr(108):>7s} | {chr(72)+chr(105)+chr(116)+chr(37):>5s} | {chr(80)+chr(70):>5s}'
print(hdr2)
print('  ' + '-'*55)
for r in sorted(df['regime'].unique()):
    s = df[df['regime'] == r]
    v = s['realized_vol_bps'].mean() if 'realized_vol_bps' in s.columns else 0
    h = s['y_bin'].mean() * 100
    tp = (s['label'] == 1).sum()
    ot = (s['label'] != 1).sum()
    print(f'  Regime {r} | {len(s):>10} | {len(s)/len(df)*100:4.1f}% | {v:7.2f} | {h:5.1f}% | {tp/max(ot,1):.2f}')
print('[6/6] Feature importance...')
Xa = df[MICRO + CTX].fillna(0).replace([np.inf, -np.inf], 0).values
mf = lgb.LGBMClassifier(n_estimators=300, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_samples=50, verbose=-1, random_state=42)
mf.fit(Xa, df['y_bin'].values)
imp = pd.Series(mf.feature_importances_, index=MICRO + CTX).sort_values(ascending=False)
print()
print('  TOP 15 FEATURES:')
for i, (ft, sc) in enumerate(imp.head(15).items()):
    t = 'MICRO' if ft in MICRO else 'CTX'
    print(f'  {i+1:2d}. {ft:35s} {sc:6d}  [{t}]')
print()
sep = '='*70
print(sep)
elapsed = time.time()-t0
print(f'  CONCLUSAO: {elapsed:.0f}s | {len(MICRO)} micro + {len(CTX)} ctx + {len(ICOLS)} inter')
print(sep)
dfc.to_csv('D:/MarketData/mimo/analise_contextual_v937.csv', index=False)
imp.to_csv('D:/MarketData/mimo/feature_importance_v937.csv')
print('  Salvos em D:/MarketData/mimo/')

