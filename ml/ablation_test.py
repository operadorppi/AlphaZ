"""
ablation_test.py — Ablation test (item 24).

Testa INCREMENTALMENTE cada camada de features sobre o mesmo dataset,
mesmo protocolo temporal, e gera tabela de comparação.

Camadas testadas (em ordem de complexidade):
  1. baseline                : features do modelo atual (modelo_lgbm_v4_limpo.pkl)
  2. +contexto               : baseline + features de features_contexto_preco
  3. +ajuste_oficial         : +contexto + ajuste_oficial + abertura_vs_ajuste
  4. +vwap                   : anteriores + vwap + dist_vwap + cruzou_vwap
  5. +interacoes             : anteriores + aggr_x_*, cvd_x_*, imb_x_*
  6. +regime                 : todas as anteriores + regime_*
  7. +todas                  : tudo junto (ref. do item 24)

USO:
  python ablation_test.py dataset_final.parquet [--quick]

  --quick : 1 fold, sem early stopping (rápido para sanity check)
  --folds N : N folds (default = todos os dias, walk-forward completo)
"""
import os
import sys
import json
import time
import argparse
import datetime
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

from sklearn.metrics import roc_auc_score
try:
    from lightgbm import LGBMClassifier, early_stopping, log_evaluation
    LGBM_DISPONIVEL = True
except ImportError:
    LGBM_DISPONIVEL = False
    from sklearn.ensemble import RandomForestClassifier

# configs do walk-forward (item 22: manter protocolo)
TZ = 3 * 3600 * 1000
PURGE_S = 30
EMBARGO_S = 30
SEED = 42
THRESHOLDS = [0.5, 0.6, 0.7]
THRESH_PRINCIPAL = 0.6
MIN_TREINO_DIAS = 3
COSTO = 5.0

# colunas excluidas (do walk_forward_v914_limpo)
PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']


# ============================================================
#   CLASSIFICADORES DAS CAMADAS
# ============================================================
CAMADAS = {
    'baseline': {
        'descricao': 'features do modelo atual (sem contexto)',
        'filtro_extras': lambda df: df[[c for c in df.columns
                                          if c.endswith(('_v1',)) or
                                          c in ('preco_ultimo', 'aggr_imb', 'cvd_total',
                                                'vol_total', 'delta_preco_janela',
                                                'imbalance_L1', 'imbalance_L5',
                                                'microprice', 'spread', 'mid',
                                                'vel_bid_ewma', 'vel_ask_ewma',
                                                'vpin', 'hhi_book',
                                                'ewma_imb_curta', 'ewma_imb_media',
                                                'range_vol_bps')]],
    },
    'contexto': {
        'descricao': 'baseline + features de features_contexto_preco (proxy)',
        'filtro': lambda df: [c for c in df.columns
                               if c.startswith(('maxima_dia', 'minima_dia',
                                                  'dist_', 'posicao_', 'gap_',
                                                  'acima_', 'abaixo_',
                                                  'perto_', 'rompimento_',
                                                  'rejeicao_', 'range_anterior',
                                                  'abertura', 'retorno_em_relacao',
                                                  'fechamento_anterior',
                                                  'ajuste_anterior', '_norm'))
                                  and c not in ('_norm',)],
    },
    'ajuste_oficial': {
        'descricao': 'contexto + ajuste oficial B3',
        'filtro': lambda df: [c for c in df.columns if c.startswith(
            ('ajuste_anterior_oficial', 'dist_ajuste_oficial',
             'acima_ajuste_oficial', 'abaixo_ajuste_oficial',
             'abertura_vs_ajuste_oficial', 'retorno_em_relacao_ao_ajuste_oficial'))],
    },
    'vwap': {
        'descricao': 'anteriores + VWAP intraday causal',
        'filtro': lambda df: [c for c in df.columns if c.startswith(
            ('vwap', 'dist_vwap', 'acima_vwap', 'abaixo_vwap',
             'aproximando_vwap', 'afastando_vwap', 'cruzou_vwap'))],
    },
    'interacoes': {
        'descricao': 'anteriores + interacoes micro x contexto',
        'filtro': lambda df: [c for c in df.columns if '_x_' in c
                              or c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))],
    },
    'regime': {
        'descricao': 'anteriores + features de regime continuo',
        'filtro': lambda df: [c for c in df.columns if c.startswith(
            ('regime_', 'vwap_inclinacao'))],
    },
}


def _dias_idx(ts):
    local = ts - TZ
    return local // 86_400_000


