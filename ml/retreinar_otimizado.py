# -*- coding: utf-8 -*-
"""
ml/retreinar_otimizado.py — Retreina LightGBM com 17 features otimizadas.

Features removidas (9):
  - kyle_kyle_n (0% importance, é contagem de amostras)
  - vp_poc_acima (0% importance, redundante com vp_poc_dist)
  - taxa_eventos (0.5%, r=1.000 com n_eventos_janela)
  - cvd_div (0.9%, baixa importância)
  - entropy_compra (1.9%, r=-0.976 com hhi_compra)
  - entropy_venda (1.7%, r=-0.976 com hhi_venda)
  - vol_compra (1.6%, r=0.817 com vol_total)
  - vol_venda (1.8%, r=0.833 com vol_total)
  - ewma_imb_curta (1.0%, r=0.854 com aggr_imb)

Resultado do ablation test: zero perda de performance (AUC=0.7225 idêntico).
"""

import sys
import os
import pickle
import time
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score, confusion_matrix
from datetime import date, datetime

# === 17 FEATURES OTIMIZADAS ===
FEATURES_OTIMIZADAS = [
    # Trade features (7)
    'n_eventos_janela',
    'vol_total',
    'aggr_imb',
    'ewma_imb_longa',
    'hhi_compra',
    'hhi_venda',
    'delta_preco_janela',
    
    # Book features (1)
    'vpin',
    
    # Price features (1)
    'preco_ultimo',
    
    # Volume features (2)
    'cvd_total',
    'realized_vol_bps',
    
    # Volatility features (1)
    'range_vol_bps',
    
    # Volume Profile features (4)
    'vp_poc_dist',
    'vp_vah_dist',
    'vp_val_dist',
    'vp_vp_total',
    
    # Cross-asset features (1)
    'kyle_kyle_lambda',
]

# Paths
DATASET = r'D:\MarketData\mimo\dataset_final.parquet'  # v12.1: pipeline multi-ativo
MODELO_OUT = r'D:\MarketData\mimo\26\modelo_lgbm_v5_otimizado.pkl'
SAVE_DIR = r'D:\MarketData\mimo'


