"""
analise_redundancia.py — Detecta redundância entre features (item 25).

Não aumentar dimensionalidade sem benefício.

A abordagem:
  1. Calcula correlação de Pearson e Spearman entre todas as features.
  2. Identifica pares com |corr| > threshold (default 0.95).
  3. Calcula VIF (Variance Inflation Factor) para multicolinearidade.
  4. Sugere quais features podem ser removidas.

USO:
  python analise_redundancia.py dataset_final.parquet --threshold 0.95
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd

# features que NAO devem entrar na analise (label, identificadores, etc.)
PROIBIDAS = {'label', 'ts_ms', 'event_id', 'ativo', 'data', 'entrada',
             'outcome', 'preco_saida', 'tp_atingido', 'sl_atingido',
             'duracao_label_ms', 'retorno', 'atingido', 'saida', 'fase_sessao',
             'dias_ate_venc', 'ctx_', '_dia', '_vol_pts'}


def _filtrar_features(df):
    """Seleciona colunas numericas que NAO estao na blacklist."""
    cols = []
    for c in df.columns:
        if c in PROIBIDAS or any(p in c.lower() for p in PROIBIDAS):
            continue
        if df[c].dtype.kind in ('f', 'i'):
            cols.append(c)
    return cols


def matriz_correlacao(df, features, method='pearson', sample_size=200_000,
                      random_state=42):
    """Retorna matriz de correlacao (features x features) + pares redundantes.

    Para datasets muito grandes, amostramos aleatoriamente `sample_size`
    linhas (sem perder info estrutural sobre correlacao). O default
    e' 200k pontos, suficiente para estabilidade estatistica de Pearson.
    """
    if not features:
        return pd.DataFrame(), []
    sub = df[features].apply(pd.to_numeric, errors='coerce').dropna(axis=1, how='all')
    features = [c for c in features if c in sub.columns]
    if not features:
        return pd.DataFrame(), []
    if len(sub) > sample_size:
        sub = sub.sample(n=sample_size, random_state=random_state)
    corr = sub[features].corr(method=method)
    pares = []
    cols = corr.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if pd.notna(r) and abs(r) >= 0.95:
                pares.append((cols[i], cols[j], r))
    return corr, pares


def vif_scores(df, features, sample_size=100_000, random_state=42):
    """Calcula VIF (Variance Inflation Factor) para cada feature.
    VIF > 10 indica multicolinearidade alta.
    Usa amostragem para datasets grandes.
    """
    if not features:
        return {}
    sub = df[features].apply(pd.to_numeric, errors='coerce').dropna()
    if len(sub) > sample_size:
        sub = sub.sample(n=sample_size, random_state=random_state)
    if sub.shape[0] < 10 or sub.shape[1] < 2:
        return {}
    # padronizar
    X = (sub - sub.mean()) / sub.std(ddof=0)
    X = X.fillna(0).to_numpy()
    vifs = {}
    n_features = X.shape[1]
    cols = list(sub.columns)
    # matriz de correlacao = X^T X / n
    XtX = X.T @ X
    R = XtX / X.shape[0]
    # VIF_i = (R^{-1})_{ii}
    # usar pinv para tratar matrizes singulares (multicolinearidade perfeita)
    try:
        from numpy.linalg import pinv
        R_inv = pinv(R)
    except Exception:
        return {c: np.nan for c in cols}
    for i, col in enumerate(cols):
        v = R_inv[i, i]
        # det(R) ~ 0 indica singularidade (multicolinearidade exata)
        # nesse caso VIF = inf
        try:
            from numpy.linalg import slogdet
            sign, logdet = slogdet(R)
            if logdet < -10:  # det ~ 0 -> singular
                vifs[col] = float('inf')
                continue
        except Exception:
            pass
        if v > 50 or not np.isfinite(v):
            vifs[col] = float('inf')
        else:
            vifs[col] = v
    return vifs


def identificar_features_removiveis(pares, vifs):
    """Heuristica: para cada par redundante, remover a de MAIOR VIF
    (mais colinear com as outras). Documenta a escolha.
    """
    if not pares:
        return [], []
    vif_score = vifs if vifs else {}
    remover = set()
    raciocinio = []
    for f1, f2, r in pares:
        # manter a feature com MENOR VIF (mais unica)
        v1 = vif_score.get(f1, 1.0)
        v2 = vif_score.get(f2, 1.0)
        if v1 > v2:
            candidata = f1
        else:
            candidata = f2
        if candidata not in remover:
            remover.add(candidata)
            raciocinio.append(
                f"  {f1} (VIF={v1:.1f}) <-> {f2} (VIF={v2:.1f}) |r|={r:.3f} -> remover {candidata}"
            )
    return sorted(remover), raciocinio


def analisar(df, threshold=0.95, top_n_vif=20):
    """Analise completa: correlacao + VIF + redundancia.
    Imprime relatorio formatado e retorna dict.
    """
    features = _filtrar_features(df)
    print(f'Total de features numericas (sem proibidas): {len(features)}')

    # 1. correlacao
    print(f'\n1. Correlacao (|r| >= {threshold})')
    corr_pearson, pares_pearson = matriz_correlacao(df, features, 'pearson')
    corr_spearman, pares_spearman = matriz_correlacao(df, features, 'spearman')
    print(f'   Pearson  : {len(pares_pearson)} pares redundantes')
    print(f'   Spearman : {len(pares_spearman)} pares redundantes')

    if pares_pearson:
        print(f'\n   Top 10 pares redundantes (Pearson):')
        for f1, f2, r in sorted(pares_pearson, key=lambda x: -abs(x[2]))[:10]:
            print(f'     r={r:+.3f}  {f1}  <->  {f2}')

    # 2. VIF
    print(f'\n2. VIF (top {top_n_vif} com multicolinearidade mais alta)')
    vifs = vif_scores(df, features)
    if vifs:
        top_vif = sorted(vifs.items(), key=lambda x: -(x[1] if np.isfinite(x[1]) else 1e9))
        for f, v in top_vif[:top_n_vif]:
            flag = ' ***' if (np.isfinite(v) and v > 10) else ''
            print(f'   VIF={v:>6.1f}  {f}{flag}')

    # 3. features removiveis
    print(f'\n3. Sugestao de features removiveis (heuristica: manter a de menor VIF)')
    remover, raciocinio = identificar_features_removiveis(pares_pearson, vifs)
    print(f'   {len(remover)} feature(s) candidata(s) a remocao:')
    for r in raciocinio:
        print(r)
    print(f'\n   Lista: {remover}')

    return {
        'n_features': len(features),
        'n_pares_pearson': len(pares_pearson),
        'n_pares_spearman': len(pares_spearman),
        'pares_pearson': pares_pearson,
        'pares_spearman': pares_spearman,
        'vifs': vifs,
        'candidatas_remover': remover,
        'corr_pearson': corr_pearson,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('parquet', help='Caminho do parquet com features')
    parser.add_argument('--threshold', type=float, default=0.95)
    parser.add_argument('--top-n-vif', type=int, default=20)
    args = parser.parse_args()
    df = pd.read_parquet(args.parquet)
    analisar(df, threshold=args.threshold, top_n_vif=args.top_n_vif)
