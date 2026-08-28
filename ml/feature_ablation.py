# -*- coding: utf-8 -*-
"""
ml/feature_ablation.py — Teste de ablation de features.

Analisa quais features podem ser removidas sem perda de performance.
Treina modelos variantes e compara métricas.

Uso:
    python ml/feature_ablation.py
"""

import pickle
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.metrics import accuracy_score, roc_auc_score

DATASET = r'D:\MarketData\mimo\26\dataset_final_v2_win_v914.parquet'
MODELO = r'D:\MarketData\mimo\26\modelo_lgbm_v4_limpo.pkl'


def load_data():
    df = pd.read_parquet(DATASET)
    df = df[df['ativo'] == 'WINV26'].copy()
    df['ts'] = pd.to_datetime(df['ts_ms'], unit='ms')
    df['dia'] = df['ts'].dt.date
    
    with open(MODELO, 'rb') as f:
        blob = pickle.load(f)
    
    all_features = blob['features']
    
    COLS_DROP = ['ts_ms', 'ts', 'dia', 'ativo', 'label', 'tp_atingido', 'sl_atingido',
                 'preco_saida', 'retorno_pts', 'duracao_label_ms', 'book_ts_ms']
    X_cols = [c for c in all_features if c in df.columns]
    
    dias = sorted(df['dia'].unique())
    dia_teste = dias[-1]
    df_train = df[df['dia'] < dia_teste]
    df_test = df[df['dia'] == dia_teste]
    
    train = df_train[df_train['label'] != 0]
    test = df_test[df_test['label'] != 0]
    
    y_train = (train['label'] == 1).astype(int)
    y_test = (test['label'] == 1).astype(int)
    X_train = train[X_cols].fillna(0)
    X_test = test[X_cols].fillna(0)
    
    return X_train, y_train, X_test, y_test, X_cols


def train_and_eval(X_train, y_train, X_test, y_test, features, nome):
    import lightgbm as lgb
    
    modelo = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, max_depth=6,
        num_leaves=31, min_child_samples=20,
        subsample=0.8, colsample_bytree=0.8,
        class_weight='balanced', random_state=42, verbose=-1,
    )
    modelo.fit(X_train, y_train, eval_set=[(X_test, y_test)],
               callbacks=[lgb.early_stopping(30, verbose=False)])
    
    y_pred = modelo.predict(X_test)
    y_prob = modelo.predict_proba(X_test)[:, 1]
    
    acc = accuracy_score(y_test, y_pred)
    auc = roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else 0.5
    
    # Profit factor simulado
    cm = [[0,0],[0,0]]
    for yt, yp in zip(y_test, y_pred):
        cm[yt][yp] += 1
    tp, fn, fp, tn = cm[1][1], cm[1][0], cm[0][1], cm[0][0]
    ganhos = (tp + tn) * 50
    perdas = (fp + fn) * 30
    pf = ganhos / perdas if perdas > 0 else 999
    
    return {
        'nome': nome,
        'n_features': len(features),
        'accuracy': acc,
        'auc': auc,
        'profit_factor': pf,
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
    }


def main():
    print('Carregando dados...')
    X_train, y_train, X_test, y_test, all_features = load_data()
    print(f'  Treino: {len(X_train)} | Teste: {len(X_test)} | Features: {len(all_features)}')
    
    # Feature importance do modelo atual
    with open(MODELO, 'rb') as f:
        blob = pickle.load(f)
    modelo = blob['modelo']
    imp = pd.Series(modelo.feature_importances_, index=all_features)
    imp = imp.sort_values(ascending=False)
    total_imp = imp.sum()
    
    # === BASELINE ===
    print('\n' + '=' * 70)
    print('BASELINE (todas as 26 features)')
    print('=' * 70)
    base = train_and_eval(X_train, y_train, X_test, y_test, all_features, 'baseline')
    print(f'  Accuracy: {base["accuracy"]:.4f} | AUC: {base["auc"]:.4f} | PF: {base["profit_factor"]:.2f}')
    
    # === REMOVER FEATURES MORTAS (0% importance) ===
    dead = ['kyle_kyle_n', 'vp_poc_acima']
    print(f'\n[1] Removendo features mortas (0%): {dead}')
    feats_1 = [f for f in all_features if f not in dead]
    r1 = train_and_eval(X_train, y_train, X_test, y_test, feats_1, 'sem-mortas')
    print(f'  Accuracy: {r1["accuracy"]:.4f} | AUC: {r1["auc"]:.4f} | PF: {r1["profit_factor"]:.2f}')
    
    # === REMOVER FEATURES MORTAS + BAIXA IMPORTANCIA ===
    low = ['kyle_kyle_n', 'vp_poc_acima', 'taxa_eventos', 'cvd_div']
    print(f'\n[2] + baixa importancia: {low}')
    feats_2 = [f for f in all_features if f not in low]
    r2 = train_and_eval(X_train, y_train, X_test, y_test, feats_2, 'sem-baixa')
    print(f'  Accuracy: {r2["accuracy"]:.4f} | AUC: {r2["auc"]:.4f} | PF: {r2["profit_factor"]:.2f}')
    
    # === REMOVER MULTICOLINEARIDADE ===
    # hhi_compra ~= entropy_compra (r=-0.976) -> remover entropy
    # hhi_venda ~= entropy_venda (r=-0.976) -> remover entropy
    # vol_total ~= vol_compra (r=0.817) -> remover vol_compra
    # vol_total ~= vol_venda (r=0.833) -> remover vol_venda
    # aggr_imb ~= ewma_imb_curta (r=0.854) -> remover ewma_imb_curta
    multi = low + ['entropy_compra', 'entropy_venda', 'vol_compra', 'vol_venda', 'ewma_imb_curta']
    print(f'\n[3] + remover multicolinearidade: {multi}')
    feats_3 = [f for f in all_features if f not in multi]
    r3 = train_and_eval(X_train, y_train, X_test, y_test, feats_3, 'sem-multicollin')
    print(f'  Accuracy: {r3["accuracy"]:.4f} | AUC: {r3["auc"]:.4f} | PF: {r3["profit_factor"]:.2f}')
    
    # === RESUMO ===
    print('\n' + '=' * 70)
    print('RESUMO COMPARATIVO')
    print('=' * 70)
    print(f'{"Modelo":25s} {"Feat":>4s} {"Acc":>7s} {"AUC":>7s} {"PF":>7s} {"Delta AUC":>10s}')
    print('-' * 70)
    for r in [base, r1, r2, r3]:
        delta = r['auc'] - base['auc']
        mark = ' <-- MELHOR' if r['auc'] >= base['auc'] else ''
        print(f'{r["nome"]:25s} {r["n_features"]:4d} {r["accuracy"]:7.4f} {r["auc"]:7.4f} {r["profit_factor"]:7.2f} {delta:+10.4f}{mark}')
    
    # Features restantes no melhor modelo
    best = max([base, r1, r2, r3], key=lambda x: x['auc'])
    print(f'\nMelhor: {best["nome"]} ({best["n_features"]} features)')
    
    print('\nFeatures recomendadas para proximo treino:')
    for f in feats_3:
        pct = imp.get(f, 0) / total_imp * 100
        print(f'  {f:30s} {pct:5.1f}%')


if __name__ == '__main__':
    main()
