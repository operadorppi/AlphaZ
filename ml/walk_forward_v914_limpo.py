#!/usr/bin/env python3
# walk_forward_v914_limpo.py - Walk-forward: métricas de QUALIDADE do modelo
#
# v11.4: Reescrito para focar em métricas de classificação, não P&L simulado.
# O walk-forward anterior tratava cada segundo como um trade independente
# (456K trades/dia), o que é fisicamente impossível.
#
# Métricas reportadas por fold:
#   - AUC (discriminação)
#   - ECE (Expected Calibration Error)
#   - Accuracy, Precision, Recall, F1
#   - Distribuição de probabilidades
#
# A simulação de P&L com regras reais (1 trade por vez, TP/SL, reentrada)
# deve ser feita em replay_engine.py ou simular_pnl.py.
#
# Saida: walk_forward_v914_limpo.json

import io
import json
import os
import time
import datetime
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pyarrow.parquet as pq
import pandas as pd
from sklearn.metrics import (
    roc_auc_score, accuracy_score, precision_score, recall_score,
    f1_score, confusion_matrix, brier_score_loss,
)

PATH = 'D:/MarketData/mimo/26/dataset_final_v2_win_v914.parquet'
PATH_COMPL = 'D:/MarketData/mimo/26/dataset_final_completo.parquet'
OUT = 'walk_forward_v914_limpo.json'
PURGE_S = 30
EMBARGO_S = 30
SEED = 42
THRESHOLDS = [0.5, 0.6, 0.7]
THRESH_PRINCIPAL = 0.6
MIN_TREINO_DIAS = 3

PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']


