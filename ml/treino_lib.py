#!/usr/bin/env python3
"""
treino_lib.py — Funções compartilhadas para treino de ML.

- flatten_snapshot: achata snapshots com dicts/listas aninhadas
- split_com_purge: split temporal com purge/embargo (López de Prado)

Usado por: retreinar_sem_leak.py, treinar_modelo.py, scorer.py, dataset_builder.py
"""
import pandas as pd
import numpy as np


def flatten_snapshot(snap):
    """Achata um snapshot com nested dicts/list em um dict plano.
    
    Exemplos:
      {'imbalance': {'L1': 0.5, 'L5': 0.3}} -> {'imbalance_L1': 0.5, 'imbalance_L5': 0.3}
      {'bid_vol': [10, 20]} -> {'bid_vol_0': 10, 'bid_vol_1': 20}
    """
    flat = {}
    for k, v in snap.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                flat[f'{k}_{k2}'] = v2
        elif isinstance(v, list):
            for i, v2 in enumerate(v):
                flat[f'{k}_{i}'] = v2
        elif v is None or isinstance(v, (int, float, str, bool)):
            flat[k] = v
    return flat


def split_com_purge(df, train_pct=0.8, purge_s=5, embargo_s=30, ts_col='ts_ms'):
    """Split temporal com purge + embargo (sem leakage).
    
    - purge_s: remove linhas do FINAL do treino que estão a menos de
      purge_s segundos do início do teste (evita overlap de labels)
    - embargo_s: adiciona gap entre treino e teste (barreira temporal)
    
    Baseado em: de Prado, Advances in Financial Machine Learning, Cap. 7
    
    Args:
        df: DataFrame ordenado por tempo
        train_pct: fração do treino (default 0.8)
        purge_s: segundos de purge entre treino e teste
        embargo_s: segundos de embargo após o purge
        ts_col: coluna de timestamp em milissegundos
    
    Returns:
        (df_train, df_test) sem leakage
    """
    n = len(df)
    split_idx = int(n * train_pct)
    
    if ts_col not in df.columns:
        # Sem coluna de tempo — split simples (sem purge/embargo)
        return df.iloc[:split_idx], df.iloc[split_idx:]
    
    # Timestamp do ponto de corte
    ts_corte = df[ts_col].iloc[split_idx]
    
    # Purge: remove do treino tudo que está a menos de purge_s do corte
    purge_ms = purge_s * 1000
    embargo_ms = embargo_s * 1000
    
    # Purge: remove do treino as linhas cuja janela de label (forward-looking)
    # se estende até o período de teste — i.e. o final do treino fica purge_s
    # ANTES do corte.
    ts_purge_start = ts_corte - purge_ms

    # Embargo: o TESTE só começa embargo_s DEPOIS do corte. Antes o teste
    # iniciava em ts_corte - embargo_ms e a linha seguinte forçava
    # df_test >= ts_corte, anulando o embargo e vazando treino->teste (o
    # teste passava "vacuamente" com só o gap de purge). Agora há uma
    # barreira temporal real de (purge_s + embargo_s) entre treino e teste.
    ts_test_start = ts_corte + embargo_ms

    # Robustez: se os dados após o corte não comportam o embargo integral
    # (ex.: janela curta de teste), reduzimos o início do teste até pelo
    # menos o gap de purge (preservando a barreira temporal mínima) e
    # LOGAMOS claramente para o operador. Só falhamos de verdade se nem
    # mesmo o gap reduzido deixar linhas de teste (protegendo contra teste
    # vazio → passagem vacua que enganaria a avaliação).
    import logging as _logging
    _log_warn = _logging.getLogger(__name__).warning
    max_ts = df[ts_col].max()
    if ts_test_start > max_ts:
        ts_test_start = min(max_ts, ts_corte + purge_ms)
        _log_warn(
            "split_com_purge: embargo (%ss) maior que os dados apos o corte — "
            "teste inicia em %s (gap reduzido p/ evitar teste vazio); confira "
            "se o conjunto de teste NAO ficou pequeno demais para ser conclusivo",
            embargo_s, ts_test_start)
        if ts_test_start > max_ts:
            raise ValueError(
                f"split_com_purge: ate o gap de purge ({purge_s}s) nao deixa "
                f"linhas de teste (max_ts={max_ts}, ts_test_start={ts_test_start}). "
                f"Reduza parameters ou use mais dados."
            )

    df_train = df[df[ts_col] < ts_purge_start]
    df_test = df[df[ts_col] >= ts_test_start]
    
    return df_train, df_test


