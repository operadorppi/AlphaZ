"""
validar_contexto_preco.py — Validação estatística OOS (itens 17-20).

Compara, NO MESMO protocolo temporal do walk_forward_v914_limpo
(purge 30s / embargo 30s, folds expansivos, custo 5 pts, thresholds
0.5/0.6/0.7, LightGBM seed 42):

  Baseline : features do modelo atual (modelo_lgbm_v4_limpo.pkl)
  Novo     : Baseline + contexto de preço (features_contexto_preco)

O dataset de entrada já traz ts_ms/ativo/preco_ultimo, então o contexto
é adicionado DIRETAMENTE ao parquet existente (sem rerodar o pipeline).
O baseline usa o walk_forward_v914_limpo.json já calculado; só o NOVO é
treinado aqui (mesmos dias/folds, para alinhamento dia a dia).

Saída: tabela comparativa (AUC/Acc/PF/Expectancy/Sharpe-por-dia),
importância de features (top existentes vs top novas) e conclusão.
"""
import io
import json
import os
import time
import datetime
import warnings
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from walk_forward_v914_limpo import metricas, PROIBIDAS, PURGE_S, EMBARGO_S, COSTO, SEED, THRESHOLDS, THRESH_PRINCIPAL, MIN_TREINO_DIAS
from features_contexto_preco import adicionar_contexto_preco

BASE_PATH = 'D:/MarketData/mimo/dataset_final.parquet'
BASELINE_JSON = 'walk_forward_v914_limpo.json'
MODELO_PKL = 'D:/MarketData/mimo/modelo_lgbm_v4_limpo.pkl'
NOVO_JSON = os.path.join(os.environ.get('TEMP', '/tmp'), 'walk_forward_contexto.json')
TZ = 3 * 3600 * 1000


def _dias(ts):
    local = ts - TZ
    return local // 86400000


def _novo_feats(df):
    if os.path.exists(MODELO_PKL):
        blob = __import__('pickle').load(open(MODELO_PKL, 'rb'))
        base = list(blob['features'])
    else:
        base = [c for c in df.columns if c not in PROIBIDAS]
    contextas = [c for c in df.columns if c not in PROIBIDAS and c not in base]
    return base, base + contextas


def rodar_walk_forward(df, feats, out_path):
    Xfull = df[feats].apply(pd.to_numeric, errors='coerce').astype(np.float32)
    Xfull = Xfull.dropna(axis=1, how='all')
    Xcols = list(Xfull.columns)
    Xarr = Xfull.to_numpy()
    y = (df['label'].to_numpy() == 1).astype(np.int8)
    ret = df['retorno_pts'].to_numpy()
    delta = df['delta_preco_janela'].to_numpy() if 'delta_preco_janela' in df.columns else np.zeros(len(df))

    ts = df['ts_ms'].to_numpy()
    dias_idx = _dias(ts)
    ordem = sorted(set(int(d) for d in dias_idx))
    data_dia = {d: (datetime.date(1970, 1, 1) + datetime.timedelta(days=d)).isoformat() for d in ordem}

    from lightgbm import LGBMClassifier, early_stopping, log_evaluation
    folds = []
    for i in range(MIN_TREINO_DIAS, len(ordem)):
        test_day = ordem[i]
        treino_dias = ordem[:i]
        b_ts = int(ts[dias_idx == test_day].min())
        tr_mask = (dias_idx < test_day) & (ts <= b_ts - PURGE_S * 1000)
        te_mask = (dias_idx == test_day) & (ts >= b_ts + EMBARGO_S * 1000)
        if tr_mask.sum() < 100 or te_mask.sum() < 10:
            continue
        clf = LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=63,
                             min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                             n_jobs=2, random_state=SEED, verbose=-1)
        _tr = np.where(tr_mask)[0]
        _split = int(len(_tr) * 0.8)
        clf.fit(Xarr[_tr[:_split]], y[_tr[:_split]],
                eval_set=[(Xarr[_tr[_split:]], y[_tr[_split:]])],
                callbacks=[early_stopping(50, verbose=False), log_evaluation(0)])
        prob = clf.predict_proba(Xarr[te_mask])[:, 1]
        y_te = y[te_mask]
        r_te = ret[te_mask]
        d_te = delta[te_mask]
        modelo_por_thr = {str(t): metricas(r_te[prob >= t], COSTO) for t in THRESHOLDS}
        m = modelo_por_thr[str(THRESH_PRINCIPAL)]
        auc = round(float(roc_auc_score(y_te, prob)), 4) if len(set(y_te)) > 1 else None
        folds.append({
            'teste_dia': data_dia[test_day], 'n_teste': int(te_mask.sum()),
            'auc': auc, 'modelo_por_threshold': modelo_por_thr,
            'baseline_momentum': metricas(r_te[d_te > 0], COSTO),
        })
        print('fold', len(folds), data_dia[test_day], 'auc', auc, 'exp', m['expectancy'], flush=True)

    with io.open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'folds': folds, 'features': len(Xcols)}, f, ensure_ascii=False, indent=2)
    return folds, Xcols


def agregar(folds, thr=str(THRESH_PRINCIPAL)):
    aus = [f['auc'] for f in folds if f['auc'] is not None]
    exps = [f['modelo_por_threshold'][thr]['expectancy'] for f in folds]
    pfs = [f['modelo_por_threshold'][thr]['pf'] for f in folds if f['modelo_por_threshold'][thr]['pf'] is not None]
    ntr = [f['modelo_por_threshold'][thr]['n_trades'] for f in folds]
    return {
        'auc_medio': round(float(np.nanmean(aus)), 4) if aus else None,
        'expectancy_medio': round(float(np.nanmean(exps)), 4),
        'pf_medio': round(float(np.nanmean(pfs)), 4) if pfs else None,
        'n_trades_total': int(sum(ntr)),
        'n_folds': len(folds),
    }


