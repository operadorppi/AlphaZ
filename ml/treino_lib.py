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


def split_com_purge(df, train_pct=0.8, purge_s=5, embargo_s=30, ts_col='ts_ms',
                    retornar_politica=False, exigir_integral=False):
    """Split temporal com purge + embargo (sem leakage).
    
    - purge_s: remove linhas do FINAL do treino que estão a menos de
      purge_s segundos do início do teste (evita overlap de labels)
    - embargo_s: adiciona gap entre treino e teste (barreira temporal)
    
    Baseado em: de Prado, Advances in Financial Machine Learning, Cap. 7
    
    P1-A32 (v15.29): embargo SOLICITADO != embargo REALIZADO nunca e
    adaptacao silenciosa. Quando os dados apos o corte nao comportam o
    embargo integral:
      - default (retornar_politica=False): mantem o fallback operacional
        (gap minimo de purge) mas LOGANDO claramente o estado
        INCONCLUSIVO — o retorno NUNCA deve ser reportado como
        "embargo integral/sem leakage" nesse caso;
      - retornar_politica=True: retorna tambem um dict com a politica
        realizada (embargo_solicitado/realizado, status OK ou
        EMBARGO_REDUZIDO) para o chamador rotular a validacao;
      - exigir_integral=True: NAO adapta — levanta ValueError
        (VALIDACAO INCONCLUSIVA) se o embargo solicitado nao couber.

    Args:
        df: DataFrame ordenado por tempo
        train_pct: fração do treino (default 0.8)
        purge_s: segundos de purge entre treino e teste
        embargo_s: segundos de embargo após o purge
        ts_col: coluna de timestamp em milissegundos
        retornar_politica: se True, retorna (train, test, politica)
        exigir_integral: se True, falha quando o embargo nao cabe
    
    Returns:
        (df_train, df_test) sem leakage — ou (df_train, df_test, politica)
        quando retornar_politica=True
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

    # P1-A32 (v15.29): embargo SOLICITADO != embargo REALIZADO. Se os dados
    # após o corte não comportam o embargo integral, NUNCA adaptar em
    # silencio:
    #   - exigir_integral=True -> VALIDACAO INCONCLUSIVA (falha explicita)
    #   - senao -> fallback operacional (gap minimo de purge) logado e com a
    #     politica realizada exposta via retornar_politica, para o chamador
    #     rotular o resultado (nunca "embargo integral aplicado").
    import logging as _logging
    _log_warn = _logging.getLogger(__name__).warning
    max_ts = df[ts_col].max()
    embargo_realizado_s = float(embargo_s)
    if ts_test_start > max_ts:
        if exigir_integral:
            raise ValueError(
                "split_com_purge: VALIDACAO INCONCLUSIVA — embargo solicitado "
                f"({embargo_s}s) NAO cabe apos o corte (dados terminam em "
                f"{max_ts}, teste precisaria comecar em {ts_test_start}). Exija "
                "mais dados ou reduza o embargo EXPLICITAMENTE.")
        ts_test_start = min(max_ts, ts_corte + purge_ms)
        embargo_realizado_s = max(0.0, (ts_test_start - ts_corte) / 1000.0)
        _log_warn(
            "split_com_purge: VALIDACAO INCONCLUSIVA — embargo solicitado "
            "(%ss) > dados apos o corte; embargo REALIZADO=%ss (gap minimo de "
            "purge). Nao reporte este split como 'embargo integral/sem leakage'. "
            "Use exigir_integral=True para falhar explicitamente.",
            embargo_s, embargo_realizado_s)

    df_train = df[df[ts_col] < ts_purge_start]
    df_test = df[df[ts_col] >= ts_test_start]

    if retornar_politica:
        _integral = bool(embargo_realizado_s >= embargo_s - 1e-9)
        politica = {
            'purge_solicitado_s': purge_s,
            'embargo_solicitado_s': embargo_s,
            'embargo_realizado_s': round(float(embargo_realizado_s), 3),
            'embargo_integral': _integral,
            'status': ('OK' if _integral else 'EMBARGO_REDUZIDO'),
            'ts_corte': int(ts_corte),
            'ts_test_start_real': int(ts_test_start),
        }
        return df_train, df_test, politica

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
    originais e com as dummies. Chamado ANTES do fillna(0).

    ATENCAO (P1-A16): esta versao so deve ser usada quando o encoding e
    feito num UNICO dataframe (sem split treino/teste). Para splits, use
    aplicar_encoding_fit() — nunca concatene treino+teste antes do
    get_dummies (as categorias do teste vazariam para a preparacao)."""
    if not cat_cols:
        return df
    dummies = pd.get_dummies(df[cat_cols], prefix=cat_cols, dtype=int)
    df = df.drop(columns=cat_cols)
    return pd.concat([df, dummies], axis=1)