def preparar_features(df, proibidas=None, label_col='label',
                     max_card_categorica=8):
    """Prepara features para treino: remove colunas proibidas e retorna X_cols.

    v9.15: antes só incluía colunas numéricas — features categóricas de
    baixa cardinalidade (ex.: `fase_sessao`, 4 valores) eram descartadas
    silenciosamente, e o modelo nunca via essa informação. Agora colunas
    object com <= max_card_categorica valores únicos também são listadas;
    o chamador aplica encoding (ver retreinar_sem_leak/walk_forward).

    Args:
        df: DataFrame com features + label
        proibidas: lista de substrings proibidos nas colunas
        label_col: nome da coluna alvo
        max_card_categorica: cardinalidade máxima para considerar categórica

    Returns:
        X_cols: lista de colunas de features (numéricas + categóricas)
    """
    if proibidas is None:
        proibidas = [
            'label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
            'book_ts', 'ctx_', 'ativo', 'dia', 'entrada',
        ]

    X_cols = []
    for c in df.columns:
        if c == label_col:
            continue
        
        # Check if column should be excluded (exact match or prefix match)
        excluded = False
        for p in proibidas:
            if p.endswith('_'):
                # Prefix match (e.g., 'ctx_')
                if c.startswith(p):
                    excluded = True
                    break
            else:
                # Exact match
                if c == p:
                    excluded = True
                    break
        
        if excluded:
            continue
        dtype = df[c].dtype
        if dtype in ('float64', 'int64', 'float32', 'int32'):
            X_cols.append(c)
        elif dtype == object:
            # Categórica de baixa cardinalidade (v9.15)
            n_unicos = df[c].nunique(dropna=True)
            if 0 < n_unicos <= max_card_categorica:
                X_cols.append(c)
    return X_cols


def aplicar_encoding(df, cat_cols):
    """One-hot encoding de colunas categóricas (v9.15) — compatível com
    qualquer modelo (LightGBM, RF, XGBoost). Retorna df sem as colunas
    originais e com as dummies. Chamado ANTES do fillna(0)."""
    if not cat_cols:
        return df
    dummies = pd.get_dummies(df[cat_cols], prefix=cat_cols, dtype=int)
    df = df.drop(columns=cat_cols)
    return pd.concat([df, dummies], axis=1)


def avaliar_modelo(modelo, X_test, y_test, tp_pts=50, sl_pts=30, modo='binario'):
    """Avalia modelo e imprime métricas.

    modo='binario' (default): PF baseado em trades LONG ONLY (como o motor
    executa). modo='3classes': só conta corretos 1→1 e -1→-1 como ganho
    (erros de 1↔-1 contam como perda dupla).

    Returns:
        dict com acuracia, auc, profit_factor, expectancy
    """
    from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix

    y_pred = modelo.predict(X_test)

    result = {
        'acuracia': accuracy_score(y_test, y_pred),
        'auc': None,
        'profit_factor': 0,
        'expectancy': 0,
    }

    if hasattr(modelo, 'predict_proba') and len(set(y_test.tolist())) > 1:
        try:
            y_prob = modelo.predict_proba(X_test)[:, 1]
            result['auc'] = roc_auc_score(y_test, y_prob)
        except Exception:
            result['auc'] = None

    if modo == 'binario':
        cm = confusion_matrix(y_test, y_pred, labels=[0, 1])
        ganhos = (cm[1, 1] + cm[0, 0]) * tp_pts
        perdas = (cm[1, 0] + cm[0, 1]) * sl_pts
        if perdas > 0:
            result['profit_factor'] = ganhos / perdas
        result['expectancy'] = (ganhos - perdas) / max(cm.sum(), 1)
    else:
        # 3 classes: acertou 1→1 ou -1→-1 = ganhou; errou de lado (1→-1, -1→1)
        # perde os DOIS lados (TP perdido + SL tomado)
        cm = confusion_matrix(y_test, y_pred, labels=[-1, 0, 1])
        acertos = cm[0, 0] + cm[2, 2]
        inversoes = cm[0, 2] + cm[2, 0]
        erros = cm.sum() - acertos
        ganhos = acertos * tp_pts
        perdas = erros * sl_pts + inversoes * sl_pts
        result['profit_factor'] = ganhos / max(perdas, 1)
        result['expectancy'] = (ganhos - perdas) / max(cm.sum(), 1)

    return result


# Feature importances
def feature_importances(modelo, X_cols, top_n=20, importance_type='split'):
    """Retorna top N features por importância.
    Para LightGBM, permite escolher entre 'split' e 'gain'."""
    if hasattr(modelo, 'booster_'):
        # LightGBM (Sklearn wrapper) - extração direta via booster
        vals = modelo.booster_.feature_importance(importance_type=importance_type)
        imp = pd.Series(vals, index=X_cols)
    elif hasattr(modelo, 'feature_importances_'):
        # Fallback para RandomForest ou outros modelos Sklearn
        imp = pd.Series(modelo.feature_importances_, index=X_cols)
    else:
        return pd.Series(dtype=float)
    return imp.sort_values(ascending=False).head(top_n)
