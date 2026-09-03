#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_labels.py — Debug para entender por que 99.9% dos labels são TIMEOUT.
"""
import sys
import json
import numpy as np
from pathlib import Path

sys.path.insert(0, '.')

def main():
    print('='*70)
    print('DEBUG: POR QUE 99.9% DOS LABELS SÃO TIMEOUT?')
    print('='*70)
    
    # Carregar features
    feat_file = Path(r'D:\MarketData\mimo\dataset_100ms_WINV26_1-29.jsonl')
    print(f'\n[1] Carregando features: {feat_file}')
    
    features = []
    with open(feat_file, 'r') as f:
        for i, line in enumerate(f):
            if i >= 10000:
                break
            features.append(json.loads(line))
    
    print(f'  Features carregadas: {len(features):,}')
    
    # Verificar preco_ultimo
    precos = []
    ts_ms = []
    ativos = []
    
    for f in features:
        preco = f.get('preco_ultimo', 0)
        if preco > 0:
            precos.append(preco)
            ts_ms.append(f.get('ts_ms', 0))
            ativos.append(f.get('ativo', 'UNKNOWN'))
    
    print(f'\n[2] Análise de preços:')
    print(f'  Preços válidos: {len(precos):,}')
    if precos:
        print(f'  Min: {min(precos):,.0f}')
        print(f'  Max: {max(precos):,.0f}')
        print(f'  Mean: {np.mean(precos):,.0f}')
        
        # Verificar variação de preço
        precos_arr = np.array(precos)
        deltas = np.abs(np.diff(precos_arr))
        print(f'  Delta medio: {np.mean(deltas):.2f}')
        print(f'  Delta max: {np.max(deltas):.2f}')
    
    # Verificar configuração
    print(f'\n[3] Configuração de TP/SL:')
    from config import CONFIG
    tp_pts = CONFIG['trading'].get('tp_pts', 100)
    sl_pts = CONFIG['trading'].get('sl_pts', 50)
    max_holding_s = CONFIG['trading'].get('max_holding_s', 30)
    
    print(f'  TP: {tp_pts} pts')
    print(f'  SL: {sl_pts} pts')
    print(f'  Holding: {max_holding_s}s')
    
    # Simular labeler
    print(f'\n[4] Simulando labeler...')
    
    from ml.labeler_vectorizado import label_vectorizado
    
    if precos:
        precos_arr = np.array(precos[:1000], dtype=np.float64)  # Amostra
        ts_arr = np.array(ts_ms[:1000], dtype=np.int64)
        ativos_arr = np.array(ativos[:1000])
        
        resultado = label_vectorizado(
            precos_arr, ts_arr, ativos_arr,
            tp_pts=tp_pts, sl_pts=sl_pts,
            max_holding_s=max_holding_s, purge_s=10
        )
        
        labels = resultado['label']
        n_tp = np.sum(labels == 1)
        n_sl = np.sum(labels == -1)
        n_timeout = np.sum(labels == 0)
        n_amb = np.sum(labels == -99)
        total = len(labels)
        
        print(f'\n  Resultado da simulação:')
        print(f'  Total: {total:,}')
        print(f'    TP (+1): {n_tp:,} ({100*n_tp/total:.2f}%)')
        print(f'    SL (-1): {n_sl:,} ({100*n_sl/total:.2f}%)')
        print(f'    TIMEOUT (0): {n_timeout:,} ({100*n_timeout/total:.2f}%)')
        print(f'    AMBIGUOUS (-99): {n_amb:,} ({100*n_amb/total:.2f}%)')
        
        # Analisar durações
        duracoes = resultado['duracao_ms']
        print(f'\n  Durações:')
        print(f'    Media: {np.mean(duracoes):.0f} ms')
        print(f'    Max: {np.max(duracoes):.0f} ms')
        print(f'    Median: {np.median(duracoes):.0f} ms')
        
        # Verificar quantos têm duracao > 0
        com_duracao = np.sum(duracoes > 0)
        print(f'    Com duração > 0: {com_duracao:,} ({100*com_duracao/total:.2f}%)')
    else:
        print('  [FAIL] Nenhum preço válido encontrado')
    
    # Verificar se o problema é no arquivo de labels
    print(f'\n[5] Verificando arquivo de labels existente...')
    labels_file = Path(r'D:\MarketData\mimo\labels_WINV26_1-29.jsonl')
    if labels_file.exists():
        with open(labels_file, 'r') as f:
            first_line = f.readline()
        data = json.loads(first_line)
        print(f'  Primeiro label:')
        print(f'    ts_ms: {data.get("ts_ms", "N/A")}')
        print(f'    label: {data.get("label", "N/A")}')
        print(f'    outcome: {data.get("outcome", "N/A")}')
        print(f'    duracao_ms: {data.get("duracao_ms", "N/A")}')
        print(f'    preco_entrada: {data.get("preco_entrada", "N/A")}')
        print(f'    preco_saida: {data.get("preco_saida", "N/A")}')
        
        # Verificar se ts_ms é 0
        if data.get('ts_ms', 0) == 0:
            print(f'\n  [CRÍTICO] ts_ms=0 indica que o label não foi gerado corretamente')
            print(f'  [CRÍTICO] Isso explica por que 99.9% são TIMEOUT')
    
    print('\n' + '='*70)

if __name__ == '__main__':
    main()
