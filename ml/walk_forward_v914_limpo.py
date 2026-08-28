#!/usr/bin/env python3
# walk_forward_v914_limpo.py - Walk-forward limpo sobre labels v9.14 (v9.20).
#
#  - treino SEMPRE antes do teste (folds expansivos, min 3 dias de treino)
#  - purge (30s) + embargo (30s) na fronteira treino/teste
#  - target binario TP-vs-nao-TP (label == 1) - framing do AUC 0.66
#  - modelo: LightGBM (fallback RandomForest), seed fixo
#  - metricas POR DIA, custo 5 pts WIN, thresholds 0.5 / 0.6 / 0.7
#  - baselines: B1 threshold=0, B2 aleatorio (30 seeds, mesmo n de trades),
#    B3 momentum (delta_preco_janela > 0)
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
from sklearn.metrics import roc_auc_score

PATH = 'D:/MarketData/mimo/26/dataset_final_v2_win_v914.parquet'
# v9.32: dataset enriquecido com ajuste oficial + VWAP + regime
PATH_COMPL = 'D:/MarketData/mimo/26/dataset_final_completo.parquet'
OUT = 'walk_forward_v914_limpo.json'
COSTO = 5.0
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


def metricas(ret_arr, custo):
    ret_arr = np.asarray(ret_arr).ravel()
    if ret_arr is None or len(ret_arr) == 0:
        return {'n_trades': 0, 'total_pts': 0.0, 'expectancy': 0.0,
                'pf': None, 'winrate': 0.0, 'dd_pts': 0.0}
    net = ret_arr - custo
    wins = net[net > 0].sum() if (net > 0).any() else 0.0
    losses = -net[net < 0].sum() if (net < 0).any() else 0.0
    pf = (wins / losses) if losses > 0 else None
    cum = np.cumsum(net)
    dd = float((cum - np.maximum.accumulate(cum)).min())
    return {
        'n_trades': int(len(net)),
        'total_pts': round(float(net.sum()), 2),
        'expectancy': round(float(net.mean()), 4),
        'pf': round(float(pf), 4) if pf is not None else None,
        'winrate': round(float((net > 0).mean()), 4),
        'dd_pts': round(dd, 2),
    }


