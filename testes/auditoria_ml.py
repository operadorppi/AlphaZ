#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_ml.py — Auditoria completa do pipeline de Machine Learning.
"""
import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA COMPLETA DA ML')
    print('='*70)
    
    save_dir = Path(r'D:\MarketData\mimo')
    findings = []
    
    # ========================================================================
    # 1. VERIFICAR MODELOS EXISTENTES
    # ========================================================================
    print('\n[1] VERIFICANDO MODELOS EXISTENTES...')
    
    modelo_files = list(save_dir.rglob('*.pkl'))
    print(f'  Modelos encontrados: {len(modelo_files)}')
    for f in sorted(modelo_files):
        print(f'    - {f.name} ({f.stat().st_size / 1024:.1f} KB)')
    
    # ========================================================================
    # 2. VERIFICAR DATASETS
    # ========================================================================
    print('\n[2] VERIFICANDO DATASETS...')
    
    parquet_files = list(save_dir.rglob('*.parquet'))
    print(f'  Parquets encontrados: {len(parquet_files)}')
    for f in sorted(parquet_files):
        print(f'    - {f.name} ({f.stat().st_size / 1024 / 1024:.2f} MB)')
    
    # ========================================================================
    # 3. ANALISAR FEATURES UTILIZADAS
    # ========================================================================
    print('\n[3] ANALISANDO FEATURES UTILIZADAS...')
    
    # Verificar feature manifest
    manifest_file = save_dir / 'feature_manifest.json'
    if manifest_file.exists():
        with open(manifest_file, 'r') as f:
            manifest = json.load(f)
        print(f'  Features no manifest: {len(manifest.get("features", []))}')
    else:
        print('  [WARN] feature_manifest.json não encontrado')
    
    # Verificar modelo carregado
    modelo_files = list(save_dir.rglob('modelo_lgbm_*.pkl'))
    if modelo_files:
        import pickle
        with open(modelo_files[-1], 'rb') as f:
            blob = pickle.load(f)
        features = blob.get('features', [])
        print(f'  Features no modelo: {len(features)}')
        print(f'  Top 10 features:')
        if 'importancias' in blob:
            imp = blob['importancias']
            for feat, score in sorted(imp.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f'    {feat:40s} {score:.4f}')
    
    # ========================================================================
    # 4. ANALISAR FEATURES DESCARTADAS
    # ========================================================================
    print('\n[4] ANALISANDO FEATURES DESCARTADAS...')
    
    from ml.retreinar_lgbm_limpo import LEAKAGE_FEATURES, PROIBIDAS
    print(f'  Leakage features: {LEAKAGE_FEATURES}')
    print(f'  Proibidas: {PROIBIDAS}')
    
    # ========================================================================
    # 5. CARREGAR DATASET PARA ANÁLISE
    # ========================================================================
    print('\n[5] CARREGANDO DATASET...')
    
    dataset_files = sorted(save_dir.rglob('dataset_final*.parquet'))
    if not dataset_files:
        print('  [FAIL] Nenhum dataset encontrado')
        return 1
    
    dataset_file = dataset_files[-1]
    print(f'  Carregando: {dataset_file.name}')
    
    try:
        df = pd.read_parquet(dataset_file)
        print(f'  Shape: {df.shape}')
        print(f'  Columns: {len(df.columns)}')
    except Exception as e:
        print(f'  [FAIL] Erro ao carregar: {e}')
        return 1
    
    # ========================================================================
    # 6. DISTRIBUIÇÃO DE LABELS
    # ========================================================================
    print('\n[6] DISTRIBUIÇÃO DE LABELS...')
    
    if 'label' in df.columns:
        label_dist = df['label'].value_counts()
        print(f'  Total: {len(df):,}')
        for label, count in label_dist.items():
            pct = 100.0 * count / len(df)
            label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT', -99: 'AMBIGUOUS'}.get(label, str(label))
            print(f'    {label_name} ({label}): {count:>10} ({pct:>6.2f}%)')
    
    # ========================================================================
    # 7. PERFORMANCE POR ATIVO
    # ========================================================================
    print('\n[7] PERFORMANCE POR ATIVO...')
    
    if 'ativo' in df.columns and 'label' in df.columns:
        for ativo in df['ativo'].unique():
            df_ativo = df[df['ativo'] == ativo]
            print(f'\n  {ativo}:')
            print(f'    Total: {len(df_ativo):,}')
            if 'label' in df_ativo.columns:
                dist = df_ativo['label'].value_counts()
                for label, count in dist.items():
                    pct = 100.0 * count / len(df_ativo)
                    label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT'}.get(label, str(label))
                    print(f'      {label_name}: {count:>8} ({pct:>5.2f}%)')
    
    # ========================================================================
    # 8. PERFORMANCE POR DIA
    # ========================================================================
    print('\n[8] PERFORMANCE POR DIA...')
    
    if 'ts_ms' in df.columns and 'label' in df.columns:
        df['data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.date
        for dia in sorted(df['data'].unique())[:10]:  # Primeiros 10 dias
            df_dia = df[df['data'] == dia]
            print(f'\n  {dia}:')
            print(f'    Total: {len(df_dia):,}')
            if 'label' in df_dia.columns:
                dist = df_dia['label'].value_counts()
                for label, count in dist.items():
                    pct = 100.0 * count / len(df_dia)
                    label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT'}.get(label, str(label))
                    print(f'      {label_name}: {count:>8} ({pct:>5.2f}%)')
    
    # ========================================================================
    # 9. ANÁLISE DE CORRELAÇÃO
    # ========================================================================
    print('\n[9] ANÁLISE DE CORRELAÇÃO...')
    
    # Selecionar apenas features numéricas
    num_cols = df.select_dtypes(include=[np.number]).columns
    if len(num_cols) > 1:
        corr_matrix = df[num_cols].corr().abs()
        # Encontrar pares altamente correlacionados
        high_corr = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                if corr_matrix.iloc[i, j] > 0.95:
                    high_corr.append((corr_matrix.columns[i], corr_matrix.columns[j], corr_matrix.iloc[i, j]))
        
        if high_corr:
            print(f'  [WARN] {len(high_corr)} pares com correlação > 0.95:')
            for a, b, c in high_corr[:10]:
                print(f'    {a} <-> {b}: {c:.4f}')
        else:
            print('  [OK] Nenhuma correlação alta encontrada')
    
    # ========================================================================
    # 10. IMPORTÂNCIA DAS FEATURES
    # ========================================================================
    print('\n[10] IMPORTÂNCIA DAS FEATURES...')
    
    if modelo_files:
        import pickle
        with open(modelo_files[-1], 'rb') as f:
            blob = pickle.load(f)
        
        model = blob.get('modelo')
        features = blob.get('features', [])
        
        if model and hasattr(model, 'feature_importances_'):
            imp = pd.Series(model.feature_importances_, index=features)
            imp_sorted = imp.sort_values(ascending=False)
            
            print(f'  Top 20 features:')
            for feat, score in imp_sorted.head(20).items():
                print(f'    {feat:40s} {score:.4f}')
            
            # Verificar se top features fazem sentido
            top_feats = imp_sorted.head(5).index.tolist()
            print(f'\n  Top 5: {top_feats}')
            
            # Verificar se há leakage nas top features
            leakage_in_top = [f for f in top_feats if f in LEAKAGE_FEATURES or any(p in f.lower() for p in PROIBIDAS)]
            if leakage_in_top:
                print(f'  [FAIL] Features de leakage nas top 5: {leakage_in_top}')
                findings.append('leakage_nas_features')
            else:
                print('  [OK] Nenhuma feature de leakage nas top 5')
    
    # ========================================================================
    # 11. VERIFICAR WALK-FORWARD
    # ========================================================================
    print('\n[11] VERIFICANDO WALK-FORWARD...')
    
    wf_file = save_dir / 'walk_forward_v950.json'
    if wf_file.exists():
        with open(wf_file, 'r') as f:
            wf_data = json.load(f)
        print(f'  Walk-forward results encontradas')
        print(f'  Métricas disponíveis: {list(wf_data.keys())[:10]}')
    else:
        print(f'  [WARN] walk_forward_v950.json não encontrado')
    
    # ========================================================================
    # 12. VERIFICAR VALIDAÇÃO RIGOROSA
    # ========================================================================
    print('\n[12] VERIFICANDO VALIDAÇÃO RIGOROSA...')
    
    valid_file = save_dir / 'validacao_resultado.json'
    if valid_file.exists():
        with open(valid_file, 'r') as f:
            valid_data = json.load(f)
        print(f'  Resultados da validação:')
        for k, v in valid_data.items():
            if isinstance(v, dict):
                print(f'    {k}:')
                for k2, v2 in v.items():
                    print(f'      {k2}: {v2}')
            else:
                print(f'    {k}: {v}')
    else:
        print(f'  [WARN] validacao_resultado.json não encontrado')
    
    # ========================================================================
    # 13. ANÁLISE DE OVERFITTING
    # ========================================================================
    print('\n[13] ANÁLISE DE OVERFITTING...')
    
    # Verificar se há métricas de train/test
    if modelo_files:
        import pickle
        with open(modelo_files[-1], 'rb') as f:
            blob = pickle.load(f)
        
        metricas = blob.get('metricas', {})
        if metricas:
            print(f'  Métricas do modelo:')
            for k, v in metricas.items():
                print(f'    {k}: {v}')
            
            # Verificar se há diferença grande entre métricas
            if 'auc' in metricas and 'accuracy' in metricas:
                auc = metricas['auc']
                acc = metricas['accuracy']
                if auc > 0.7 and acc < 0.6:
                    print('  [WARN] Possível overfitting (AUC alto, accuracy baixo)')
                    findings.append('overfitting_potencial')
    
    # ========================================================================
    # 14. VERIFICAR ABALATION
    # ========================================================================
    print('\n[14] VERIFICANDO ABALATION...')
    
    ablation_file = save_dir / 'ablation_resultado.json'
    if ablation_file.exists():
        with open(ablation_file, 'r') as f:
            ablation_data = json.load(f)
        print(f'  Resultados de ablation encontrados')
    else:
        print(f'  [WARN] ablation_resultado.json não encontrado')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA ML')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} problema(s) encontrado(s):')
        for f in findings:
            print(f'  - {f}')
        return 1
    else:
        print('\n[ML OK] Nenhuma irregularidade crítica encontrada.')
        print('\nRecomendações:')
        print('  1. Executar walk-forward para validar estabilidade temporal')
        print('  2. Executar ablation para verificar redundâncias')
        print('  3. Monitorar drift de features ao longo do tempo')
        return 0

if __name__ == '__main__':
    sys.exit(main())
