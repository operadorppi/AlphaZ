#!/usr/bin/env python3
"""Validação completa do modelo v9.14."""
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix, brier_score_loss
from sklearn.calibration import calibration_curve
import json, time

# 1. CARREGAR
print("=== VALIDACAO COMPLETA v9.14 ===\n")
df = pd.read_parquet(r"D:\MarketData\mimo\dataset_final_v2_win_v914.parquet")
print(f"Linhas: {len(df):,}, Colunas: {len(df.columns)}")

PROIBIDAS = ["label","saida","retorno","duracao","atingido","ts_ms","book_ts",
             "ctx_","ativo","dia","entrada","preco_saida","retorno_pts",
             "duracao_label_ms","tp_atingido","sl_atingido"]
X_cols = [c for c in df.columns if df[c].dtype.kind in ("f","i") and not any(p in c.lower() for p in PROIBIDAS)]
print(f"Features: {len(X_cols)}")

df_t = df[df["label"] != 0].copy().sort_values("ts_ms").reset_index(drop=True)
print(f"Treinaveis: {len(df_t):,} (TP={int((df_t['label']==1).sum()):,}, SL={int((df_t['label']==-1).sum()):,})")

df_t["dia"] = df_t["ts_ms"] // 86400000
dias = sorted(df_t["dia"].unique())
print(f"Dias: {len(dias)}")

# 2. WALK-FORWARD POR DIA
print("\n--- WALK-FORWARD POR DIA ---")
dia_split = dias[len(dias)-3]
df_train = df_t[df_t["dia"] < dia_split]
df_test = df_t[df_t["dia"] >= dia_split]
X_train, X_test = df_train[X_cols].fillna(0), df_test[X_cols].fillna(0)
y_train, y_test = df_train["label"].astype(int), df_test["label"].astype(int)
print(f"Treino: {len(df_train):,} | Teste: {len(df_test):,}")

modelo = RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_split=20,
                                min_samples_leaf=10, class_weight="balanced", random_state=42, n_jobs=-1)
t0 = time.time()
modelo.fit(X_train, y_train)
print(f"Treino RF: {time.time()-t0:.1f}s")

y_pred = modelo.predict(X_test)
y_proba = modelo.predict_proba(X_test)

dias_teste = sorted(df_test["dia"].unique())
resultados_dia = []
for d in dias_teste:
    mask = df_test["dia"] == d
    yt, yp = y_test.values[mask], y_pred[mask]
    n = len(yt)
    acc = accuracy_score(yt, yp)
    try:
        mb = yt != 0
        auc = roc_auc_score((yt[mb]==1).astype(int), y_proba[mask][mb][:, list(modelo.classes_).index(1)]) if len(np.unique((yt[mb]==1).astype(int))) > 1 else 0.5
    except: auc = 0.5
    cm = confusion_matrix(yt, yp, labels=[-1,1])
    ganhos = cm[1,1]*100+cm[0,0]*100
    perdas = cm[1,0]*50+cm[0,1]*50
    pf = ganhos/max(perdas,1)
    print(f"  Dia {int(d)}: n={n:5d} | acc={acc:.3f} AUC={auc:.3f} PF={pf:.2f}")
    resultados_dia.append({"dia":int(d),"n":n,"acc":acc,"auc":auc,"pf":pf})

acc_g = accuracy_score(y_test, y_pred)
mb = y_test.values != 0
try:
    auc_g = roc_auc_score((y_test.values[mb]==1).astype(int), y_proba[mb][:, list(modelo.classes_).index(1)])
except: auc_g = 0.5
cm_g = confusion_matrix(y_test, y_pred, labels=[-1,1])
ganhos_g = cm_g[1,1]*100+cm_g[0,0]*100
perdas_g = cm_g[1,0]*50+cm_g[0,1]*50
pf_g = ganhos_g/max(perdas_g,1)
exp_g = (ganhos_g-perdas_g)/max(len(y_test),1)
print(f"\n  GLOBAL: acc={acc_g:.4f} AUC={auc_g:.4f} PF={pf_g:.2f} expect={exp_g:+.1f}pts")

# 3. BASELINES
print("\n--- BASELINES ---")
yta = y_test.values
acc_sl = np.mean(yta==-1); pf_sl = np.sum(yta==-1)*100/max(np.sum(yta==1)*50,1)
acc_tp = np.mean(yta==1); pf_tp = np.sum(yta==1)*100/max(np.sum(yta==-1)*50,1)
np.random.seed(42)
yr = np.random.choice([-1,1],size=len(yta))
acc_r = np.mean(yr==yta)
cm_r = confusion_matrix(yta, yr, labels=[-1,1])
pf_r = (cm_r[1,1]*100+cm_r[0,0]*100)/max(cm_r[1,0]*50+cm_r[0,1]*50,1)
mom = np.where(df_test["delta_preco_janela"].values>0,1,-1) if "delta_preco_janela" in df_test else yr
acc_m = np.mean(mom==yta)
cm_m = confusion_matrix(yta, mom, labels=[-1,1])
pf_m = (cm_m[1,1]*100+cm_m[0,0]*100)/max(cm_m[1,0]*50+cm_m[0,1]*50,1)
print(f"  Always-SL:  acc={acc_sl:.4f} PF={pf_sl:.2f}")
print(f"  Always-TP:  acc={acc_tp:.4f} PF={pf_tp:.2f}")
print(f"  Random:     acc={acc_r:.4f} PF={pf_r:.2f}")
print(f"  Momentum:   acc={acc_m:.4f} PF={pf_m:.2f}")
print(f"  MODELO RF:  acc={acc_g:.4f} PF={pf_g:.2f}")

