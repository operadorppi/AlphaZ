#!/usr/bin/env python3
"""
comparar_contexto_preco.py — Validação estatística: baseline vs baseline + contexto de preço.

Executa walk-forward com E sem features de contexto de preço,
gera a tabela comparativa (item 18) e extrai feature importance.

USO:
  python ml/comparar_contexto_preco.py --dataset D:/MarketData/mimo/26/dataset_final_v2_win_v914.parquet
  python ml/comparar_contexto_preco.py --dataset D:/MarketData/mimo/26/dataset_final_completo.parquet

Se o dataset NÃO tiver as features de contexto (colunas começando com
dist_, posicao_, gap_, acima_, abaixo_, perto_, rompimento_, rejeicao_),
ele automaticamente as gera usando features_contexto_preco + features_contexto_avancado.

SAÍDA:
  comparacao_baseline_vs_contexto.json
  comparacao_tabela.md   (tabela Markdown pronta para copiar)
  feature_importance.json
"""

import io
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
#   CONFIG
# ============================================================
DATASET_DEFAULT = r'D:\MarketData\mimo\26\dataset_final_completo.parquet'
OUT_DIR = os.environ.get('SINAL_RT_DIR', r'D:\MarketData\mimo\26')

COSTO = 5.0
PURGE_S = 30
EMBARGO_S = 30
SEED = 42
MIN_TREINO_DIAS = 3

THRESH_PRINCIPAL = 0.6
THRESHOLDS = [0.5, 0.6, 0.7]

PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']

# Padrões de colunas de contexto de preço
CTX_PATTERNS = [
    'dist_', 'posicao_range', 'gap_', 'acima_', 'abaixo_',
    'perto_', 'rompimento_', 'rejeicao_', 'range_anterior',
    'faixa_', 'abertura_vs_', 'delta_x_',
    # ajuste oficial
    'ajuste_anterior_oficial', 'dist_ajuste_oficial_',
    'acima_ajuste_oficial', 'abaixo_ajuste_oficial',
    'abertura_vs_ajuste_oficial',
    # VWAP
    'vwap', 'dist_vwap_', 'acima_vwap', 'abaixo_vwap',
    'aproximando_vwap', 'afastando_vwap', 'cruzou_vwap',
    # regime
    'regime_', 'vwap_inclinacao',
    # interações
    '_x_', 'aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_',
    'aggr_imb_x_', 'cvd_norm_x_', 'imb_L5_x_',
]


def eh_feature_contexto(col):
    """True se a coluna parece ser uma feature de contexto de preço."""
    c = col.lower()
    return any(pat in c for pat in CTX_PATTERNS)


def metricas(ret_arr, custo):
    ret_arr = np.asarray(ret_arr).ravel()
    if ret_arr is None or len(ret_arr) == 0:
        return {'n_trades': 0, 'total_pts': 0.0, 'expectancy': 0.0,
                'pf': None, 'winrate': 0.0, 'dd_pts': 0.0,
                'auc': None}
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


