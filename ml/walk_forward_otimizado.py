#!/usr/bin/env python3
# walk_forward_otimizado.py - Walk-forward HONESTO (v12.2)
#
# SO metricas de QUALIDADE do modelo (sem simulacao de P&L):
#   - AUC (discriminacao)
#   - ECE (Expected Calibration Error)
#   - Precision/Recall por threshold
#   - Calibration curve
#
# A simulacao de P&L com regras reais (1 trade por vez, TP/SL, reentrada)
# deve ser feita no replay_engine.py.
#
#  - treino SEMPRE antes do teste (folds expansivos, min 3 dias de treino)
#  - purge (30s) + embargo (30s) na fronteira treino/teste
#  - target binario TP-vs-nao-TP (label == 1)
#  - modelo: LightGBM (fallback RandomForest), seed fixo
# Saida: walk_forward_v950.json
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
from sklearn.metrics import roc_auc_score

try:
    from feature_cache import load_or_compute
    USE_CACHE = True
except ImportError:
    USE_CACHE = False

PATH = 'D:/MarketData/mimo/dataset_final.parquet'  # v12.1: pipeline multi-ativo
PATH_COMPL = None  # v11.20: nao usar completo (contaminado)
OUT = 'walk_forward_v950.json'
PURGE_S = 30
EMBARGO_S = 30
SEED = 42
THRESHOLDS = [0.5, 0.6, 0.7]
MIN_TREINO_DIAS = 3

PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']


def _ece(y_true, y_prob, n_bins=10):
    """Expected Calibration Error."""
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


