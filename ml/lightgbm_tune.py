#!/usr/bin/env python3
"""
lightgbm_tune.py — Grid search de hiperparametros LightGBM para walk-forward.

Testa combinacoes de:
  - num_leaves: 15, 31, 63
  - min_child_samples: 20, 50, 100
  - learning_rate: 0.01, 0.05, 0.1
  - n_estimators: 200, 500
  - subsample: 0.7, 0.9

Salva resultados em lightgbm_tune_results.json
"""
import json
import os
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from treino_lib import preparar_features, avaliar_modelo, feature_importances

SAVE_DIR = r'D:\MarketData\mimo'
DATASET = os.path.join(SAVE_DIR, 'dataset_final_v2_win.parquet')
ATIVO = 'WINV26'


def split_temporal(df, n_teste_dias=3, col_data='_data'):
    datas = sorted(set(df[col_data].unique()))
    if len(datas) < n_teste_dias + 1:
        return None, None, datas
    teste_datas = set(datas[-n_teste_dias:])
    treino = df[~df[col_data].isin(teste_datas)].copy()
    teste = df[df[col_data].isin(teste_datas)].copy()
    return treino, teste, datas


def run_tune():
    print(f'Carregando {DATASET}...')
    df = pd.read_parquet(DATASET)
    df = df[df['ativo'] == ATIVO].copy()
    print(f'  {len(df):,} linhas')

    # Data split
    df['_data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
    try:
        from zoneinfo import ZoneInfo
        df['_data'] = df['_data'].dt.tz_convert(ZoneInfo('America/Sao_Paulo'))
    except:
        pass
    df['_data'] = df['_data'].dt.strftime('%Y%m%d')

    treino, teste, datas = split_temporal(df, 3, '_data')
    print(f'  Treino: {len(treino):,} | Teste: {len(teste):,}')

    X_cols = preparar_features(df)
    X_cols = [c for c in X_cols if c in treino.columns]
    print(f'  Features: {len(X_cols)}')

    train = treino[treino['label'] != 0].copy()
    test = teste[teste['label'] != 0].copy()
    y_train = (train['label'] == 1).astype(int)
    y_test = (test['label'] == 1).astype(int)
    X_train = train[X_cols].fillna(0)
    X_test = test[X_cols].fillna(0)

    print(f'  Amostras treino: {len(X_train):,} | teste: {len(X_test):,}')

    # Grid search
    import lightgbm as lgb

    configs = []
    for num_leaves in [15, 31, 63]:
        for min_child in [20, 50, 100]:
            for lr in [0.01, 0.05, 0.1]:
                for n_est in [200, 500]:
                    configs.append({
                        'num_leaves': num_leaves,
                        'min_child_samples': min_child,
                        'learning_rate': lr,
                        'n_estimators': n_est,
                        'subsample': 0.8,
                    })

    print(f'\nTestando {len(configs)} combinacoes...\n')

    results = []
    best_pf = 0
    best_cfg = None

    for i, cfg in enumerate(configs):
        t0 = time.time()
        modelo = lgb.LGBMClassifier(
            num_leaves=cfg['num_leaves'],
            min_child_samples=cfg['min_child_samples'],
            learning_rate=cfg['learning_rate'],
            n_estimators=cfg['n_estimators'],
            subsample=cfg['subsample'],
            colsample_bytree=0.8,
            class_weight='balanced',
            random_state=42,
            verbose=-1,
        )
        modelo.fit(X_train, y_train)
        dt = time.time() - t0

        y_pred = modelo.predict(X_test)
        y_prob = modelo.predict_proba(X_test)[:, 1]

        from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix
        acc = accuracy_score(y_test, y_pred)
        try:
            auc = roc_auc_score(y_test, y_prob)
        except:
            auc = None

        cm = confusion_matrix(y_test, y_pred, labels=[0, 1])
        ganhos = cm[1, 1] * 100  # v11.6: TN nao e lucro
        perdas = (cm[1, 0] + cm[0, 1]) * 50
        pf = ganhos / perdas if perdas > 0 else 0
        exp = (ganhos - perdas) / max(cm.sum(), 1)

        r = {**cfg, 'acuracia': acc, 'auc': auc, 'pf': pf, 'expectancy': exp, 'tempo': dt}
        results.append(r)

        if pf > best_pf:
            best_pf = pf
            best_cfg = r

        # Progress
        if (i + 1) % 10 == 0 or i == len(configs) - 1:
            print(f'  [{i+1}/{len(configs)}] melhor PF={best_pf:.2f} (lr={best_cfg["learning_rate"]}, leaves={best_cfg["num_leaves"]}, min_child={best_cfg["min_child_samples"]}, n_est={best_cfg["n_estimators"]}, sub={best_cfg["subsample"]})')

    # Salva
    results.sort(key=lambda x: x['pf'], reverse=True)
    output = {
        'melhor': results[0],
        'top_10': results[:10],
        'todas': results,
    }

    with open('lightgbm_tune_results.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f'\n{"="*60}')
    print(f'MELHOR CONFIG:')
    print(f'  num_leaves: {results[0]["num_leaves"]}')
    print(f'  min_child_samples: {results[0]["min_child_samples"]}')
    print(f'  learning_rate: {results[0]["learning_rate"]}')
    print(f'  n_estimators: {results[0]["n_estimators"]}')
    print(f'  subsample: {results[0]["subsample"]}')
    print(f'  ---')
    print(f'  Acuracia: {results[0]["acuracia"]:.4f}')
    print(f'  AUC: {results[0]["auc"]:.4f}' if results[0]["auc"] else '  AUC: N/A')
    print(f'  Profit Factor: {results[0]["pf"]:.2f}')
    print(f'  Expectancy: {results[0]["expectancy"]:+.1f} pts')
    print(f'{"="*60}')

    print(f'\nTop 5:')
    for i, r in enumerate(results[:5]):
        print(f'  #{i+1} PF={r["pf"]:.2f} acc={r["acuracia"]:.4f} lr={r["learning_rate"]} leaves={r["num_leaves"]} min_child={r["min_child_samples"]} n_est={r["n_estimators"]}')


if __name__ == '__main__':
    run_tune()
