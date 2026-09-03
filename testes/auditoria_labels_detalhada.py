#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_labels_detalhada.py — Auditoria detalhada dos labels.
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
    print('AUDITORIA DETALHADA DE LABELS')
    print('='*70)
    
    save_dir = Path(r'D:\MarketData\mimo')
    
    # ========================================================================
    # 1. VERIFICAR ARQUIVO DE LABELS
    # ========================================================================
    print('\n[1] VERIFICANDO ARQUIVO DE LABELS...')
    
    labels_file = save_dir / 'labels_WINV26_1-29.jsonl'
    if not labels_file.exists():
        print(f'  [FAIL] Arquivo não encontrado: {labels_file}')
        return 1
    
    print(f'  Arquivo: {labels_file}')
    print(f'  Tamanho: {labels_file.stat().st_size / 1024 / 1024:.2f} MB')
    
    # Contar linhas
    with open(labels_file, 'r') as f:
        linhas = f.readlines()
    print(f'  Linhas: {len(linhas):,}')
    
    # ========================================================================
    # 2. ANALISAR PRIMEIRAS LINHAS
    # ========================================================================
    print('\n[2] ANALISANDO PRIMEIRAS LINHAS...')
    
    for i in range(min(5, len(linhas))):
        data = json.loads(linhas[i])
        print(f'\n  Linha {i+1}:')
        for k, v in sorted(data.items()):
            if k not in ['book', 'vp', 'kyle']:  # Skip dict features
                print(f'    {k}: {v}')
    
    # ========================================================================
    # 3. ANALISAR DISTRIBUIÇÃO COMPLETA
    # ========================================================================
    print('\n[3] DISTRIBUIÇÃO COMPLETA DE LABELS...')
    
    label_counts = defaultdict(int)
    dia_counts = defaultdict(lambda: defaultdict(int))
    ativo_counts = defaultdict(lambda: defaultdict(int))
    
    for i, line in enumerate(linhas):
        if i % 100000 == 0:
            print(f'  Processando... {i:,} linhas')
        data = json.loads(line)
        label = data.get('label', 0)
        label_counts[label] += 1
        
        ts_ms = data.get('ts_ms', 0)
        if ts_ms > 0:
            dia = datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d')
            dia_counts[dia][label] += 1
        
        ativo = data.get('ativo', 'UNKNOWN')
        ativo_counts[ativo][label] += 1
    
    print(f'\n  Total: {sum(label_counts.values()):,} labels')
    print(f'\n  Distribuição:')
    for label, count in sorted(label_counts.items()):
        pct = 100.0 * count / sum(label_counts.values())
        label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT', -99: 'AMBIGUOUS'}.get(label, str(label))
        print(f'    {label_name} ({label}): {count:>10} ({pct:>6.2f}%)')
    
    # ========================================================================
    # 4. DISTRIBUIÇÃO POR DIA
    # ========================================================================
    print('\n[4] DISTRIBUIÇÃO POR DIA...')
    
    print(f'  {"Dia":<12} {"TP":>8} {"SL":>8} {"TO":>8} {"AMB":>8} {"Total":>10}')
    print('  ' + '-' * 50)
    for dia in sorted(dia_counts.keys()):
        counts = dia_counts[dia]
        total = sum(counts.values())
        print(f'  {dia:<12} {counts.get(1,0):>8} {counts.get(-1,0):>8} '
              f'{counts.get(0,0):>8} {counts.get(-99,0):>8} {total:>10}')
    
    # ========================================================================
    # 5. DISTRIBUIÇÃO POR ATIVO
    # ========================================================================
    print('\n[5] DISTRIBUIÇÃO POR ATIVO...')
    
    for ativo in sorted(ativo_counts.keys()):
        counts = ativo_counts[ativo]
        total = sum(counts.values())
        print(f'\n  {ativo}:')
        print(f'    Total: {total:,}')
        for label, count in sorted(counts.items()):
            pct = 100.0 * count / total
            label_name = {1: 'TP', -1: 'SL', 0: 'TIMEOUT', -99: 'AMBIGUOUS'}.get(label, str(label))
            print(f'      {label_name} ({label}): {count:,} ({pct:.2f}%)')
    
    # ========================================================================
    # 6. VERIFICAR PARAMETROS DO LABELER
    # ========================================================================
    print('\n[6] VERIFICANDO PARAMETROS DO LABELER...')
    
    from config import CONFIG
    tp_pts = CONFIG['trading'].get('tp_pts', 100)
    sl_pts = CONFIG['trading'].get('sl_pts', 50)
    max_holding_s = CONFIG['trading'].get('max_holding_s', 30)
    
    print(f'  TP: {tp_pts} pts')
    print(f'  SL: {sl_pts} pts')
    print(f'  Max Holding: {max_holding_s}s')
    
    # Verificar faixas de preço
    if 'faixas_preco' in CONFIG:
        print(f'  Faixas de preço: {CONFIG["faixas_preco"]}')
    else:
        print('  [WARN] Faixas de preço não configuradas')
    
    # ========================================================================
    # 7. VERIFICAR FEATURES ANTES DO LABEL
    # ========================================================================
    print('\n[7] VERIFICANDO FEATURES...')
    
    # Verificar se há preco_ultimo nos labels
    if linhas:
        first_line = json.loads(linhas[0])
        if 'preco_ultimo' in first_line:
            print(f'  preco_ultimo: {first_line["preco_ultimo"]}')
        else:
            print('  [WARN] preco_ultimo não encontrado nos labels')
        
        if 'ativo' in first_line:
            print(f'  ativo: {first_line["ativo"]}')
        else:
            print('  [WARN] ativo não encontrado nos labels')
    
    # ========================================================================
    # 8. VERIFICAR POSSÍVEIS PROBLEMAS
    # ========================================================================
    print('\n[8] VERIFICANDO PROBLEMAS EM POTENCIAL...')
    
    problemas = []
    
    # Verificar se todos os labels são 0
    if label_counts.get(0, 0) > sum(label_counts.values()) * 0.9:
        problemas.append('MAIORIA DOS LABELS SÃO TIMEOUT (0)')
    
    # Verificar se não há labels TP ou SL
    if label_counts.get(1, 0) == 0 and label_counts.get(-1, 0) == 0:
        problemas.append('NENHUM TP ou SL encontrado')
    
    # Verificar se há muitos AMBIGUOUS
    if label_counts.get(-99, 0) > sum(label_counts.values()) * 0.1:
        problemas.append('EXCESSO DE AMBIGUOUS (-99)')
    
    if problemas:
        print('  [FAIL] Problemas encontrados:')
        for p in problemas:
            print(f'    - {p}')
    else:
        print('  [OK] Nenhum problema crítico encontrado')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA')
    print('='*70)
    
    total = sum(label_counts.values())
    tp = label_counts.get(1, 0)
    sl = label_counts.get(-1, 0)
    to = label_counts.get(0, 0)
    amb = label_counts.get(-99, 0)
    
    print(f'\nTotal de labels: {total:,}')
    print(f'  TP (+1): {tp:,} ({100*tp/max(total,1):.2f}%)')
    print(f'  SL (-1): {sl:,} ({100*sl/max(total,1):.2f}%)')
    print(f'  TIMEOUT (0): {to:,} ({100*to/max(total,1):.2f}%)')
    print(f'  AMBIGUOUS (-99): {amb:,} ({100*amb/max(total,1):.2f}%)')
    
    if tp + sl > 0:
        ratio = tp / (tp + sl)
        print(f'\nRatio TP/(TP+SL): {ratio:.2f}')
        if 0.4 < ratio < 0.6:
            print('  [OK] Balanceamento aceitável')
        else:
            print('  [WARN] Balanceamento desfavorável')
    
    if to > total * 0.8:
        print('\n[CRÍTICO] Alta taxa de TIMEOUT — verificar parâmetros TP/SL/Holding')
        return 1
    else:
        print('\n[LABELS OK] Distribuição de labels aceitável')
        return 0

if __name__ == '__main__':
    sys.exit(main())
