#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_ml_rapida.py — Auditoria rápida da ML.
"""
import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA ML - VERSÃO RÁPIDA')
    print('='*70)
    
    save_dir = Path(r'D:\MarketData\mimo')
    
    # 1. Modelos
    print('\n[1] MODELOS...')
    modelo_files = list(save_dir.rglob('modelo_*.pkl'))
    print(f'  Modelos: {len(modelo_files)}')
    for f in sorted(modelo_files)[-3:]:
        print(f'    {f.name} ({f.stat().st_size / 1024:.1f} KB)')
    
    # 2. Datasets
    print('\n[2] DATASETS...')
    parquet_files = list(save_dir.rglob('dataset_*.parquet'))
    print(f'  Parquets: {len(parquet_files)}')
    for f in sorted(parquet_files)[-3:]:
        print(f'    {f.name} ({f.stat().st_size / 1024 / 1024:.2f} MB)')
    
    # 3. Labels
    print('\n[3] LABELS...')
    labels_files = list(save_dir.rglob('labels_*.jsonl'))
    print(f'  Arquivos de labels: {len(labels_files)}')
    for f in sorted(labels_files)[-3:]:
        size = f.stat().st_size
        print(f'    {f.name} ({size / 1024:.1f} KB)')
    
    # 4. Features
    print('\n[4] FEATURES...')
    feat_files = list(save_dir.rglob('dataset_100ms_*.jsonl'))
    print(f'  Arquivos de features: {len(feat_files)}')
    for f in sorted(feat_files)[-3:]:
        size = f.stat().st_size
        print(f'    {f.name} ({size} bytes)')
    
    # 5. Análise do modelo
    print('\n[5] ANÁLISE DO MODELO...')
    if modelo_files:
        import pickle
        with open(modelo_files[-1], 'rb') as f:
            blob = pickle.load(f)
        
        print(f'  Features: {len(blob.get("features", []))}')
        print(f'  Métricas: {blob.get("metricas", {})}')
        
        model = blob.get('modelo')
        if model and hasattr(model, 'feature_importances_'):
            features = blob.get('features', [])
            imp = pd.Series(model.feature_importances_, index=features)
            print(f'  Top 5 features:')
            for feat, score in imp.sort_values(ascending=False).head(5).items():
                print(f'    {feat:40s} {score:.4f}')
    
    # 6. Análise do dataset
    print('\n[6] ANÁLISE DO DATASET...')
    if parquet_files:
        df = pd.read_parquet(parquet_files[-1])
        print(f'  Shape: {df.shape}')
        
        if 'label' in df.columns:
            dist = df['label'].value_counts()
            print(f'  Distribuição de labels:')
            for label, count in dist.items():
                pct = 100.0 * count / len(df)
                label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT', -99: 'AMBIGUOUS'}.get(label, str(label))
                print(f'    {label_name} ({label}): {count:,} ({pct:.2f}%)')
        
        if 'ativo' in df.columns:
            print(f'  Ativos: {df["ativo"].unique().tolist()}')
    
    print('\n' + '='*70)
    print('AUDITORIA CONCLUÍDA')
    print('='*70)

if __name__ == '__main__':
    main()