def run():
    t0 = time.time()
    import pickle as _pk
    with open('D:/MarketData/mimo/26/modelo_lgbm_v4_limpo.pkl', 'rb') as _f:
        blob = _pk.load(_f)
    feat_cols = list(blob['features'])
    # v9.32: preferir dataset enriquecido se existir
    import os as _os
    _path = PATH if (PATH_COMPL is None or not _os.path.exists(PATH_COMPL)) else PATH_COMPL
    print(f'[walk_forward] usando: {_path} (cache={USE_CACHE})')
    schema = pq.read_schema(_path)
    todas = set(schema.names)
    faltando = [f for f in feat_cols if f not in todas]
    if faltando:
        raise SystemExit('features do modelo ausentes no parquet: %s' % faltando)
    _COLS = list(dict.fromkeys(['ts_ms', 'label'] + feat_cols))
    def _load_data(_df=None):
        if _df is None:
            _df = pq.read_table(_path, columns=_COLS).to_pandas()
        if 'vol_total' in _df.columns:
            _mask = _df['vol_total'].to_numpy() >= 5
            _df = _df[_mask].reset_index(drop=True)
        return _df
    # Load with column selection to avoid OOM
    print('carregando dataset...', flush=True)
    df = pq.read_table(_path, columns=_COLS).to_pandas()
    df = _load_data(df)
    print(f'carregado: {len(df)} linhas, {len(df.columns)} cols', flush=True)

    ts = df['ts_ms'].to_numpy()
    local = ts - 3 * 3600 * 1000  # Brasilia sem DST (UTC-3 fixo em agosto)
    dias_idx = local // 86400000
    ordem = sorted(set(int(d) for d in dias_idx))
    data_dia = {d: (datetime.date(1970, 1, 1) + datetime.timedelta(days=d)).isoformat()
                for d in ordem}
    print('dias:', [data_dia[d] for d in ordem], flush=True)

    # vol>=5 filter agora em _load_data (cache)
    Xfull = df[feat_cols].apply(pd.to_numeric, errors='coerce').astype(np.float32)
    Xfull = Xfull.dropna(axis=1, how='all')  # float32 = metade da RAM
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
        pass  # ja_prontos calculado uma vez
        test_day = ordem[i]
        treino_dias = ordem[:i]
        if data_dia[test_day] in ja_prontos:
            print('fold', i - MIN_TREINO_DIAS + 1, data_dia[test_day], 'ja pronto, pulando', flush=True)
            continue
        b_ts = int(ts[dias_idx == test_day].min())
        tr_mask = (dias_idx < test_day) & (ts <= b_ts - PURGE_S * 1000)
        te_mask = (dias_idx == test_day) & (ts >= b_ts + EMBARGO_S * 1000)

        t_fold_start = time.time()
        clf = LGBMClassifier(n_estimators=500, learning_rate=0.05,
                             num_leaves=63, min_child_samples=50,
                             subsample=0.8, colsample_bytree=0.8,
                             n_jobs=-1, random_state=SEED, verbose=-1)
        # v9.30: early stopping — divide treino em train/val (80/20)
        import lightgbm as lgb
        _tr_idx = np.where(tr_mask)[0]
        _split = int(len(_tr_idx) * 0.8)
        _tr_final = _tr_idx[:_split]
        _val_idx = _tr_idx[_split:]
        clf.fit(Xarr[_tr_final], y[_tr_final],
                eval_set=[(Xarr[_val_idx], y[_val_idx])],
                callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])

        prob = clf.predict_proba(Xarr[te_mask])[:, 1]
        y_te = y[te_mask]
        n_te = int(te_mask.sum())
        n_unicos = len(np.unique(y_te))

        # AUC (independente de threshold)
        auc = round(float(roc_auc_score(y_te, prob)), 4) if n_unicos > 1 else None

        # ECE (calibracao)
        ece = _ece(y_te.astype(float), prob)

        # Precision/Recall por threshold
        from sklearn.metrics import precision_score, recall_score
        metrics_por_thr = {}
        for thr in THRESHOLDS:
            y_pred = (prob >= thr).astype(int)
            n_pred_pos = int(y_pred.sum())
            if n_pred_pos == 0:
                metrics_por_thr[str(thr)] = {'n_pred_pos': 0, 'precision': None, 'recall': None}
            else:
                metrics_por_thr[str(thr)] = {
                    'n_pred_pos': n_pred_pos,
                    'precision': round(float(precision_score(y_te, y_pred, zero_division=0)), 4),
                    'recall': round(float(recall_score(y_te, y_pred, zero_division=0)), 4),
                }

        # Calibration curve
        cal_bins = {}
        bin_edges = np.linspace(0, 1, 11)
        for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
            mask = (prob >= lo) & (prob < hi)
            if mask.sum() == 0:
                continue
            cal_bins[f'{lo:.1f}-{hi:.1f}'] = {
                'predicted': round(float(prob[mask].mean()), 4),
                'observed': round(float(y_te[mask].mean()), 4),
                'count': int(mask.sum()),
            }

        t_fold_end = time.time()
        folds.append({
            'fold': len(folds) + 1,
            'treino_dias': [data_dia[d] for d in treino_dias],
            'teste_dia': data_dia[test_day],
            'n_treino': int(tr_mask.sum()),
            'n_teste': n_te,
            'auc': auc,
            'ece': ece,
            'metrics_por_threshold': metrics_por_thr,
            'calibration_curve': cal_bins,
            'tempo_fold_s': round(t_fold_end - t_fold_start, 2),
        })
        with io.open(OUT, 'w', encoding='utf-8') as f:
            json.dump({'parcial': True, 'fold_atual': len(folds), 'folds': folds},
                      f, ensure_ascii=False, indent=2)
        print('fold', len(folds), data_dia[test_day], 'auc', auc,
              'ece', ece, flush=True)

    # Resumo global
    aucs = [f['auc'] for f in folds if f.get('auc') is not None]
    eces = [f['ece'] for f in folds if f.get('ece') is not None]

    res = {
        'descricao': 'Walk-forward HONESTO v12.2: so metricas de qualidade (AUC+ECE+calibration)',
        'dataset': _path,
        'modelo': MODELO,
        'features': len(Xcols),
        'purge_s': PURGE_S,
        'embargo_s': EMBARGO_S,
        'target': 'binario TP-vs-nao-TP',
        'resumo_global': {
            'n_folds': len(folds),
            'auc_media': round(float(np.mean(aucs)), 4) if aucs else None,
            'ece_media': round(float(np.mean(eces)), 4) if eces else None,
        },
        'tempo_total_s': round(time.time() - t0, 2),
        'folds': folds,
    }
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print('salvo:', OUT, '| segundos:', round(time.time() - t0, 1), flush=True)


if __name__ == '__main__':
    run()
