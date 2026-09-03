#!/usr/bin/env python3
"""
walk_forward_completo.py — Walk-forward rigoroso + avaliação por dia + ablação.
Rode: python walk_forward_completo.py
"""
import os
import sys
import json
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

DATASET = r'D:\MarketData\mimo\dataset_final_v2_win.parquet'
ATIVO = 'WINV26'
TP_PTS = 100
SL_PTS = 50
OUTPUT = Path('validacao_resultados')
OUTPUT.mkdir(exist_ok=True)


def carregar():
    print('Carregando dataset...')
    df = pd.read_parquet(DATASET)
    df = df[df['ativo'] == ATIVO].copy()

    try:
        from zoneinfo import ZoneInfo
        df['_data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.tz_convert(ZoneInfo('America/Sao_Paulo')).dt.strftime('%Y%m%d')
    except:
        df['_data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.strftime('%Y%m%d')

    df['label'] = df['label'].fillna(0).astype(int)

    proibidas = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms', 'book_ts', 'ctx_', 'ativo', '_data']
    X_cols = [c for c in df.columns if df[c].dtype in ('float64', 'int64', 'float32', 'int32') and c != 'label' and not any(p in c.lower() for p in proibidas)]

    print(f'  {len(df):,} linhas, {len(X_cols)} features')
    return df, X_cols


def avaliar(modelo, X_test, y_test):
    from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix
    y_pred = modelo.predict(X_test)

    r = {'acuracia': accuracy_score(y_test, y_pred), 'auc': None, 'pf': 0, 'exp': 0, 'n_pos': int(np.sum(y_pred == 1)), 'n_neg': int(np.sum(y_pred == 0))}

    if hasattr(modelo, 'predict_proba') and len(np.unique(y_test)) > 1:
        r['auc'] = roc_auc_score(y_test, modelo.predict_proba(X_test)[:, 1])

    cm = confusion_matrix(y_test, y_pred, labels=[0, 1])
    g = (cm[1, 1] + cm[0, 0]) * TP_PTS
    p = (cm[1, 0] + cm[0, 1]) * SL_PTS
    r['pf'] = g / p if p > 0 else 0
    r['exp'] = (g - p) / max(cm.sum(), 1)

    # Drawdown
    dd = 0; max_dd = 0
    for i in range(len(y_pred)):
        if y_pred[i] != y_test.iloc[i]:
            dd += SL_PTS
            max_dd = max(max_dd, dd)
        else:
            dd = max(0, dd - TP_PTS)
    r['drawdown'] = max_dd
    return r


def walk_forward():
    """Walk-forward: treina nos N primeiros dias, testa nos últimos."""
    from sklearn.ensemble import RandomForestClassifier

    df, X_cols = carregar()
    datas = sorted(df['_data'].unique())

    print(f'\nDatas: {len(datas)} ({datas[0]} a {datas[-1]})')

    # Split: últimos 3 dias teste
    teste_datas = set(datas[-3:])
    treino_datas = set(datas[:-3])

    treino = df[df['_data'].isin(treino_datas)]
    teste = df[df['_data'].isin(teste_datas)]

    train = treino[treino['label'] != 0]
    test = teste[teste['label'] != 0]

    y_train = (train['label'] == 1).astype(int)
    y_test = (test['label'] == 1).astype(int)
    X_train = train[X_cols].fillna(0)
    X_test = test[X_cols].fillna(0)

    print(f'\nTreino: {len(X_train):,} | Teste: {len(X_test):,}')

    # Modelo FROZEN
    modelo = RandomForestClassifier(n_estimators=100, max_depth=10, class_weight='balanced', random_state=42, n_jobs=-1)
    print('Treinando...')
    t0 = time.time()
    modelo.fit(X_train, y_train)
    print(f'Treino: {time.time()-t0:.1f}s')

    # Resultado global
    r = avaliar(modelo, X_test, y_test)
    print(f'\n=== RESULTADO GLOBAL ===')
    print(f'  Accuracy:  {r["acuracia"]:.4f}')
    print(f'  AUC-ROC:   {r["auc"]:.4f}' if r['auc'] else '  AUC-ROC: N/A')
    print(f'  PF:        {r["pf"]:.2f}')
    print(f'  Expectancy: {r["exp"]:+.1f} pts')
    print(f'  Drawdown:  {r["drawdown"]} pts')
    print(f'  Sinais:    +1={r["n_pos"]} -1={r["n_neg"]}')

    # Por dia
    print(f'\n=== POR DIA ===')
    por_dia = {}
    for dia in sorted(teste_datas):
        td = test[test['_data'] == dia]
        if len(td) < 10:
            continue
        Xd = td[X_cols].fillna(0)
        yd = (td['label'] == 1).astype(int)
        rd = avaliar(modelo, Xd, yd)
        por_dia[dia] = rd
        auc_s = f'{rd["auc"]:.4f}' if rd['auc'] else 'N/A'
        print(f'  {dia}: acc={rd["acuracia"]:.4f} auc={auc_s:>6} pf={rd["pf"]:.2f} exp={rd["exp"]:+.1f} dd={rd["drawdown"]} sinais=+{rd["n_pos"]}/-{rd["n_neg"]}')

    # Features
    imp = pd.Series(modelo.feature_importances_, index=X_cols).sort_values(ascending=False)
    print(f'\n=== TOP 10 FEATURES ===')
    for f, v in imp.head(10).items():
        print(f'  {f:35s} {v:.4f}')

    # Salva
    resultado = {
        'global': r,
        'por_dia': por_dia,
        'features': imp.head(15).to_dict(),
        'treino_dias': list(treino_datas),
        'teste_dias': list(teste_datas),
    }
    with open(OUTPUT / 'walk_forward_completo.json', 'w') as f:
        json.dump(resultado, f, indent=2, default=str)

    return modelo, X_cols, df, r


def ablacao(modelo_treinado, X_cols_full, df):
    """Ablação: compara grupos de features."""
    from sklearn.ensemble import RandomForestClassifier

    datas = sorted(df['_data'].unique())
    teste_datas = set(datas[-3:])
    treino_datas = set(datas[:-3])

    treino = df[df['_data'].isin(treino_datas)]
    teste = df[df['_data'].isin(teste_datas)]

    train = treino[treino['label'] != 0]
    test = teste[teste['label'] != 0]

    y_train = (train['label'] == 1).astype(int)
    y_test = (test['label'] == 1).astype(int)

    grupos = {
        'top10': ['delta_preco_janela', 'vp_vp_total', 'cvd_total', 'preco_ultimo', 'ewma_imb_longa', 'vp_vah_dist', 'vp_poc_dist', 'aggr_imb', 'n_eventos_janela', 'vol_compra'],
        'preco_vol': ['delta_preco_janela', 'preco_ultimo', 'vol_compra', 'vol_venda', 'vol_total', 'n_eventos_janela', 'vp_vp_total'],
        'fluxo': ['cvd_total', 'cvd_div', 'aggr_imb', 'ewma_imb_longa', 'ewma_imb_curta', 'ewma_imb_media', 'vpin', 'kyle_kyle_lambda'],
        'book': ['spread', 'microprice', 'ofi', 'hhi_book', 'imb_L1', 'imb_L5', 'micro_drift_ewma', 'imb_ponderado'],
    }

    print(f'\n=== ABLACAO ===')
    print(f'{"Grupo":15s} {"#Feat":>5s} {"Acc":>8s} {"AUC":>8s} {"PF":>8s} {"Exp":>8s}')
    print('-'*55)

    resultados = {}
    for nome, feats in grupos.items():
        X_cols = [f for f in feats if f in X_cols_full]
        if len(X_cols) == 0:
            print(f'{nome:15s}   SKIP (nenhuma feature encontrada)')
            continue
        X_tr = train[X_cols].fillna(0)
        X_te = test[X_cols].fillna(0)

        m = RandomForestClassifier(n_estimators=100, max_depth=10, class_weight='balanced', random_state=42, n_jobs=-1)
        m.fit(X_tr, y_train)
        r = avaliar(m, X_te, y_test)
        resultados[nome] = r

        auc_s = f'{r["auc"]:.4f}' if r['auc'] else 'N/A'
        print(f'{nome:15s} {len(X_cols):5d} {r["acuracia"]:8.4f} {auc_s:>8s} {r["pf"]:8.2f} {r["exp"]:+8.1f}')

    # Todas as features
    X_tr = train[X_cols_full].fillna(0)
    X_te = test[X_cols_full].fillna(0)
    m = RandomForestClassifier(n_estimators=100, max_depth=10, class_weight='balanced', random_state=42, n_jobs=-1)
    m.fit(X_tr, y_train)
    r = avaliar(m, X_te, y_test)
    resultados['todas'] = r
    auc_s = f'{r["auc"]:.4f}' if r['auc'] else 'N/A'
    print(f'{"todas":15s} {len(X_cols_full):5d} {r["acuracia"]:8.4f} {auc_s:>8s} {r["pf"]:8.2f} {r["exp"]:+8.1f}')

    with open(OUTPUT / 'ablacao.json', 'w') as f:
        json.dump(resultados, f, indent=2, default=str)

    return resultados


def robustez():
    """Testa diferentes splits temporais."""
    from sklearn.ensemble import RandomForestClassifier

    df, X_cols = carregar()
    datas = sorted(df['_data'].unique())

    # v9.13: folds com TESTE disjunto (antes '5d_3d' testava os mesmos 3 dias
    # de '7d_3d' — mediam saturação de treino, não robustez entre regimes).
    # Se houver 11+ dias, os 3 folds cobrem todo o período sem sobreposição.
    if len(datas) >= 11:
        splits = [
            ('7d_fold1', datas[:7], datas[7:10]),
            ('7d_fold2', datas[4:11], datas[11:14]) if len(datas) >= 14 else ('7d_fold2', datas[4:11], datas[11:]),
            ('5d_3d', datas[:5], datas[7:10]),  # sensibilidade ao tamanho do treino (mesmo teste p/ comparar)
        ]
    else:
        splits = [
            ('7d_3d', datas[:7], datas[7:10]),
            ('8d_2d', datas[:8], datas[8:10]),
            ('5d_3d', datas[:5], datas[7:10]),
        ]

    print(f'\n=== ROBUSTEZ ===')
    resultados = {}

    for nome, t_d, te_d in splits:
        treino = df[df['_data'].isin(t_d)]
        teste = df[df['_data'].isin(te_d)]

        train = treino[treino['label'] != 0]
        test = teste[teste['label'] != 0]

        if len(train) < 50 or len(test) < 50:
            print(f'{nome}: pulando (poucos dados)')
            continue

        y_train = (train['label'] == 1).astype(int)
        y_test = (test['label'] == 1).astype(int)
        X_train = train[X_cols].fillna(0)
        X_test = test[X_cols].fillna(0)

        m = RandomForestClassifier(n_estimators=100, max_depth=10, class_weight='balanced', random_state=42, n_jobs=-1)
        m.fit(X_train, y_train)
        r = avaliar(m, X_test, y_test)
        resultados[nome] = {'treino': t_d, 'teste': te_d, 'r': r}

        auc_s = f'{r["auc"]:.4f}' if r['auc'] else 'N/A'
        print(f'  {nome}: acc={r["acuracia"]:.4f} auc={auc_s:>6} pf={r["pf"]:.2f} exp={r["exp"]:+.1f} dd={r["drawdown"]}')

    with open(OUTPUT / 'robustez.json', 'w') as f:
        json.dump(resultados, f, indent=2, default=str)

    return resultados


def relatorio(wf_r, abl_r, rob_r):
    """Gera relatório final."""
    pf = wf_r['global']['pf']
    auc = wf_r['global']['auc'] or 0

    if pf > 2.0 and auc > 0.6:
        classif = 'A — CONFIRMADO'
    elif pf > 1.5 and auc > 0.55:
        classif = 'B — PARCIAL'
    else:
        classif = 'C — NAO CONFIRMADO'

    print(f'\n{"="*60}')
    print(f'RELATORIO FINAL')
    print(f'{"="*60}')
    print(f'Classificacao: {classif}')
    print(f'PF={pf:.2f} AUC={auc:.4f}')

    print(f'\nFeatures principais:')
    for f, v in list(wf_r['features'].items())[:5]:
        print(f'  {f}: {v:.4f}')

    print(f'\nRobustez:')
    for nome, res in rob_r.items():
        print(f'  {nome}: PF={res["r"]["pf"]:.2f}')

    print(f'\nAblacao:')
    for nome, r in abl_r.items():
        print(f'  {nome}: PF={r["pf"]:.2f}')

    with open(OUTPUT / 'relatorio_final.json', 'w') as f:
        json.dump({'classificacao': classif, 'global': wf_r['global'], 'features': wf_r['features'], 'ablacao': abl_r, 'robustez': {k: v['r'] for k, v in rob_r.items()}}, f, indent=2, default=str)


if __name__ == '__main__':
    modelo, X_cols, df, wf_r = walk_forward()
    abl_r = ablacao(modelo, X_cols, df)
    rob_r = robustez()
    # wf_r is the result dict directly, not nested
    # v14.8: 'imp' era local de walk_forward() — a expressão era código morto
    # (sempre {}); as importâncias reais já vêm em wf_r['features'].
    wf_wrapped = {'global': wf_r, 'features': (wf_r or {}).get('features', {})}
    relatorio(wf_wrapped, abl_r, rob_r)
    print('\nConcluido!')