# 4. ABLACAO
print("\n--- ABLACAO ---")
grupos = {
    "todas": X_cols,
    "top10": [c for c in ["vp_vp_total","delta_preco_janela","vp_vah_dist","cvd_total","vp_poc_dist","vp_val_dist","preco_ultimo","vpin","ewma_imb_curta","aggr_imb"] if c in X_cols],
    "fluxo": [c for c in ["cvd_total","cvd_div","aggr_imb","ewma_imb_longa","ewma_imb_curta","ewma_imb_media","vpin","kyle_lambda"] if c in X_cols],
    "preco_vol": [c for c in ["delta_preco_janela","preco_ultimo","vol_compra","vol_venda","vol_total","n_eventos_janela","vp_vp_total"] if c in X_cols],
    "book": [c for c in ["spread","microprice","imbalance_L1","imbalance_L5","hhi_book","ofi","vel_imb"] if c in X_cols],
}
resultados_abl = []
for nome, cols in grupos.items():
    if len(cols) < 2: continue
    Xtr, Xte = df_train[cols].fillna(0), df_test[cols].fillna(0)
    m = RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_split=20,
                                min_samples_leaf=10, class_weight="balanced", random_state=42, n_jobs=-1)
    m.fit(Xtr, y_train); yp = m.predict(Xte)
    acc_a = accuracy_score(y_test, yp)
    try:
        mb2 = y_test.values != 0
        auc_a = roc_auc_score((y_test.values[mb2]==1).astype(int), m.predict_proba(Xte)[mb2][:, list(m.classes_).index(1)])
    except: auc_a = 0.5
    cm_a = confusion_matrix(y_test, yp, labels=[-1,1])
    pf_a = (cm_a[1,1]*100+cm_a[0,0]*100)/max(cm_a[1,0]*50+cm_a[0,1]*50,1)
    print(f"  {nome:12s} ({len(cols):2d} feat): acc={acc_a:.4f} AUC={auc_a:.4f} PF={pf_a:.2f}")
    resultados_abl.append({"grupo":nome,"n_features":len(cols),"acc":acc_a,"auc":auc_a,"pf":pf_a})

# 5. CALIBRACAO
print("\n--- CALIBRACAO ---")
idx_tp = list(modelo.classes_).index(1)
proba_tp = y_proba[:, idx_tp]
mbc = y_test.values != 0
y_bin = (y_test.values[mbc]==1).astype(int)
proba_cal = proba_tp[mbc]
brier = brier_score_loss(y_bin, proba_cal)
print(f"  Brier Score: {brier:.4f}")
fp, mp = calibration_curve(y_bin, proba_cal, n_bins=10)
ece = np.mean(np.abs(fp - mp))
print(f"  ECE: {ece:.4f}")
for i in range(min(len(fp),8)):
    print(f"    Pred {mp[i]:.2f} -> Real {fp[i]:.2f}")

# 6. BOOTSTRAP IC95%
print("\n--- IC95% (bootstrap por dia, 1000 iter) ---")
n_boot = 1000
boot_accs, boot_pfs = [], []
np.random.seed(42)
dta = df_test["dia"].values
for _ in range(n_boot):
    db = np.random.choice(dias_teste, size=len(dias_teste), replace=True)
    mb3 = np.isin(dta, db)
    yt_b, yp_b = y_test.values[mb3], y_pred[mb3]
    boot_accs.append(np.mean(yt_b==yp_b))
    cm_b = confusion_matrix(yt_b, yp_b, labels=[-1,1])
    boot_pfs.append((cm_b[1,1]*100+cm_b[0,0]*100)/max(cm_b[1,0]*50+cm_b[0,1]*50,1))
acc_ic = (np.percentile(boot_accs,2.5), np.percentile(boot_accs,97.5))
pf_ic = (np.percentile(boot_pfs,2.5), np.percentile(boot_pfs,97.5))
print(f"  Accuracy: {acc_g:.4f} IC95%=[{acc_ic[0]:.4f}, {acc_ic[1]:.4f}]")
print(f"  PF:       {pf_g:.2f} IC95%=[{pf_ic[0]:.2f}, {pf_ic[1]:.2f}]")

# 7. SALVAR
rel = {
    "versao": "v9.14", "data": "2026-08-23",
    "global": {"accuracy":float(acc_g),"auc":float(auc_g),"pf":float(pf_g),"expectancy":float(exp_g),
               "ic95_acc":[float(acc_ic[0]),float(acc_ic[1])],"ic95_pf":[float(pf_ic[0]),float(pf_ic[1])]},
    "baselines": {"always_sl":float(acc_sl),"always_tp":float(acc_tp),"random":float(acc_r),"momentum":float(acc_m)},
    "por_dia": resultados_dia, "ablacao": resultados_abl,
    "calibracao": {"brier":float(brier),"ece":float(ece)},
    "top_features": {k:float(v) for k,v in pd.Series(modelo.feature_importances_,index=X_cols).sort_values(ascending=False).head(10).items()},
}
with open(r"D:\MarketData\mimo\validacao_v914.json","w") as f:
    json.dump(rel, f, indent=2)
print(f"\nRelatorio salvo: validacao_v914.json")