def run_walk_forward(df, feat_cols, label='baseline'):
    """Executa walk-forward e retorna métricas agregadas + folds.
    
    OTIMIZAÇÃO MEMÓRIA: extrai arrays numpy coluna-a-coluna em vez de
    df.apply() que cria cópia float64 inteira.
    """
    from sklearn.metrics import roc_auc_score

    # Filtrar colunas que existem no df
    cols_ok = [c for c in feat_cols if c in df.columns and c not in PROIBIDAS]
    if not cols_ok:
        print(f'  [{label}] Nenhuma feature válida encontrada!')
        return None, [], []

    # Extrair y, ret, ts como numpy
    y_all = (df['label'].to_numpy() == 1).astype(np.int8)
    ret_all = df['retorno_pts'].to_numpy()
    ts_all = df['ts_ms'].to_numpy()

    # vol_total filter
    if 'vol_total' in df.columns:
        mask_vol = df['vol_total'].to_numpy() >= 5
        y_all = y_all[mask_vol]
        ret_all = ret_all[mask_vol]
        ts_all = ts_all[mask_vol]
        df = df[mask_vol].reset_index(drop=True)

    # Construir X coluna-a-coluna (memória-eficiente)
    arr_cols = []
    Xcols = []
    for c in cols_ok:
        col = pd.to_numeric(df[c], errors='coerce').astype(np.float32).to_numpy()
        if np.all(np.isnan(col)):
            continue
        arr_cols.append(col)
        Xcols.append(c)
    if not arr_cols:
        print(f'  [{label}] Nenhuma feature válida após filtro NaN!')
        return None, [], []
    Xarr = np.column_stack(arr_cols).astype(np.float32)

    ts = ts_all
    local = ts - 3 * 3600 * 1000
    dias_idx = local // 86400000
    ordem = sorted(set(int(d) for d in dias_idx))
    data_dia = {d: (datetime(1970, 1, 1) + timedelta(days=d)).isoformat()
                for d in ordem}

    y = y_all
    ret = ret_all

    try:
        from lightgbm import LGBMClassifier
        import lightgbm as lgb
        MODELO = 'LightGBM'
    except ImportError:
        from sklearn.ensemble import RandomForestClassifier
        MODELO = 'RandomForest'

    folds = []
    fold_importances = []

    for i in range(MIN_TREINO_DIAS, len(ordem)):
        test_day = ordem[i]
        treino_dias = ordem[:i]
        b_ts = int(ts[dias_idx == test_day].min())
        tr_mask = (dias_idx < test_day) & (ts <= b_ts - PURGE_S * 1000)
        te_mask = (dias_idx == test_day) & (ts >= b_ts + EMBARGO_S * 1000)

        n_tr = int(tr_mask.sum())
        n_te = int(te_mask.sum())
        if n_tr < 100 or n_te < 10:
            continue

        if MODELO == 'LightGBM':
            clf = LGBMClassifier(n_estimators=400, learning_rate=0.05,
                                 num_leaves=63, min_child_samples=50,
                                 subsample=0.8, colsample_bytree=0.8,
                                 n_jobs=2, random_state=SEED, verbose=-1)
            _tr_idx = np.where(tr_mask)[0]
            _split = int(len(_tr_idx) * 0.8)
            _tr_final = _tr_idx[:_split]
            _val_idx = _tr_idx[_split:]
            clf.fit(Xarr[_tr_final], y[_tr_final],
                    eval_set=[(Xarr[_val_idx], y[_val_idx])],
                    callbacks=[lgb.early_stopping(50, verbose=False),
                               lgb.log_evaluation(0)])
        else:
            from sklearn.ensemble import RandomForestClassifier  # noqa: F811 (fallback condicional)
            clf = RandomForestClassifier(n_estimators=200, max_depth=12,
                                         n_jobs=2, random_state=SEED)
            clf.fit(Xarr[tr_mask], y[tr_mask])

        prob = clf.predict_proba(Xarr[te_mask])[:, 1]
        y_te = y[te_mask]
        r_te = ret[te_mask]
        n_unicos = len(np.unique(y_te))

        auc = round(float(roc_auc_score(y_te, prob)), 4) if n_unicos > 1 else None

        sel = prob >= THRESH_PRINCIPAL
        m = metricas(r_te[sel], COSTO)
        m['auc'] = auc
        m['teste_dia'] = data_dia[test_day]
        m['n_teste'] = n_te
        folds.append(m)

        # Feature importance (último fold para simplificar)
        if hasattr(clf, 'feature_importances_'):
            fi = dict(zip(Xcols, clf.feature_importances_.tolist()))
            fold_importances.append(fi)

    # Agregar folds
    if not folds:
        return None, [], []

    agg = {
        'label': label,
        'modelo': MODELO,
        'n_folds': len(folds),
        'features': len(Xcols),
        'n_trades_total': sum(f['n_trades'] for f in folds),
        'expectancy_media': round(float(np.mean([f['expectancy'] for f in folds if f['n_trades'] > 0])), 4),
        'pf_medio': round(float(np.nanmean([f['pf'] if f['pf'] is not None else np.nan for f in folds])), 4),
        'winrate_medio': round(float(np.mean([f['winrate'] for f in folds if f['n_trades'] > 0])), 4),
        'auc_medio': round(float(np.nanmean([f['auc'] if f['auc'] is not None else np.nan for f in folds])), 4),
        'total_pts_soma': round(float(sum(f['total_pts'] for f in folds)), 2),
        'expectancy_por_dia': folds,
    }
    return agg, folds, fold_importances


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', default=DATASET_DEFAULT)
    args = parser.parse_args()

    print(f'Carregando: {args.dataset}')
    import pyarrow.parquet as pq
    df = pq.read_table(args.dataset).to_pandas()
    print(f'  {len(df)} linhas, {len(df.columns)} colunas')

    # Verificar colunas essenciais
    required = ['ts_ms', 'label', 'retorno_pts', 'preco_ultimo']
    for c in required:
        if c not in df.columns:
            print(f'ERRO: coluna "{c}" ausente no dataset')
            return

    # Identificar features de contexto
    cols_nao_label = [c for c in df.columns if c not in PROIBIDAS
                      and c not in ('ts_ms', 'preco_ultimo', 'ativo',
                                    'book_ts', 'book_mid')]
    ctx_cols = [c for c in cols_nao_label if eh_feature_contexto(c)]
    base_cols = [c for c in cols_nao_label if not eh_feature_contexto(c)]

    print(f'\nFeatures base (microestrutura): {len(base_cols)}')
    print(f'Features contexto de preço: {len(ctx_cols)}')
    if ctx_cols:
        print(f'  Exemplos: {ctx_cols[:5]}...')

    # Se não há features de contexto, gerar
    if not ctx_cols:
        print('\nSem features de contexto no dataset. Gerando...')
        from features_contexto_preco import adicionar_contexto_preco
        from features_contexto_avancado import adicionar_interacoes_micro_contexto

        df = adicionar_contexto_preco(df)
        df = adicionar_interacoes_micro_contexto(df)

        cols_nao_label = [c for c in df.columns if c not in PROIBIDAS
                          and c not in ('ts_ms', 'preco_ultimo', 'ativo',
                                        'book_ts', 'book_mid')]
        ctx_cols = [c for c in cols_nao_label if eh_feature_contexto(c)]
        base_cols = [c for c in cols_nao_label if not eh_feature_contexto(c)]
        print(f'  Geradas {len(ctx_cols)} features de contexto')

    # ---- RUN 1: BASELINE (só microestrutura) ----
    print('\n' + '=' * 60)
    print('RUN 1: BASELINE (features de microestrutura)')
    print('=' * 60)
    t0 = time.time()
    agg_base, folds_base, fi_base = run_walk_forward(df, base_cols, label='baseline')
    print(f'  Tempo: {time.time() - t0:.1f}s')
    if agg_base:
        print(f'  Folds: {agg_base["n_folds"]}')
        print(f'  Expectancy média: {agg_base["expectancy_media"]} pts')
        print(f'  PF médio: {agg_base["pf_medio"]}')
        print(f'  AUC médio: {agg_base["auc_medio"]}')
        print(f'  Winrate: {agg_base["winrate_medio"]}')

    # ---- RUN 2: BASELINE + CONTEXTO ----
    print('\n' + '=' * 60)
    print('RUN 2: BASELINE + CONTEXTO DE PREÇO')
    print('=' * 60)
    t0 = time.time()
    all_cols = base_cols + ctx_cols
    agg_ctx, folds_ctx, fi_ctx = run_walk_forward(df, all_cols, label='baseline+contexto')
    print(f'  Tempo: {time.time() - t0:.1f}s')
    if agg_ctx:
        print(f'  Folds: {agg_ctx["n_folds"]}')
        print(f'  Expectancy média: {agg_ctx["expectancy_media"]} pts')
        print(f'  PF médio: {agg_ctx["pf_medio"]}')
        print(f'  AUC médio: {agg_ctx["auc_medio"]}')
        print(f'  Winrate: {agg_ctx["winrate_medio"]}')

    # ---- TABELA COMPARATIVA ----
    print('\n' + '=' * 60)
    print('TABELA COMPARATIVA (item 18)')
    print('=' * 60)

    def _v(d, k):
        return d.get(k, '-') if d else '-'

    tabela = f"""| Modelo | Features | Accuracy | AUC | PF | Expectancy | Total Pts |
|--------|----------|----------|-----|-----|-----------|-----------|
| Baseline | {base_cols[0][:20]}... ({len(base_cols)}) | {_v(agg_base, 'winrate_medio')} | {_v(agg_base, 'auc_medio')} | {_v(agg_base, 'pf_medio')} | {_v(agg_base, 'expectancy_media')} | {_v(agg_base, 'total_pts_soma')} |
| Novo | {ctx_cols[0][:20]}...+base ({len(all_cols)}) | {_v(agg_ctx, 'winrate_medio')} | {_v(agg_ctx, 'auc_medio')} | {_v(agg_ctx, 'pf_medio')} | {_v(agg_ctx, 'expectancy_media')} | {_v(agg_ctx, 'total_pts_soma')} |
"""
    print(tabela)

    # ---- FEATURE IMPORTANCE ----
    print('\n' + '=' * 60)
    print('FEATURE IMPORTANCE (item 19)')
    print('=' * 60)

    if fi_base:
        avg_base = {}
        for fi in fi_base:
            for k, v in fi.items():
                avg_base[k] = avg_base.get(k, 0) + v
        avg_base = {k: v / len(fi_base) for k, v in avg_base.items()}
        top_base = sorted(avg_base.items(), key=lambda x: -x[1])[:15]
        print('\nTOP 15 FEATURES EXISTENTES:')
        for name, imp in top_base:
            print(f'  {name:45s} {imp:.0f}')

    if fi_ctx:
        avg_ctx = {}
        for fi in fi_ctx:
            for k, v in fi.items():
                avg_ctx[k] = avg_ctx.get(k, 0) + v
        avg_ctx = {k: v / len(fi_ctx) for k, v in avg_ctx.items()}

        # Separar novas vs existentes
        novas = {k: v for k, v in avg_ctx.items() if k in ctx_cols}
        top_novas = sorted(novas.items(), key=lambda x: -x[1])[:15]
        print('\nTOP 15 FEATURES NOVAS (contexto de preço):')
        for name, imp in top_novas:
            print(f'  {name:45s} {imp:.0f}')

        # Verificar redundância
        print('\nAnálise de redundância:')
        base_set = set(avg_base.keys())
        ctx_set = set(novas.keys())
        overlap = base_set & ctx_set
        if overlap:
            print(f'  Colunas em ambos: {len(overlap)} (verificar se são mesmo novas)')

    # ---- SALVAR ----
    resultado = {
        'comparacao': {
            'baseline': agg_base,
            'novo': agg_ctx,
        },
        'tabela': tabela,
        'contexto': {
            'n_features_base': len(base_cols),
            'n_features_ctx': len(ctx_cols),
            'features_ctx': ctx_cols,
        },
    }
    out_json = os.path.join(OUT_DIR, 'comparacao_baseline_vs_contexto.json')
    with io.open(out_json, 'w', encoding='utf-8') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f'\nSalvo: {out_json}')

    out_md = os.path.join(OUT_DIR, 'comparacao_tabela.md')
    with io.open(out_md, 'w', encoding='utf-8') as f:
        f.write('# Comparação: Baseline vs Contexto de Preço\n\n')
        f.write(tabela)
        f.write(f'\nGerado: {datetime.now().isoformat()}\n')
    print(f'Salvo: {out_md}')

    # Feature importance
    fi_data = {}
    if fi_base:
        fi_data['base_top15'] = top_base if 'top_base' in dir() else []
    if fi_ctx and 'top_novas' in dir():
        fi_data['novas_top15'] = top_novas
    fi_data['media_base'] = avg_base if 'avg_base' in dir() else {}
    fi_data['media_ctx'] = avg_ctx if 'avg_ctx' in dir() else {}
    fi_out = os.path.join(OUT_DIR, 'feature_importance.json')
    with io.open(fi_out, 'w', encoding='utf-8') as f:
        json.dump(fi_data, f, ensure_ascii=False, indent=2)
    print(f'Salvo: {fi_out}')


if __name__ == '__main__':
    main()