def aplicar_encoding_fit(X_train, X_test, cat_cols):
    """One-hot com FIT apenas no TREINO (P1-A16).

    pd.get_dummies sobre treino+teste concatenados deixava as categorias do
    TESTE influenciarem as colunas dummies (ex.: categoria presente so no
    teste criava coluna visivel ao modelo) — leak de preprocessing. Aqui:

      fit  (categorias) -> X_train
      transform        -> X_train e X_test com as MESMAS colunas

    Categorias do teste ausentes no treino sao descartadas (invisiveis ao
    modelo, como em producao); categorias do treino ausentes no teste viram 0.

    Returns:
        (X_train_encoded, X_test_encoded) sem as colunas originais
    """
    if not cat_cols:
        return X_train, X_test
    dummies_train = pd.get_dummies(X_train[cat_cols], prefix=cat_cols, dtype=int)
    dummies_test = pd.get_dummies(X_test[cat_cols], prefix=cat_cols, dtype=int)
    # Alinhar colunas do teste EXATAMENTE as do treino (fit) — categorias do
    # teste desconhecidas no treino sao descartadas; ausentes viram 0.
    dummies_test = dummies_test.reindex(columns=dummies_train.columns, fill_value=0)
    Xt = X_train.drop(columns=cat_cols).reset_index(drop=True)
    Xe = X_test.drop(columns=cat_cols).reset_index(drop=True)
    return (
        pd.concat([Xt, dummies_train.reset_index(drop=True)], axis=1),
        pd.concat([Xe, dummies_test.reset_index(drop=True)], axis=1),
    )


def avaliar_modelo(modelo, X_test, y_test, tp_pts=50, sl_pts=30, modo='binario'):
    """Avalia modelo e retorna métricas de CLASSIFICAÇÃO (não P&L).

    NOTA: Profit Factor (PF) NÃO é calculado aqui.
    O PF só pode ser medido via simulação de execução real no
    replay_engine.py, com regras de 1 trade por vez, TP/SL, slippage e custo.
    Aqui, FP/FN são previsões erradas, não trades perdidos.
    TN é "não trade" — não gera P&L.

    Returns:
        dict com acuracia, auc, ece, precision, recall, n_trades_previstos
    """
    from sklearn.metrics import (
        accuracy_score, roc_auc_score, confusion_matrix,
        precision_score, recall_score, f1_score,
    )

    y_pred = modelo.predict(X_test)

    result = {
        'acuracia': accuracy_score(y_test, y_pred),
        'auc': None,
        'profit_factor': None,  # Calcular no replay_engine.py
        'expectancy': None,     # Calcular no replay_engine.py
        'precision': None,
        'recall': None,
        'f1': None,
        'n_trades_previstos': int(y_pred.sum() if hasattr(y_pred, 'sum') else 0),
    }

    if hasattr(modelo, 'predict_proba') and len(set(y_test.tolist())) > 1:
        try:
            y_prob = modelo.predict_proba(X_test)[:, 1]
            result['auc'] = roc_auc_score(y_test, y_prob)
            # ECE (Expected Calibration Error)
            bins = np.linspace(0, 1, 11)
            ece = 0.0
            for lo, hi in zip(bins[:-1], bins[1:]):
                mask = (y_prob >= lo) & (y_prob < hi)
                if mask.sum() > 0:
                    acc_bin = y_test[mask].mean()
                    conf_bin = y_prob[mask].mean()
                    ece += mask.sum() / len(y_test) * abs(acc_bin - conf_bin)
            result['ece'] = round(float(ece), 4)
        except Exception:
            result['auc'] = None

    result['precision'] = round(float(precision_score(y_test, y_pred, zero_division=0)), 4)
    result['recall'] = round(float(recall_score(y_test, y_pred, zero_division=0)), 4)
    result['f1'] = round(float(f1_score(y_test, y_pred, zero_division=0)), 4)

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