def _metricas(ret_arr, custo):
    ret_arr = np.asarray(ret_arr).ravel()
    if len(ret_arr) == 0:
        return {'n_trades': 0, 'total_pts': 0.0, 'expectancy': 0.0,
                'pf': None, 'winrate': 0.0, 'dd_pts': 0.0}
    net = ret_arr - custo
    wins = net[net > 0].sum() if (net > 0).any() else 0.0
    losses = -net[net < 0].sum() if (net < 0).any() else 0.0
    pf = (wins / losses) if losses > 0 else None
    cum = np.cumsum(net)
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0
    return {
        'n_trades': int(len(net)),
        'total_pts': round(float(net.sum()), 2),
        'expectancy': round(float(net.mean()), 4),
        'pf': round(float(pf), 4) if pf is not None else None,
        'winrate': round(float((net > 0).mean()), 4),
        'dd_pts': round(dd, 2),
    }


def treinar_e_prever(Xtr, ytr, Xte, n_features):
    """Treina um classificador simples e retorna probabilidades."""
    if LGBM_DISPONIVEL:
        clf = LGBMClassifier(n_estimators=200, learning_rate=0.05,
                             num_leaves=min(63, max(7, n_features // 4)),
                             min_child_samples=50,
                             subsample=0.8, colsample_bytree=0.8,
                             n_jobs=2, random_state=SEED, verbose=-1)
        if len(Xtr) > 200:
            _split = int(len(Xtr) * 0.8)
            try:
                clf.fit(Xtr[:_split], ytr[:_split],
                        eval_set=[(Xtr[_split:], ytr[_split:])],
                        callbacks=[early_stopping(30, verbose=False),
                                   log_evaluation(0)])
            except Exception:
                clf.fit(Xtr, ytr)
        else:
            clf.fit(Xtr, ytr)
        return clf.predict_proba(Xte)[:, 1]
    else:
        clf = RandomForestClassifier(n_estimators=200, max_depth=10,
                                       min_samples_leaf=20, n_jobs=2,
                                       random_state=SEED)
        clf.fit(Xtr, ytr)
        return clf.predict_proba(Xte)[:, 1]


def preparar_features(df, layer_name, baseline_features):
    """Retorna a lista de features a usar para a camada layer_name."""
    if layer_name == 'baseline':
        return baseline_features
    extras = CAMADAS[layer_name]['filtro'](df)
    feats = list(set(baseline_features + extras))
    feats = [c for c in feats if c in df.columns]
    return feats


def run_fold(df, feats, dias, i):
    """Executa 1 fold do walk-forward. Retorna dict com metricas."""
    test_day = dias[i]
    treino_dias = dias[:i]
    if len(treino_dias) < MIN_TREINO_DIAS:
        return None
    ts = df['ts_ms'].to_numpy()
    dias_idx = _dias_idx(ts)
    b_ts = int(ts[dias_idx == test_day].min())
    tr_mask = (dias_idx < test_day) & (ts <= b_ts - PURGE_S * 1000)
    te_mask = (dias_idx == test_day) & (ts >= b_ts + EMBARGO_S * 1000)
    if tr_mask.sum() < 100 or te_mask.sum() < 10:
        return None
    X = df[feats].apply(pd.to_numeric, errors='coerce').astype('float32').fillna(0)
    y = (df['label'].to_numpy() == 1).astype('int8')
    ret = df['retorno_pts'].to_numpy() if 'retorno_pts' in df.columns else np.zeros(len(df))
    prob = treinar_e_prever(X[tr_mask].to_numpy(), y[tr_mask],
                              X[te_mask].to_numpy(),
                              n_features=len(feats))
    y_te = y[te_mask]
    r_te = ret[te_mask]
    sel = prob >= THRESH_PRINCIPAL
    m = _metricas(r_te[sel], COSTO)
    try:
        auc = float(roc_auc_score(y_te, prob)) if len(set(y_te)) > 1 else None
    except Exception:
        auc = None
    return {
        'teste_dia': (datetime.date(1970, 1, 1) +
                      datetime.timedelta(days=int(test_day))).isoformat(),
        'n_treino': int(tr_mask.sum()),
        'n_teste': int(te_mask.sum()),
        'auc': round(auc, 4) if auc is not None else None,
        **m,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('parquet', help='dataset parquet com features')
    ap.add_argument('--quick', action='store_true',
                    help='1 fold so (sanity check rapido)')
    ap.add_argument('--folds', type=int, default=None,
                    help='Numero de folds (default: todos os dias)')
    ap.add_argument('--sample', type=int, default=None,
                    help='Amostra N linhas do dataset (default: usa todas)')
    ap.add_argument('--baseline-features', default=None,
                    help='arquivo com lista de features baseline (default: do pickle)')
    args = ap.parse_args()

    print(f'Carregando {args.parquet}...')
    df = pd.read_parquet(args.parquet)
    if 'vol_total' in df.columns:
        df = df[df['vol_total'].to_numpy() >= 5].reset_index(drop=True)
    print(f'  shape inicial: {df.shape}')
    if args.sample and len(df) > args.sample:
        df = df.sample(n=args.sample, random_state=SEED).sort_values('ts_ms').reset_index(drop=True)
        print(f'  shape apos sample: {df.shape}')

    # carregar baseline features
    if args.baseline_features and os.path.exists(args.baseline_features):
        baseline = json.load(open(args.baseline_features))
    else:
        # tentar do pickle do modelo
        MODELO_PKL = r'D:/MarketData/mimo/modelo_lgbm_v4_limpo.pkl'
        if os.path.exists(MODELO_PKL):
            import pickle
            blob = pickle.load(open(MODELO_PKL, 'rb'))
            baseline = list(blob['features'])
        else:
            # fallback: detectar
            baseline = [c for c in df.columns
                        if c not in PROIBIDAS and
                        not any(p in c for p in ['ajuste_', 'vwap', 'dist_',
                                                    'acima_', 'abaixo_',
                                                    'maxima_dia', 'minima_dia',
                                                    'posicao_', 'gap_',
                                                    'perto_', 'rompimento_',
                                                    'rejeicao_',
                                                    'regime_', '_x_', 'cruzou_',
                                                    'aproximando_', 'afastando_',
                                                    'abertura_vs_', 'fechamento_anterior',
                                                    'ajuste_anterior'])]
    print(f'  baseline features: {len(baseline)}')

    # dias
    ts = df['ts_ms'].to_numpy()
    dias_idx = _dias_idx(ts)
    dias = sorted(set(int(d) for d in dias_idx))
    if args.quick:
        dias = dias[-4:]  # so os ultimos 4 dias
    if args.folds is not None:
        dias = dias[:args.folds + MIN_TREINO_DIAS]
    print(f'  dias: {len(dias)} (de {dias[0]} ate {dias[-1]})')

    # loop por camada (CUMULATIVO: cada camada inclui as features das anteriores)
    resultados = {}
    feats_acumuladas = list(baseline)  # comecamos com baseline
    ordem = ['baseline', 'contexto', 'ajuste_oficial', 'vwap', 'interacoes', 'regime']
    for layer in ordem:
        if layer == 'baseline':
            feats = list(baseline)
        else:
            extras = CAMADAS[layer]['filtro'](df)
            feats = list(set(feats_acumuladas + extras))
            feats = [c for c in feats if c in df.columns]
            feats_acumuladas = feats
        t0 = time.time()
        folds = []
        for i in range(MIN_TREINO_DIAS, len(dias)):
            f = run_fold(df, feats, dias, i)
            if f:
                folds.append(f)
        elapsed = time.time() - t0
        if not folds:
            continue
        aucs = [f['auc'] for f in folds if f['auc'] is not None]
        exps = [f['expectancy'] for f in folds]
        pfs = [f['pf'] for f in folds if f['pf'] is not None]
        ntr = sum(f['n_trades'] for f in folds)
        resultados[layer] = {
            'descricao': CAMADAS[layer]['descricao'],
            'n_features': len(feats),
            'n_folds': len(folds),
            'auc_medio': round(float(np.mean(aucs)), 4) if aucs else None,
            'expectancy_media': round(float(np.mean(exps)), 4),
            'pf_medio': round(float(np.mean(pfs)), 4) if pfs else None,
            'n_trades_total': ntr,
            'tempo_s': round(elapsed, 1),
        }
        r = resultados[layer]
        print(f'  {layer:20s}  features={r["n_features"]:4d}  '
              f'auc={r["auc_medio"]}  exp={r["expectancy_media"]:+.4f}  '
              f'pf={r["pf_medio"]}  n_trades={r["n_trades_total"]}  '
              f't={r["tempo_s"]}s')

    # tabela final
    print('\n' + '=' * 80)
    print('ABLATION TEST — cada camada adiciona features ao anterior')
    print('=' * 80)
    print(f'{"Camada":<20} {"Features":>8} {"AUC":>8} {"Expectancy":>12} '
          f'{"PF":>8} {"Trades":>8} {"Tempo":>8}')
    print('-' * 80)
    for layer in ordem:
        r = resultados.get(layer)
        if r:
            print(f'{layer:<20} {r["n_features"]:>8d} '
                  f'{str(r["auc_medio"]):>8} {r["expectancy_media"]:>+12.4f} '
                  f'{str(r["pf_medio"]):>8} {r["n_trades_total"]:>8d} '
                  f'{r["tempo_s"]:>7.1f}s')
    print('=' * 80)

    # conclusao automatica
    if 'baseline' in resultados and 'regime' in resultados:
        b = resultados['baseline']
        r = resultados['regime']
        delta_exp = r['expectancy_media'] - b['expectancy_media']
        print(f'\nDelta expectancy (regime - baseline): {delta_exp:+.4f}')
        if delta_exp > 0:
            print('  Conclusao: regime MELHOROU a expectativa media.')
        else:
            print('  Conclusao: regime NAO melhorou a expectativa media.')
            print('  Considerar: poucas features uteis, overfitting, ou ruido.')


if __name__ == '__main__':
    main()
