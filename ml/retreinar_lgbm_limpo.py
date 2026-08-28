#!/usr/bin/env python3
"""
retreinar_lgbm_limpo.py — Retreina LightGBM SEM leakage.

Comparacao:
  ANTIGO: 24 features (inclui preco_saida, duracao_label_ms) = LEAKAGE
  NOVO:   22 features (sem leakage) = LIMPO
"""
import sys, os, json, pickle, time, argparse
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix
from datetime import date

PARQUET_PATH = r'D:\MarketData\mimo\dataset_final_v2_win_v914.parquet'
# v9.32: dataset enriquecido com ajuste oficial + VWAP + regime
PARQUET_PATH_COMPL = r'D:\MarketData\mimo\26\dataset_final_completo.parquet'
OLD_MODEL_PATH = r'D:\MarketData\mimo\modelo_lgbm_v3.pkl'
NEW_MODEL_PATH = r'D:\MarketData\mimo\modelo_lgbm_v4_limpo.pkl'

# Features com leakage (BLOQUEADAS)
LEAKAGE_FEATURES = {'preco_saida', 'duracao_label_ms'}

# Split temporal
TREINO_DIAS = [date(2026, 8, d) for d in [4, 5, 6, 7]]
CAL_DIAS = [date(2026, 8, d) for d in [10, 11]]
TEST_DIAS = [date(2026, 8, d) for d in [13, 14]]

# Blacklist de colunas
PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']


def colunas_validas(df):
    """Retorna features numericas sem leakage."""
    return [c for c in df.columns
            if df[c].dtype.kind in ('f', 'i')
            and c not in LEAKAGE_FEATURES
            and not any(p in c.lower() for p in PROIBIDAS)]


def calcular_ece(y_true, y_proba, n_bins=10):
    y_bin = (np.array(y_true) == 1).astype(int)
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (y_proba >= bins[i]) & (y_proba < bins[i+1])
        if mask.sum() > 0:
            bin_acc = y_bin[mask].mean()
            bin_conf = y_proba[mask].mean()
            ece += mask.sum() / len(y_bin) * abs(bin_acc - bin_conf)
    return ece