def run():
    t0 = time.time()
    import pickle as _pk
    with open('D:/MarketData/mimo/26/modelo_lgbm_v4_limpo.pkl', 'rb') as _f:
        blob = _pk.load(_f)
    feat_cols = list(blob['features'])
    # v9.32: preferir dataset enriquecido se existir
    import os as _os
    _path = PATH_COMPL if _os.path.exists(PATH_COMPL) else PATH
    print(f'[walk_forward] usando: {_path}')
    schema = pq.read_schema(_path)
    todas = set(schema.names)
    faltando = [f for f in feat_cols if f not in todas]
    if faltando:
        raise SystemExit('features do modelo ausentes no parquet: %s' % faltando)
    _COLS = list(dict.fromkeys(['ts_ms', 'label', 'retorno_pts',
                               'delta_preco_janela'] + feat_cols))
    df = pq.read_table(_path, columns=_COLS).to_pandas()

    ts = df['ts_ms'].to_numpy()
    local = ts - 3 * 3600 * 1000  # Brasilia sem DST (UTC-3 fixo em agosto)
    dias_idx = local // 86400000
    ordem = sorted(set(int(d) for d in dias_idx))
    data_dia = {d: (datetime.date(1970, 1, 1) + datetime.timedelta(days=d)).isoformat()
                for d in ordem}
    print('dias:', [data_dia[d] for d in ordem], flush=True)

    # v9.20: excluir linhas com vol_total < 5 (labeler forca TIMEOUT nelas,
    # criando leak vol_total->label. Somete linhas com atividade real sao validas.)
    if 'vol_total' in df.columns:
        mask_vol = df['vol_total'].to_numpy() >= 5
        n_antes = len(df)
        df = df[mask_vol].reset_index(drop=True)
        ts = df['ts_ms'].to_numpy()
        local = ts - 3 * 3600 * 1000
        dias_idx = local // 86400000
        print('vol>=5 filter:', n_antes, '->', len(df), 'linhas (' + str(round(100*len(df)/n_antes,1)) + '%)', flush=True)
    Xfull = df[feat_cols].apply(pd.to_numeric, errors='coerce').astype(np.float32)
    Xfull = Xfull.dropna(axis=1, how='all')
    Xcols = list(Xfull.columns)
    Xarr = Xfull.to_numpy()
    y = (df['label'].to_numpy() == 1).astype(np.int8)
    ret = df['retorno_pts'].to_numpy()
    delta = df['delta_preco_janela'].to_numpy()
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

        clf = LGBMClassifier(n_estimators=400, learning_rate=0.05,
                             num_leaves=63, min_child_samples=50,
                             subsample=0.8, colsample_bytree=0.8,
                             n_jobs=2, random_state=SEED, verbose=-1)
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
        r_te = ret[te_mask]
        d_te = delta[te_mask]
        n_te = int(te_mask.sum())
        n_unicos = len(np.unique(y_te))

        # modelo em varios thresholds
        modelo_por_thr = {}
        for thr in THRESHOLDS:
            sel = prob >= thr
            modelo_por_thr[str(thr)] = metricas(r_te[sel], COSTO)
        m_princ = modelo_por_thr[str(THRESH_PRINCIPAL)]
        n_alvo = m_princ['n_trades']

        # auc (independente de threshold)
        auc = round(float(roc_auc_score(y_te, prob)), 4) if n_unicos > 1 else None

        # baseline 1: entra em tudo
        b1 = metricas(r_te, COSTO)

        # baseline 3: momentum
        b3 = metricas(r_te[d_te > 0], COSTO)

        # baseline 2: aleatorio com mesmo n de trades (30 seeds)
        if n_alvo > 0 and n_alvo <= len(r_te):
            rngs = np.random.default_rng(SEED)
            exps, tots = [], []
            for s in range(30):
                ridx = rngs.choice(len(r_te), size=n_alvo, replace=False)
                exps.append(metricas(r_te[ridx], COSTO)['expectancy'])
                tots.append(metricas(r_te[ridx], COSTO)['total_pts'])
            b2 = {
                'n_trades': n_alvo,
                'expectancy_media': round(float(np.mean(exps)), 4),
                'total_pts_medio': round(float(np.mean(tots)), 2),
                'total_pts_min': round(float(np.min(tots)), 2),
                'total_pts_max': round(float(np.max(tots)), 2),
                'fracao_seeds_atras_do_modelo':
                    round(float(np.mean(np.array(tots) < m_princ['total_pts'])), 3),
            }
        else:
            b2 = {'n_trades': 0, 'expectancy_media': 0.0, 'total_pts_medio': 0.0,
                  'total_pts_min': 0.0, 'total_pts_max': 0.0,
                  'fracao_seeds_atras_do_modelo': 0.0}

        folds.append({
            'fold': len(folds) + 1,
            'treino_dias': [data_dia[d] for d in treino_dias],
            'teste_dia': data_dia[test_day],
            'n_treino': int(tr_mask.sum()),
            'n_teste': n_te,
            'auc': auc,
            'n_sinais': {k: v['n_trades'] for k, v in modelo_por_thr.items()},
            'modelo_por_threshold': modelo_por_thr,
            'baseline_threshold0': b1,
            'baseline_aleatorio30': b2,
            'baseline_momentum': b3,
        })
        with io.open(OUT, 'w', encoding='utf-8') as f:
            json.dump({'parcial': True, 'fold_atual': len(folds), 'folds': folds},
                      f, ensure_ascii=False, indent=2)
        print('fold', len(folds), data_dia[test_day], 'auc', auc,
              'modelo 0.6:', m_princ['n_trades'], 'trades,',
              m_princ['expectancy'], 'pts/trade', flush=True)

    res = {
        'descricao': 'Walk-forward limpo labels v9.14 (v9.20, v9.32 = dataset enriquecido)',
        'dataset': _path,
        'modelo': MODELO,
        'features': len(Xcols),
        'custo_pts': COSTO,
        'threshold_principal': THRESH_PRINCIPAL,
        'purge_s': PURGE_S,
        'embargo_s': EMBARGO_S,
        'target': 'binario TP-vs-nao-TP',
        'folds': folds,
    }
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print('salvo:', OUT, '| segundos:', round(time.time() - t0, 1), flush=True)


if __name__ == '__main__':
    run()