def _ece(y_true, y_prob, n_bins=10):
    """Expected Calibration Error: mede quão calibrada é a probabilidade."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (y_prob >= lo) & (y_prob < hi)
        if mask.sum() == 0:
            continue
        acc = y_true[mask].mean()
        conf = y_prob[mask].mean()
        ece += mask.sum() / len(y_true) * abs(acc - conf)
    return round(float(ece), 4)


def _brier(y_true, y_prob):
    """Brier Score: lower is better (0 = perfect)."""
    if len(y_true) == 0 or len(np.unique(y_true)) < 2:
        return None
    return round(float(brier_score_loss(y_true, y_prob)), 4)


def metricas_classificacao(y_true, y_prob, threshold=0.5):
    """Métricas de classificação para um threshold dado."""
    y_pred = (y_prob >= threshold).astype(int)
    n_pos = int(y_pred.sum())
    n_total = len(y_true)

    if n_pos == 0:
        return {
            'threshold': threshold,
            'n_pred_pos': 0,
            'n_total': n_total,
            'accuracy': None,
            'precision': None,
            'recall': None,
            'f1': None,
            'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0,
        }

    acc = round(float(accuracy_score(y_true, y_pred)), 4)
    prec = round(float(precision_score(y_true, y_pred, zero_division=0)), 4)
    rec = round(float(recall_score(y_true, y_pred, zero_division=0)), 4)
    f1 = round(float(f1_score(y_true, y_pred, zero_division=0)), 4)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()

    return {
        'threshold': threshold,
        'n_pred_pos': n_pos,
        'n_pred_neg': n_total - n_pos,
        'n_total': n_total,
        'accuracy': acc,
        'precision': prec,
        'recall': rec,
        'f1': f1,
        'tp': int(tp), 'fp': int(fp), 'tn': int(tn), 'fn': int(fn),
        'prevalence': round(float(y_true.mean()), 4),
    }


def run():
    t0 = time.time()
    import pickle as _pk
    with open('D:/MarketData/mimo/26/modelo_lgbm_v4_limpo.pkl', 'rb') as _f:
        blob = _pk.load(_f)
    feat_cols = list(blob['features'])

    _path = PATH_COMPL if os.path.exists(PATH_COMPL) else PATH
    print(f'[walk_forward] usando: {_path}')
    schema = pq.read_schema(_path)
    todas = set(schema.names)
    faltando = [f for f in feat_cols if f not in todas]
    if faltando:
        raise SystemExit('features do modelo ausentes no parquet: %s' % faltando)

    _COLS = list(dict.fromkeys(['ts_ms', 'label'] + feat_cols))
    df = pq.read_table(_path, columns=_COLS).to_pandas()

    ts = df['ts_ms'].to_numpy()
    local = ts - 3 * 3600 * 1000
    dias_idx = local // 86400000
    ordem = sorted(set(int(d) for d in dias_idx))
    data_dia = {d: (datetime.date(1970, 1, 1) + datetime.timedelta(days=d)).isoformat()
                for d in ordem}
    print('dias:', [data_dia[d] for d in ordem], flush=True)

    if 'vol_total' in df.columns:
        mask_vol = df['vol_total'].to_numpy() >= 5
        n_antes = len(df)
        df = df[mask_vol].reset_index(drop=True)
        ts = df['ts_ms'].to_numpy()
        local = ts - 3 * 3600 * 1000
        dias_idx = local // 86400000
        print('vol>=5 filter:', n_antes, '->', len(df),
              '(' + str(round(100*len(df)/n_antes, 1)) + '%)', flush=True)

    Xfull = df[feat_cols].apply(pd.to_numeric, errors='coerce').astype(np.float32)
    Xfull = Xfull.dropna(axis=1, how='all')
    Xcols = list(Xfull.columns)
    Xarr = Xfull.to_numpy()
    y = (df['label'].to_numpy() == 1).astype(np.int8)
    print('linhas:', len(df), '| features:', len(Xcols), flush=True)

    try:
        from lightgbm import LGBMClassifier
        MODELO = 'LightGBM'
    except ImportError:
        from sklearn.ensemble import RandomForestClassifier
        LGBMClassifier = lambda **kw: RandomForestClassifier(
            n_estimators=200, max_depth=12, n_jobs=2, random_state=kw['random_state'])
        MODELO = 'RandomForest(fallback)'
    print('modelo:', MODELO, flush=True)

    folds = []
    if os.path.exists(OUT):
        try:
            ant = json.load(io.open(OUT, encoding='utf-8'))
            folds = ant.get('folds', [])
        except Exception:
            pass
    ja_prontos = {f['teste_dia'] for f in folds}

    for i in range(MIN_TREINO_DIAS, len(ordem)):
        test_day = ordem[i]
        treino_dias = ordem[:i]
        if data_dia[test_day] in ja_prontos:
            print('fold', i - MIN_TREINO_DIAS + 1, data_dia[test_day],
                  'ja pronto, pulando', flush=True)
            continue

        b_ts = int(ts[dias_idx == test_day].min())
        tr_mask = (dias_idx < test_day) & (ts <= b_ts - PURGE_S * 1000)
        te_mask = (dias_idx == test_day) & (ts >= b_ts + EMBARGO_S * 1000)

        clf = LGBMClassifier(n_estimators=400, learning_rate=0.05,
                             num_leaves=63, min_child_samples=50,
                             subsample=0.8, colsample_bytree=0.8,
                             n_jobs=2, random_state=SEED, verbose=-1)

        import lightgbm as lgb
        _tr_idx = np.where(tr_mask)[0]
        _split = int(len(_tr_idx) * 0.8)
        _tr_final = _tr_idx[:_split]
        _val_idx = _tr_idx[_split:]
        clf.fit(Xarr[_tr_final], y[_tr_final],
                eval_set=[(Xarr[_val_idx], y[_val_idx])],
                callbacks=[lgb.early_stopping(50, verbose=False),
                           lgb.log_evaluation(0)])

        prob = clf.predict_proba(Xarr[te_mask])[:, 1]
        y_te = y[te_mask]
        n_te = int(te_mask.sum())
        n_unicos = len(np.unique(y_te))

        # === Métricas de qualidade (sem simulação P&L) ===

        # AUC (discriminação)
        auc = round(float(roc_auc_score(y_te, prob)), 4) if n_unicos > 1 else None

        # Brier Score (calibração — menor = melhor)
        brier = _brier(y_te, prob)

        # ECE ( Expected Calibration Error — menor = melhor)
        ece = _ece(y_te, prob)

        # Métricas por threshold
        metrics_por_thr = {}
        for thr in THRESHOLDS:
            metrics_por_thr[str(thr)] = metricas_classificacao(y_te, prob, thr)

        m_princ = metrics_por_thr[str(THRESH_PRINCIPAL)]

        # Feature importance (top 10)
        if hasattr(clf, 'feature_importances_'):
            imp = dict(zip(Xcols, clf.feature_importances_))
            top10 = sorted(imp.items(), key=lambda x: -x[1])[:10]
            top10_feat = {f: round(float(v), 1) for f, v in top10}
        else:
            top10_feat = {}

        # Distribuição de probabilidades
        prob_stats = {
            'mean': round(float(prob.mean()), 4),
            'std': round(float(prob.std()), 4),
            'min': round(float(prob.min()), 4),
            'max': round(float(prob.max()), 4),
            'p25': round(float(np.percentile(prob, 25)), 4),
            'p50': round(float(np.percentile(prob, 50)), 4),
            'p75': round(float(np.percentile(prob, 75)), 4),
        }

        folds.append({
            'fold': len(folds) + 1,
            'treino_dias': [data_dia[d] for d in treino_dias],
            'teste_dia': data_dia[test_day],
            'n_treino': int(tr_mask.sum()),
            'n_teste': n_te,
            'prevalence': round(float(y_te.mean()), 4),
            'auc': auc,
            'brier': brier,
            'ece': ece,
            'metrics_por_threshold': metrics_por_thr,
            'threshold_principal': m_princ,
            'prob_distribuicao': prob_stats,
            'top10_features': top10_feat,
        })

        with io.open(OUT, 'w', encoding='utf-8') as f:
            json.dump({'parcial': True, 'fold_atual': len(folds), 'folds': folds},
                      f, ensure_ascii=False, indent=2)

        print(f'fold {len(folds)} {data_dia[test_day]} '
              f'auc={auc} ece={ece} brier={brier} '
              f'acc@0.6={m_princ["accuracy"]} prec@0.6={m_princ["precision"]} '
              f'rec@0.6={m_princ["recall"]}', flush=True)

    # === Resumo global ===
    aucs = [f['auc'] for f in folds if f.get('auc') is not None]
    eces = [f['ece'] for f in folds if f.get('ece') is not None]
    briers = [f['brier'] for f in folds if f.get('brier') is not None]

    res = {
        'descricao': 'Walk-forward v11.4: metricas de QUALIDADE (sem P&L simulado)',
        'versao': 'v11.4',
        'dataset': _path,
        'modelo': MODELO,
        'features': len(Xcols),
        'purge_s': PURGE_S,
        'embargo_s': EMBARGO_S,
        'target': 'binario TP-vs-nao-TP',
        'resumo_global': {
            'n_folds': len(folds),
            'auc_media': round(float(np.mean(aucs)), 4) if aucs else None,
            'auc_std': round(float(np.std(aucs)), 4) if aucs else None,
            'ece_media': round(float(np.mean(eces)), 4) if eces else None,
            'brier_media': round(float(np.mean(briers)), 4) if briers else None,
        },
        'folds': folds,
    }

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f'\nsalvo: {OUT} | segundos: {round(time.time() - t0, 1)}')
    print(f'Resumo: AUC={res["resumo_global"]["auc_media"]} '
          f'ECE={res["resumo_global"]["ece_media"]} '
          f'Brier={res["resumo_global"]["brier_media"]}')


if __name__ == '__main__':
    run()