def calcular_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error."""
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
    print('=' * 60)
    print('RETREINO OTIMIZADO — 17 FEATURES')
    print('=' * 60)
    
    # Carregar dados
    print(f'\nCarregando dataset: {DATASET}')
    
    # v11.19: Validar hash do dataset
    import hashlib
    sha256 = hashlib.sha256()
    with open(DATASET, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    current_hash = sha256.hexdigest()
    print(f'  Hash: {current_hash[:16]}...')
    
    # Verificar se modelo antigo é válido
    if os.path.exists(MODELO_OUT):
        import pickle
        with open(MODELO_OUT, 'rb') as f:
            old_blob = pickle.load(f)
        old_hash = old_blob.get('dataset_hash', '')
        if old_hash and old_hash != current_hash:
            print(f'  [WARNING] Dataset mudou desde o ultimo treino!')
            print(f'    Anterior: {old_hash[:16]}...')
            print(f'    Atual:    {current_hash[:16]}...')
        else:
            print(f'  Dataset consistente com modelo anterior')
    
    df = pd.read_parquet(DATASET)
    df = df[df['ativo'] == 'WINV26'].copy()
    df['data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.date
    df = df.sort_values('ts_ms').reset_index(drop=True)
    print(f'  Total: {len(df)} linhas, {len(df["data"].unique())} dias')
    
    # Verificar que todas as features existem
    missing = [f for f in FEATURES_OTIMIZADAS if f not in df.columns]
    if missing:
        print(f'  [ERRO] Features ausentes: {missing}')
        sys.exit(1)
    print(f'  Features validadas: {len(FEATURES_OTIMIZADAS)}/{len(FEATURES_OTIMIZADAS)}')
    
    # Split temporal (último dia = teste)
    dias = sorted(df['data'].unique())
    dia_teste = dias[-1]
    df_train = df[df['data'] < dia_teste]
    df_test = df[df['data'] == dia_teste]
    
    print(f'\nSplit:')
    print(f'  TREINO: {len(df_train)} linhas ({df_train["data"].min()} a {df_train["data"].max()})')
    print(f'  TESTE:  {len(df_test)} linhas ({dia_teste})')
    
    # v11.21: Modo regressão (--regression) ou classificação (default)
    regression_mode = '--regression' in sys.argv
    
    if regression_mode:
        # REGRESSÃO: y = retorno_pts (contínuo)
        print(f'\nModo: REGRESSÃO (target = retorno_pts)')
        train = df_train.copy()
        test = df_test.copy()
        
        X_train = train[FEATURES_OTIMIZADAS].fillna(0)
        y_train = train['retorno_pts'].fillna(0)
        X_test = test[FEATURES_OTIMIZADAS].fillna(0)
        y_test = test['retorno_pts'].fillna(0)
        
        print(f'  Treino: {len(y_train)} linhas')
        print(f'  Teste:  {len(y_test)} linhas')
        print(f'  y_train: mean={y_train.mean():.1f}, std={y_train.std():.1f}, min={y_train.min():.0f}, max={y_train.max():.0f}')
    else:
        # CLASSIFICAÇÃO: y = label binário (1=TP, 0=neutro/SL)
        train = df_train[df_train['label'] != 0]
        test = df_test[df_test['label'] != 0]
        
        X_train = train[FEATURES_OTIMIZADAS].fillna(0)
        y_train = (train['label'] == 1).astype(int)
        X_test = test[FEATURES_OTIMIZADAS].fillna(0)
        y_test = (test['label'] == 1).astype(int)
        
        print(f'\nModo: CLASSIFICAÇÃO (target = label binário)')
        print(f'  Treino: {y_train.sum()}/{len(y_train)} positivos ({y_train.mean()*100:.1f}%)')
        print(f'  Teste:  {y_test.sum()}/{len(y_test)} positivos ({y_test.mean()*100:.1f}%)')
    
    # Treinar
    print(f'\n--- TREINANDO LightGBM ---')
    import lightgbm as lgb
    
    t0 = time.time()
    if regression_mode:
        modelo = lgb.LGBMRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )
    else:
        modelo = lgb.LGBMClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight='balanced',
            random_state=42,
            verbose=-1,
        )
    
    # Early stopping com validation split
    idx = np.arange(len(X_train))
    np.random.seed(42)
    np.random.shuffle(idx)
    split = int(len(idx) * 0.8)
    tr_idx = idx[:split]
    val_idx = idx[split:]
    
    modelo.fit(
        X_train.iloc[tr_idx], y_train.iloc[tr_idx],
        eval_set=[(X_train.iloc[val_idx], y_train.iloc[val_idx])],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    )
    
    tempo = time.time() - t0
    print(f'  Treinado em {tempo:.1f}s')
    print(f'  Melhor iteração: {modelo.best_iteration_}')
    
    # Avaliar
    print(f'\n--- AVALIAÇÃO ---')
    
    if regression_mode:
        # REGRESSÃO: métricas de regressão
        y_pred = modelo.predict(X_test)
        
        from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        mae = mean_absolute_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)
        
        # Simular trades: entrar se predição > 5, sair se predição < -5
        tradesEntrada = y_pred > 5
        tradesSaida = y_pred < -5
        n_tradesEntrada = tradesEntrada.sum()
        n_tradesSaida = tradesSaida.sum()
        
        # P&L simulado
        pnlEntrada = y_test[tradesEntrada].sum() if n_tradesEntrada > 0 else 0
        pnlSaida = (-y_test[tradesSaida]).sum() if n_tradesSaida > 0 else 0
        pnl_total = pnlEntrada + pnlSaida
        
        print(f'\n  RMSE:      {rmse:.2f} pts')
        print(f'  MAE:       {mae:.2f} pts')
        print(f'  R²:        {r2:.4f}')
        print(f'  Trades C:  {n_tradesEntrada} (PnL: {pnlEntrada:+.0f} pts)')
        print(f'  Trades V:  {n_tradesSaida} (PnL: {pnlSaida:+.0f} pts)')
        print(f'  PnL total: {pnl_total:+.0f} pts')
        print(f'  Predição:  mean={y_pred.mean():.1f}, std={np.std(y_pred):.1f}')
        
        acc = 0.0  # não aplicável
        auc = 0.0  # não aplicável
        ece = 0.0  # não aplicável
        pf = pnl_total / max(abs(pnlSaida), 1) if pnlSaida != 0 else 0
    else:
        # CLASSIFICAÇÃO: métricas de classificação
        y_prob = modelo.predict_proba(X_test)[:, 1]
        y_pred = (y_prob >= 0.5).astype(int)
        
        acc = accuracy_score(y_test, y_pred)
        auc = roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else 0.5
        ece = calcular_ece(y_test, y_prob)
        
        cm = confusion_matrix(y_test, y_pred)
        tn, fp, fn, tp = cm.ravel()
        
        ganhos = tp * 50
        perdas = (fp + fn) * 30
        pf = ganhos / perdas if perdas > 0 else 0.0
        
        print(f'\n  Accuracy:  {acc:.4f}')
        print(f'  AUC:       {auc:.4f}')
        print(f'  ECE:       {ece:.4f}')
        print(f'  Profit F.: {pf:.2f}')
        print(f'  Trades:    {tp + fp} (TP={tp}, FP={fp}, FN={fn}, TN={tn})')
    
    # Feature importance
    if hasattr(modelo, 'feature_importances_'):
        imp = pd.Series(modelo.feature_importances_, index=FEATURES_OTIMIZADAS)
        imp = imp.sort_values(ascending=False)
        total = imp.sum()
        print(f'\n  Top 10 features:')
        for i, (feat, val) in enumerate(imp.head(10).items(), 1):
            pct = val / total * 100
            print(f'    {i:2d}. {feat:30s} {val:8.0f} ({pct:4.1f}%)')
    
    # Comparar com modelo anterior (só em modo classificação)
    if not regression_mode:
        print(f'\n--- COMPARAÇÃO ---')
        old_path = r'D:\MarketData\mimo\26\modelo_lgbm_v4_limpo.pkl'
        if os.path.exists(old_path):
            with open(old_path, 'rb') as f:
                old_blob = pickle.load(f)
            old_model = old_blob['modelo']
            old_features = old_blob['features']
            
            X_test_old = test[[c for c in old_features if c in test.columns]].fillna(0)
            if hasattr(old_model, 'predict_proba'):
                p_old = old_model.predict_proba(X_test_old)[:, 1]
                acc_old = accuracy_score(y_test, (p_old >= 0.5).astype(int))
                auc_old = roc_auc_score(y_test, p_old) if len(set(y_test)) > 1 else 0.5
                ece_old = calcular_ece(y_test, p_old)
                
                print(f'\n  {"Métrica":>15} {"v4 (26 feat)":>14} {"v5 (17 feat)":>14} {"Delta":>10}')
                print(f'  {"-"*55}')
                print(f'  {"Features":>15} {len(old_features):>14} {len(FEATURES_OTIMIZADAS):>14} {len(FEATURES_OTIMIZADAS)-len(old_features):>+10}')
                print(f'  {"Accuracy":>15} {acc_old:>14.4f} {acc:>14.4f} {acc-acc_old:>+10.4f}')
                print(f'  {"AUC":>15} {auc_old:>14.4f} {auc:>14.4f} {auc-auc_old:>+10.4f}')
                print(f'  {"ECE":>15} {ece_old:>14.4f} {ece:>14.4f} {ece-ece_old:>+10.4f}')
    
    # Salvar modelo
    print(f'\n--- SALVANDO ---')
    model_meta = {
        'modelo': modelo,
        'features': FEATURES_OTIMIZADAS,
        'mode': 'regression' if regression_mode else 'classification',
        'metricas': {
            'rmse': round(rmse, 4) if regression_mode else None,
            'mae': round(mae, 4) if regression_mode else None,
            'r2': round(r2, 4) if regression_mode else None,
            'accuracy': round(acc, 4),
            'auc_roc': round(auc, 4),
            'ece': round(ece, 4),
            'profit_factor': round(pf, 2),
            'pnl_total': round(pnl_total, 1) if regression_mode else None,
        },
    }
    if not regression_mode:
        model_meta['classes'] = list(modelo.classes_)
    with open(MODELO_OUT, 'wb') as f:
        pickle.dump({**model_meta,
            'features_removidas': [
                'kyle_kyle_n', 'vp_poc_acima', 'taxa_eventos', 'cvd_div',
                'entropy_compra', 'entropy_venda', 'vol_compra', 'vol_venda',
                'ewma_imb_curta',
            ],
            'motivo_remocao': {
                'kyle_kyle_n': '0% importance (sample count)',
                'vp_poc_acima': '0% importance (redundant with vp_poc_dist)',
                'taxa_eventos': '0.5% (r=1.000 with n_eventos_janela)',
                'cvd_div': '0.9% (low importance)',
                'entropy_compra': '1.9% (r=-0.976 with hhi_compra)',
                'entropy_venda': '1.7% (r=-0.976 with hhi_venda)',
                'vol_compra': '1.6% (r=0.817 with vol_total)',
                'vol_venda': '1.8% (r=0.833 with vol_total)',
                'ewma_imb_curta': '1.0% (r=0.854 with aggr_imb)',
            },
            'version': '5.0.0',
            'train_date': str(date.today()),
            'dataset_hash': current_hash,
        }, f)
    
    print(f'  Modelo salvo: {MODELO_OUT}')
    print(f'  Tamanho: {os.path.getsize(MODELO_OUT) / 1024:.0f} KB')
    
    print(f'\n{"=" * 60}')
    if regression_mode:
        print(f'RESUMO REGRESSÃO: 17 features, RMSE={rmse:.2f}, R²={r2:.4f}, PnL={pnl_total:+.0f}')
    else:
        print(f'RESUMO CLASSIFICAÇÃO: 17 features, AUC={auc:.4f}, Acc={acc*100:.1f}%')
    print(f'{"=" * 60}')


if __name__ == '__main__':
    main()