def main():
    ap = argparse.ArgumentParser(description='Retreina LightGBM sem leakage (com gate de qualidade)')
    ap.add_argument('--gate-dias', default=None,
                    help='Dias uteis (CSV) para o gate de qualidade; aborta (exit 2) se algum reprovar')
    ap.add_argument('--save-dir', default=os.environ.get('SINAL_RT_DIR', r'D:\MarketData\mimo'))
    ap.add_argument('--dataset', default=os.environ.get('DATASET_PARQUET', PARQUET_PATH))
    ap.add_argument('--usar-complemento', action='store_true', default=False,
                    help='Se o dataset_completo (com VWAP, ajuste, regime) existir, usar')
    ap.add_argument('--modelo-out', default=os.environ.get('ML_MODELO', NEW_MODEL_PATH))
    ap.add_argument('--ativo', default='WINV26')
    args = ap.parse_args()

    # Gate de qualidade (protege o retreino contra dias com problema)
    if args.gate_dias:
        from relatorio_diario import validar_dia as _validar_dia
        print('=' * 60)
        print('GATE DE QUALIDADE (--gate-dias)')
        print('=' * 60)
        ok = True
        for d in [x.strip() for x in args.gate_dias.split(',') if x.strip()]:
            info = _validar_dia(args.save_dir, d)
            if info['problemas']:
                ok = False
                print(f'[GATE] Dia {d}: PROBLEMAS')
                for p in info['problemas']:
                    print(f'  - {p}')
            else:
                print(f'[GATE] Dia {d}: OK')
        if not ok:
            print('[GATE] Reprovado — abortando retreino (exit 2)')
            sys.exit(2)

    print('='*60)
    print('RETREINO LightGBM — SEM LEAKAGE')
    print('='*60)

    # Carregar dados
    print(f'\nCarregando dataset: {args.dataset}')
    # v9.32: se --usar-complemento, preferir dataset_final_completo (mais features)
    if args.usar_complemento and os.path.exists(PARQUET_PATH_COMPL):
        print(f'  (v9.32: usando dataset enriquecido: {PARQUET_PATH_COMPL})')
        df = pd.read_parquet(PARQUET_PATH_COMPL)
    else:
        df = pd.read_parquet(args.dataset)
    df = df[df['ativo'] == args.ativo].copy()
    df['data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.date
    df = df.sort_values('ts_ms').reset_index(drop=True)
    print(f'  Total: {len(df)} linhas, {len(df["data"].unique())} dias')
    
    # Split
    df_train = df[df['data'].isin(TREINO_DIAS)].copy()
    df_cal = df[df['data'].isin(CAL_DIAS)].copy()
    df_test = df[df['data'].isin(TEST_DIAS)].copy()
    
    print(f'\nSplit:')
    print(f'  TREINO: {len(df_train)} linhas')
    print(f'  CAL:    {len(df_cal)} linhas')
    print(f'  TESTE:  {len(df_test)} linhas')
    
    # Features limpas (SEM leakage)
    X_cols = colunas_validas(df)
    print(f'\nFeatures validas: {len(X_cols)}')
    
    # Labels: binario (TP=1, nao-TP=0)
    y_col = 'label'
    
    # ============================================================
    # MODELO ANTIGO (com leakage)
    # ============================================================
    print('\n--- MODELO ANTIGO (com leakage) ---')
    with open(OLD_MODEL_PATH, 'rb') as f:
        old_blob = pickle.load(f)
    old_model = old_blob['modelo']
    old_features = old_blob['features']
    print(f'  Features: {len(old_features)}')
    print(f'  Tipo: {type(old_model).__name__}')
    
    # Avaliar antigo no teste
    # O modelo antigo usa features que existem no parquet
    X_test_old = df_test[[c for c in old_features if c in df_test.columns]].fillna(0)
    y_test = df_test[y_col].astype(int)
    y_test_bin = (y_test == 1).astype(int)
    
    if hasattr(old_model, 'predict_proba'):
        p_old = old_model.predict_proba(X_test_old)[:, 1]
        ece_old = calcular_ece(y_test, p_old)
        acc_old = accuracy_score(y_test_bin, (p_old >= 0.5).astype(int))
        try:
            auc_old = roc_auc_score(y_test_bin, p_old)
        except:
            auc_old = None
        print(f'  ECE: {ece_old:.4f}')
        print(f'  Acc: {acc_old:.4f}')
        print(f'  AUC: {auc_old:.4f}' if auc_old else '  AUC: N/A')
        print(f'  Prob media: {p_old.mean():.4f}')
    
    # ============================================================
    # TREINAR NOVO MODELO (SEM leakage)
    # ============================================================
    print('\n--- NOVO MODELO (sem leakage) ---')
    
    X_train = df_train[X_cols].fillna(0)
    y_train = df_train[y_col].astype(int)
    
    print(f'  Treino: {len(X_train)} linhas, {len(X_cols)} features')
    print(f'  Labels: {dict(y_train.value_counts().sort_index())}')
    
    try:
        from lightgbm import LGBMClassifier
        print('  Usando LightGBM...')
        t0 = time.time()
        new_model = LGBMClassifier(
            n_estimators=400, max_depth=8, learning_rate=0.1,
            min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
            class_weight='balanced', random_state=42, n_jobs=-1,
            verbose=-1
        )
        # v9.30: early stopping — 80/20 train/val
        import lightgbm as lgb
        import numpy as np
        _idx = np.arange(len(X_train))
        np.random.seed(42)
        np.random.shuffle(_idx)
        _split = int(len(_idx) * 0.8)
        _tr = _idx[:_split]
        _val = _idx[_split:]
        new_model.fit(X_train.iloc[_tr], y_train.iloc[_tr],
                      eval_set=[(X_train.iloc[_val], y_train.iloc[_val])],
                      callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])
        print(f'  Early stopping: melhor iteracao = {new_model.best_iteration_}')
        print(f'  Treinado em {time.time()-t0:.1f}s')
    except ImportError:
        print('  LightGBM nao disponivel, usando RandomForest...')
        from sklearn.ensemble import RandomForestClassifier
        t0 = time.time()
        new_model = RandomForestClassifier(
            n_estimators=200, max_depth=10, min_samples_split=20,
            min_samples_leaf=10, class_weight='balanced',
            random_state=42, n_jobs=-1
        )
        new_model.fit(X_train, y_train)
        print(f'  Treinado em {time.time()-t0:.1f}s')
    
    # Avaliar novo
    X_test_new = df_test[X_cols].fillna(0)
    p_new = new_model.predict_proba(X_test_new)[:, 1]
    ece_new = calcular_ece(y_test, p_new)
    acc_new = accuracy_score(y_test_bin, (p_new >= 0.5).astype(int))
    try:
        auc_new = roc_auc_score(y_test_bin, p_new)
    except:
        auc_new = None
    
    print(f'\n  ECE: {ece_new:.4f}')
    print(f'  Acc: {acc_new:.4f}')
    print(f'  AUC: {auc_new:.4f}' if auc_new else '  AUC: N/A')
    print(f'  Prob media: {p_new.mean():.4f}')
    
    # ============================================================
    # COMPARACAO
    # ============================================================
    print('\n' + '='*60)
    print('COMPARACAO: ANTIGO (leakage) vs NOVO (limpo)')
    print('='*60)
    print(f'\n{"Metrica":>15} {"Antigo":>12} {"Novo":>12} {"Delta":>12}')
    print('-' * 55)
    print(f'{"Features":>15} {len(old_features):>12} {len(X_cols):>12} {len(X_cols)-len(old_features):>+12}')
    print(f'{"ECE":>15} {ece_old:>12.4f} {ece_new:>12.4f} {ece_new-ece_old:>+12.4f}')
    print(f'{"Accuracy":>15} {acc_old:>12.4f} {acc_new:>12.4f} {acc_new-acc_old:>+12.4f}')
    if auc_old and auc_new:
        print(f'{"AUC":>15} {auc_old:>12.4f} {auc_new:>12.4f} {auc_new-auc_old:>+12.4f}')
    
    # Feature importance
    if hasattr(new_model, 'feature_importances_'):
        imp = pd.Series(new_model.feature_importances_, index=X_cols).sort_values(ascending=False)
        print(f'\nTop 10 features (novo):')
        for f, v in imp.head(10).items():
            print(f'  {f:30s} {v:.4f}')
    
    # ============================================================
    # SALVAR
    # ============================================================
    with open(args.modelo_out, 'wb') as f:
        pickle.dump({
            'modelo': new_model,
            'features': X_cols,
            'classes': [-1, 0, 1],
            'split': {
                'treino': [str(d) for d in TREINO_DIAS],
                'cal': [str(d) for d in CAL_DIAS],
                'teste': [str(d) for d in TEST_DIAS],
            },
            'metricas': {
                'ece': round(ece_new, 4),
                'accuracy': round(acc_new, 4),
                'auc': round(auc_new, 4) if auc_new else None,
            },
            'leakage_removido': ['preco_saida', 'duracao_label_ms'],
        }, f)
    print(f'\nNovo modelo salvo: {args.modelo_out}')


if __name__ == '__main__':
    main()
