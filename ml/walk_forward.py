#!/usr/bin/env python3
"""
walk_forward.py — Validação temporal (out-of-sample) com split por data.

Treina em N dias, testa nos M seguintes — a evidência que falta para
responder "o modelo generaliza para dados não vistos?"

Uso:
  python walk_forward.py                                # dataset_final.parquet, 7/3
  python walk_forward.py --dataset meu_dataset.parquet --treino 7 --teste 3
  python walk_forward.py --dataset meu_dataset.parquet --treino-datas 20260810,20260816 --teste-datas 20260817,20260819

Saída:
  walk_forward_resultado.json — métricas + feature importances
  Além disso imprime o resumo no terminal.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date, timedelta
from config import CONFIG as _CFG

try:
    from zoneinfo import ZoneInfo
    FUSO_BR = ZoneInfo('America/Sao_Paulo')
except Exception:
    FUSO_BR = None

SAVE_DIR_DEFAULT = r'D:\MarketData\mimo'
ATIVO_DEFAULT = 'WINV26'
N_TREINO_DEFAULT = 7
N_TESTE_DEFAULT = 3


def _ts_para_data(ts_ms):
    """Converte epoch ms → data YYYYMMDD no fuso Brasília."""
    from datetime import datetime as dt
    d = dt.utcfromtimestamp(ts_ms / 1000)
    if FUSO_BR is not None:
        d = d.replace(tzinfo=__import__('datetime').timezone.utc).astimezone(FUSO_BR)
    return d.strftime('%Y%m%d')


def split_temporal(df, n_teste_dias=3, col_data='_data'):
    """Divide dataset por data: treino = todas menos as últimas M datas."""
    datas = sorted(set(df[col_data].unique()))
    if len(datas) < n_teste_dias + 1:
        return None, None, datas, f'poucas datas: {len(datas)} (precisa >= {n_teste_dias + 1})'
    teste_datas = set(datas[-n_teste_dias:])
    treino = df[~df[col_data].isin(teste_datas)].copy()
    teste = df[df[col_data].isin(teste_datas)].copy()
    return treino, teste, datas, None


def main():
    ap = argparse.ArgumentParser(description='Walk-forward temporal')
    ap.add_argument('--dataset', default=None,
                    help=f'Parquet (default {SAVE_DIR_DEFAULT}\\dataset_final.parquet)')
    ap.add_argument('--ativo', default=ATIVO_DEFAULT)
    ap.add_argument('--treino', type=int, default=N_TREINO_DEFAULT,
                    help='Nº de dias para treino (default 7)')
    ap.add_argument('--teste', type=int, default=N_TESTE_DEFAULT,
                    help='Nº de dias para teste (default 3)')
    ap.add_argument('--treino-datas', default=None,
                    help='Especificar datas de treino (YYYYMMDD,YYYYMMDD...)')
    ap.add_argument('--teste-datas', default=None,
                    help='Especificar datas de teste (YYYYMMDD,YYYYMMDD...)')
    ap.add_argument('--output', default='walk_forward_resultado.json',
                    help='Arquivo de saída com métricas (JSON)')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    dataset_path = args.dataset or str(Path(SAVE_DIR_DEFAULT) / 'dataset_final.parquet')
    print(f'Carregando {dataset_path}...')
    import pandas as pd
    df = pd.read_parquet(dataset_path)
    print(f'  Total: {len(df):,} linhas')

    # Filtra ativo
    df = df[df['ativo'] == args.ativo].copy()
    print(f'  Ativo {args.ativo}: {len(df):,} linhas')

    # Converte ts_ms → data local
    df['_data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
    if FUSO_BR is not None:
        df['_data'] = df['_data'].dt.tz_convert(FUSO_BR)
    df['_data'] = df['_data'].dt.strftime('%Y%m%d')

    # Split
    if args.treino_datas and args.teste_datas:
        treino_datas = set(args.treino_datas.split(','))
        teste_datas = set(args.teste_datas.split(','))
        treino = df[df['_data'].isin(treino_datas)].copy()
        teste = df[df['_data'].isin(teste_datas)].copy()
        datas = sorted(treino_datas | teste_datas)
        erro = None
        if len(treino) < 10:
            erro = f'Treino com apenas {len(treino)} linhas — poucos dados'
    else:
        n_teste = min(args.teste, len(df['_data'].unique()) - 1)
        n_treino = min(args.treino, len(df['_data'].unique()) - n_teste)
        treino, teste, datas, erro = split_temporal(df, n_teste, '_data')

    if erro or treino is None or teste is None:
        print(f'[ERRO] Split inválido: {erro}')
        print(f'  Datas disponíveis: {sorted(df["_data"].unique())}')
        sys.exit(1)

    # Prepara features (exclui colunas não-feature)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ml.treino_lib import preparar_features, avaliar_modelo, feature_importances

    X_cols = preparar_features(df)
    X_cols = [c for c in X_cols if c in treino.columns]
    print(f'Features: {len(X_cols)}')

    # Modo binário (compra vs não-compra) — mesmo padrão do retreinar
    train = treino[treino['label'] != 0].copy()
    test = teste[teste['label'] != 0].copy()
    if len(train) < 10 or len(test) < 10:
        print(f'[ERRO] Poucas amostras com label não-zero: treino={len(train)} teste={len(test)}')
        sys.exit(1)

    y_train = (train['label'] == 1).astype(int)
    y_test = (test['label'] == 1).astype(int)
    X_train = train[X_cols].copy()
    X_test = test[X_cols].copy()
    # v9.15: one-hot de categóricas (fase_sessao etc.)
    cat_cols = [c for c in X_cols if X_train[c].dtype == object]
    if cat_cols:
        from ml.treino_lib import aplicar_encoding
        n_train = len(X_train)
        combinado = pd.concat([X_train[cat_cols], X_test[cat_cols]], ignore_index=True)
        combinado = aplicar_encoding(combinado, cat_cols)
        X_train = pd.concat(
            [X_train.drop(columns=cat_cols).reset_index(drop=True),
             combinado.iloc[:n_train].reset_index(drop=True)], axis=1)
        X_test = pd.concat(
            [X_test.drop(columns=cat_cols).reset_index(drop=True),
             combinado.iloc[n_train:].reset_index(drop=True)], axis=1)
    X_train = X_train.fillna(0)
    X_test = X_test.fillna(0)

    print(f'\nSplit temporal:')
    print(f'  Datas treino ({len(datas) - len(set(teste["_data"]))}): '
          f'{datas[:-args.teste]}')
    print(f'  Datas teste ({args.teste}): {datas[-args.teste:]}')
    print(f'  Treino: {len(X_train):,} | Teste: {len(X_test):,}')

    # Treino
    try:
        import lightgbm as lgb
        print('\n>>> Treinando LightGBM (out-of-sample)...')
        modelo = lgb.LGBMClassifier(
            n_estimators=300, learning_rate=0.05, max_depth=6,
            num_leaves=31, min_child_samples=20,
            subsample=0.8, colsample_bytree=0.8,
            class_weight='balanced', random_state=args.seed, verbose=-1,
        )
        modelo.fit(X_train, y_train)
        nome = 'LightGBM'
    except ImportError:
        from sklearn.ensemble import RandomForestClassifier
        print('\n>>> LightGBM indisponivel. Usando RandomForest...')
        modelo = RandomForestClassifier(
            n_estimators=300, max_depth=10,
            class_weight='balanced', random_state=args.seed, n_jobs=1
        )
        modelo.fit(X_train, y_train)
        nome = 'RandomForest'

    # Avaliação (out-of-sample!)
    print(f'\n{"="*60}')
    print(f'OUT-OF-SAMPLE — {nome} | Ativo: {args.ativo}')
    print(f'{"="*60}')
    tp_pts = _CFG["trading"].get("tp_pts", 100)
    sl_pts = _CFG["trading"].get("sl_pts", 50)
    result = avaliar_modelo(modelo, X_test, y_test, tp_pts=tp_pts, sl_pts=sl_pts)
    print(f'Acuracia:  {result["acuracia"]:.4f}')
    if result.get('auc') is not None:
        print(f'AUC-ROC:   {result["auc"]:.4f}')
    if result.get('ece') is not None:
        print(f'ECE:       {result["ece"]:.4f}')
    if result.get('precision') is not None:
        print(f'Precision: {result["precision"]:.4f}')
    if result.get('recall') is not None:
        print(f'Recall:    {result["recall"]:.4f}')
    pf = result.get('profit_factor')
    print(f'Profit Factor: {pf:.2f}' if pf else 'Profit Factor: [replay_engine.py]')
    exp = result.get('expectancy')
    print(f'Expectancy:    {exp:+.1f} pts' if exp is not None else 'Expectancy:    [replay_engine.py]')
    print(f'Total trades previstos: {result.get("n_trades_previstos", "?")}')

    imp = feature_importances(modelo, X_cols, top_n=15)
    imp_dict = imp.to_dict() if hasattr(imp, 'to_dict') else dict(imp)
    print(f'\nTop 15 features:')
    for f, v in imp.items():
        print(f'  {f:35s} {v:.4f}')

    # Salva resultado
    resultado = {
        'modelo': nome,
        'ativo': args.ativo,
        'datas_treino': list(datas[:-args.teste]),
        'datas_teste': list(datas[-args.teste:]),
        'n_treino': len(X_train),
        'n_teste': len(X_test),
        'features': len(X_cols),
        'metricas': result,
        'feature_importances': imp_dict,
    }
    out_path = Path(args.output)
    if out_path.parent:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f'\nResultado salvo: {out_path}')


if __name__ == '__main__':
    main()