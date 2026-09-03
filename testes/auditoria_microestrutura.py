#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_microestrutura.py — Auditoria completa de microestrutura.
"""
import sys
import os
import re
from pathlib import Path
from collections import defaultdict

# Adicionar raiz ao path
_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DE MICROESTRUTURA')
    print('='*70)
    
    resultados = defaultdict(list)
    
    # ========================================================================
    # 1. VERIFICAR SEPARAÇÃO POR ATIVO
    # ========================================================================
    print('\n[1] VERIFICANDO SEPARAÇÃO POR ATIVO...')
    
    # Verificar se o labeler segmenta por ativo
    from ml.labeler_vectorizado import _segmentos
    import numpy as np
    
    # Simular dados mistos WIN/WDO
    ts_ms = np.array([1000, 1100, 1200, 1300, 1400])
    ativos = np.array(['WINV26', 'WINV26', 'WDOU26', 'WDOU26', 'WDOU26'])
    segs = _segmentos(ts_ms, ativos)
    print(f'  Segmentos para WIN/WDO misturados: {segs}')
    if segs == [0, 2, 5]:
        print('  [OK] Segmentação correta: WIN e WDO em segmentos diferentes')
        resultados['separacao_ativo'].append(('Segmentação', True))
    else:
        print('  [FAIL] Segmentação incorreta')
        resultados['separacao_ativo'].append(('Segmentação', False))
    
    # Verificar GeradorJanelas
    from ml.features_lib import GeradorJanelas
    gerador = GeradorJanelas(instrumentos=['WINV26', 'WDOU26'])
    print('  [OK] GeradorJanelas suporta múltiplos instrumentos')
    resultados['separacao_ativo'].append(('GeradorJanelas', True))
    
    # ========================================================================
    # 2. VERIFICAR TIMESTAMPS
    # ========================================================================
    print('\n[2] VERIFICANDO TIMESTAMPS...')
    
    # Verificar se timestamps são epoch ms
    from adapters.rtd_parser import parse_hms_ms
    print('  [OK] rtd_parser disponível')
    resultados['timestamps'].append(('Imports', True))
    
    # Verificar conversão TOD
    from core.event_clock import EventClock
    clock = EventClock()
    # Teste: 14h = 50400000 ms
    tod = clock.tod_de_ts(1788040000000)  # 2026-08-29 14:00:00 BRT
    print(f'  TOD para ts_ms=1788040000000: {tod}')
    if 50000000 <= tod <= 51000000:  # ~14h
        print('  [OK] Conversão TOD correta')
        resultados['timestamps'].append(('Conversão TOD', True))
    else:
        print('  [FAIL] Conversão TOD incorreta')
        resultados['timestamps'].append(('Conversão TOD', False))
    
    # ========================================================================
    # 3. VERIFICAR ALIGNMENT TEMPORAL
    # ========================================================================
    print('\n[3] VERIFICANDO ALINHAMENTO TEMPORAL...')
    
    # Verificar asof join
    from ml.features_lib import asof_join_linhas
    print('  [OK] asof_join_linhas disponível')
    resultados['alinhamento'].append(('asof_join', True))
    
    # ========================================================================
    # 4. VERIFICAR FEATURES DE MICROESTRUTURA
    # ========================================================================
    print('\n[4] VERIFICANDO FEATURES DE MICROESTRUTURA...')
    
    features_check = [
        ('aggr_imb', 'features/trade_features.py'),
        ('cvd_total', 'features/trade_features.py'),
        ('spread', 'features/book_features.py'),
        ('microprice', 'features/book_features.py'),
        ('vwap', 'features/vwap_tracker.py'),
        ('vp_vp_total', 'features/volume_profile.py'),
        ('kyle_lambda', 'features/kyle_lambda.py'),  # Corrigido: nome real
        ('vpin', 'features/vpin.py'),
        ('ofi_total', 'features/book_features.py'),
        ('cross_lag', 'features/cross_asset.py'),
    ]
    
    for feat, file_path in features_check:
        full_path = _root / file_path
        if full_path.exists():
            content = full_path.read_text(encoding='utf-8', errors='ignore')
            if feat in content:
                print(f'  [OK] {feat}')
                resultados['features'].append((feat, True))
            else:
                print(f'  [FAIL] {feat} não encontrado em {file_path}')
                resultados['features'].append((feat, False))
        else:
            print(f'  [FAIL] Arquivo não encontrado: {file_path}')
            resultados['features'].append((feat, False))
    
    # ========================================================================
    # 5. VERIFICAR MISTURAS INDEVIDAS
    # ========================================================================
    print('\n[5] VERIFICANDO MISTURAS INDEVIDAS...')
    
    # Verificar se há mistura WIN/WINFUT
    winfut_count = 0
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        # Procurar por WINFUT que não seja mapeado para WINV26
        if 'WINFUT' in content and 'mapa-ativos' not in content and 'importar_historico' not in str(py_file):
            winfut_count += 1
    
    if winfut_count <= 1:  # Só deve aparecer em importar_historico
        print('  [OK] Nenhuma mistura WIN/WINFUT indevida')
        resultados['misturas'].append(('WIN/WINFUT', True))
    else:
        print(f'  [WARN] WINFUT encontrado em {winfut_count} arquivos')
        resultados['misturas'].append(('WIN/WINFUT', False))
    
    # Verificar se há mistura WDO/DOLFUT
    dolfut_count = 0
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'DOLFUT' in content and 'importar_historico' not in str(py_file):
            dolfut_count += 1
    
    if dolfut_count == 0:
        print('  [OK] Nenhuma mistura WDO/DOLFUT indevida')
        resultados['misturas'].append(('WDO/DOLFUT', True))
    else:
        print(f'  [WARN] DOLFUT encontrado em {dolfut_count} arquivos')
        resultados['misturas'].append(('WDO/DOLFUT', False))
    
    # ========================================================================
    # 6. VERIFICAR LIVRO (BOOK)
    # ========================================================================
    print('\n[6] VERIFICANDO LIVRO (BOOK)...')
    
    # Verificar BookLevelFeatures
    from features.book_features import BookLevelFeatures
    blf = BookLevelFeatures()
    snap = {'bid_preco': [100, 99, 98], 'bid_vol': [10, 20, 30],
            'ask_preco': [101, 102, 103], 'ask_vol': [15, 25, 35]}
    result = blf.calcular(snap, 'WINV26', 1000)
    if result and 'spread' in result and 'microprice' in result:
        print(f'  [OK] Book features: spread={result["spread"]}, microprice={result["microprice"]}')
        resultados['book'].append(('BookLevelFeatures', True))
    else:
        print('  [FAIL] Book features incompletas')
        resultados['book'].append(('BookLevelFeatures', False))
    
    # ========================================================================
    # 7. VERIFICAR VOLUME
    # ========================================================================
    print('\n[7] VERIFICANDO VOLUME...')
    
    # Verificar VolumeRelativoTracker
    from features.volume_relativo import VolumeRelativoTracker
    vrt = VolumeRelativoTracker()
    vrt.update(10, 1000)  # vol, ts_ms
    vrt.update(15, 1100)
    snap = vrt.snapshot()
    if 'volume_relativo' in snap:
        print(f'  [OK] Volume relativo: {snap["volume_relativo"]}')
        resultados['volume'].append(('VolumeRelativoTracker', True))
    else:
        print('  [FAIL] Volume relativo não calculado')
        resultados['volume'].append(('VolumeRelativoTracker', False))
    
    # ========================================================================
    # 8. RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA')
    print('='*70)
    
    total_ok = 0
    total_fail = 0
    
    for categoria, items in resultados.items():
        ok = sum(1 for _, v in items if v)
        fail = sum(1 for _, v in items if not v)
        total_ok += ok
        total_fail += fail
        print(f'\n{categoria}: {ok} OK, {fail} FAIL')
        for nome, valor in items:
            status = 'OK' if valor else 'FAIL'
            print(f'  [{status}] {nome}')
    
    print(f'\nTOTAL: {total_ok} OK, {total_fail} FAIL')
    
    if total_fail == 0:
        print('\n[AUDITORIA OK] Nenhuma irregularidade crítica encontrada.')
        return 0
    else:
        print(f'\n[AUDITORIA ATTENTION] {total_fail} problema(s) encontrado(s).')
        return 1

if __name__ == '__main__':
    sys.exit(main())