def main():
    t0 = time.time()
    print('Carregando dataset base:', BASE_PATH, flush=True)
    df = pd.read_parquet(BASE_PATH)
    if 'vol_total' in df.columns:
        df = df[df['vol_total'].to_numpy() >= 5].reset_index(drop=True)
    print('linhas:', len(df), '| colunas:', len(df.columns), flush=True)

    print('Adicionando contexto de preço (causal)...', flush=True)
    df2 = adicionar_contexto_preco(df)
    print('colunas apos contexto:', len(df2.columns), flush=True)

    base_feats, novo_feats = _novo_feats(df2)
    print('baseline feats:', len(base_feats), '| novo feats:', len(novo_feats), flush=True)

    # Baseline: MESMO dataset e MESMO protocolo (features do modelo atual),
    # para comparação 100% apples-to-apples com o novo conjunto.
    print('Rodando baseline (mesmo dataset, features atuais)...', flush=True)
    base_folds, _ = rodar_walk_forward(df, base_feats, BASELINE_JSON + '.tmp')

    print('Rodando NOVO (baseline + contexto)...', flush=True)
    novo_folds, Xcols = rodar_walk_forward(df2, novo_feats, NOVO_JSON)

    ag_base = agregar(base_folds)
    ag_novo = agregar(novo_folds)

    print('\n' + '=' * 78)
    print('COMPARAÇÃO  baseline  vs  baseline+contexto  (thr=%s, custo=%s)' % (THRESH_PRINCIPAL, COSTO))
    print('=' * 78)
    print('{:<14}{:>16}{:>18}{:>14}{:>14}'.format('Modelo', 'AUC', 'Expectancy', 'PF', 'Trades'))
    print('-' * 78)
    print('{:<14}{:>16}{:>18}{:>14}{:>14}'.format(
        'Baseline', str(ag_base['auc_medio']), str(ag_base['expectancy_medio']),
        str(ag_base['pf_medio']), str(ag_base['n_trades_total'])))
    print('{:<14}{:>16}{:>18}{:>14}{:>14}'.format(
        'Novo', str(ag_novo['auc_medio']), str(ag_novo['expectancy_medio']),
        str(ag_novo['pf_medio']), str(ag_novo['n_trades_total'])))
    print('=' * 78)
    delta_exp = ag_novo['expectancy_medio'] - ag_base['expectancy_medio']
    print('Δ Expectancy (novo - base):', round(delta_exp, 4))
    print('Δ AUC (novo - base):', (round(ag_novo['auc_medio'] - ag_base['auc_medio'], 4)
          if ag_novo['auc_medio'] and ag_base['auc_medio'] else None))

    # Estabilidade por ativo: contagem de dias com novo > base em expectancy
    n_base = len(base_folds)
    n_novo = len(novo_folds)
    if n_base == n_novo:
        melhores = sum(1 for a, b in zip(novo_folds, base_folds)
                        if a['modelo_por_threshold'][str(THRESH_PRINCIPAL)]['expectancy'] >
                        b['modelo_por_threshold'][str(THRESH_PRINCIPAL)]['expectancy'])
        print('Dias com NOVO melhor que BASE: %d / %d (%.0f%%)' % (melhores, n_base, 100*melhores/n_base))

    # Importância de features (último fold) — top existentes vs top novas
    print('\nImportância de features (modelo sobre todos os dias menos o último):', flush=True)
    from lightgbm import LGBMClassifier, early_stopping, log_evaluation
    ts = df2['ts_ms'].to_numpy(); dias_idx = _dias(ts); ordem = sorted(set(int(d) for d in dias_idx))
    last = ordem[-1]; tr = (dias_idx < last)
    Xf = df2[novo_feats].apply(pd.to_numeric, errors='coerce').astype(np.float32)
    yall = (df2['label'].to_numpy() == 1).astype(np.int8)
    clf = LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=63,
                         min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                         n_jobs=2, random_state=SEED, verbose=-1)
    clf.fit(Xf.to_numpy()[tr], yall[tr])
    imp = pd.Series(clf.feature_importances_, index=novo_feats).sort_values(ascending=False)
    context_feats = [c for c in novo_feats if c not in base_feats]
    top_exist = [c for c in imp.index if c in base_feats][:15]
    top_novas = [c for c in imp.index if c in context_feats][:15]
    print('  TOP EXISTENTES:', top_exist)
    print('  TOP NOVAS    :', top_novas)
    # novas com importância ~0 (candidatas a descartar)
    zero_novas = [c for c in context_feats if imp.get(c, 0) < 1e-6]
    print('  NOVAS com importância ~0 (descartar):', len(zero_novas), 'de', len(context_feats))

    print('\nTempo:', round(time.time() - t0, 1), 's')
    # Conclusão
    if delta_exp > 0 and (ag_novo['auc_medio'] or 0) >= (ag_base['auc_medio'] or 0):
        print('CONCLUSÃO: contexto de preço AGREGA (expectancy e/ou AUC melhores). Manter no modelo.')
    else:
        print('CONCLUSÃO: contexto NÃO melhorou (ou piorou). NÃO forçar inclusão; '
              'rever features de baixa importância e reavaliar.')


if __name__ == '__main__':
    main()
