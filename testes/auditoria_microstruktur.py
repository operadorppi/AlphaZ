#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_microstruktur.py — Verifikasi fitur mikrostruktur & masalah penggabungan aset.
"""
import sys
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDIT STRUKTUR PASAR')
    print('='*70)
    
    results = []
    
    # 1. VERIFIKASI PEMISAHAN ASET
    print('\n[1] MEMVERIFIKASI PEMISAHAN ASET...')
    
    try:
        from ml.labeler_vectorizado import _segmentos
        import numpy as np
        
        ts_ms = np.array([1000, 1100, 1200, 1300, 1400])
        aktivas = np.array(['WINV26', 'WINV26', 'WDOU26', 'WDOU26', 'WDOU26'])
        segs = _segmentos(ts_ms, aktivas)
        print(f'  Segmen untuk WIN/WDO bercampur: {segs}')
        if segs == [0, 2, 5]:
            print('  [OK] Pemisahan segmen benar: WIN dan WDO di segmen berbeda')
            results.append(('Segmentasi', True))
        else:
            print('  [FAIL] Pemisahan segmen salah')
            results.append(('Segmentasi', False))
    except Exception as e:
        print(f'  [FAIL] Segmentasi: {e}')
        results.append(('Segmentasi', False))
    
    try:
        from ml.features_lib import GeradorJanelas
        gen = GeradorJanelas(instrumentos=['WINV26', 'WDOU26'])
        print('  [OK] GeradorJanelas mendukung multi-aset')
        results.append(('GeradorJanelas', True))
    except Exception as e:
        print(f'  [FAIL] GeradorJanelas: {e}')
        results.append(('GeradorJanelas', False))
    
    # 2. VERIFIKASI TIMESTAMP
    print('\n[2] MEMVERIFIKASI TIMESTAMP...')
    
    try:
        from adapters.rtd_parser import parse_hms_ms
        print('  [OK] rtd_parser tersedia')
        results.append(('Impor', True))
    except Exception as e:
        print(f'  [FAIL] Impor: {e}')
        results.append(('Impor', False))
    
    # 3. VERIFIKASI ALIGMENT TEMPORAL
    print('\n[3] MEMVERIFIKASI ALIGMENT TEMPORAL...')
    
    try:
        from ml.features_lib import asof_join_linhas
        print('  [OK] asof_join_linhas tersedia')
        results.append(('asof_join', True))
    except Exception as e:
        print(f'  [FAIL] asof_join: {e}')
        results.append(('asof_join', False))
    
    # 4. VERIFIKASI FITUR MIKROSTRUKTUR
    print('\n[4] MEMVERIFIKASI FITUR MIKROSTRUKTUR...')
    
    features_check = [
        ('aggr_imb', 'features/trade_features.py'),
        ('cvd_total', 'features/trade_features.py'),
        ('spread', 'features/book_features.py'),
        ('microprice', 'features/book_features.py'),
        ('vwap', 'features/vwap_tracker.py'),
        ('vp_total', 'features/volume_profile.py'),  # vp_total = vp_vp_total
        ('kyle_lambda', 'features/kyle_lambda.py'),
        ('vpin', 'features/vpin.py'),
        ('ofi_total', 'features/book_features.py'),
        ('lag_ms', 'features/cross_asset.py'),  # cross_lag = lag_ms
    ]
    
    for feat, file_path in features_check:
        full_path = _root / file_path
        if full_path.exists():
            content = full_path.read_text(encoding='utf-8', errors='ignore')
            if feat in content:
                print(f'  [OK] {feat}')
                results.append((feat, True))
            else:
                print(f'  [FAIL] {feat} tidak ditemukan di {file_path}')
                results.append((feat, False))
        else:
            print(f'  [FAIL] File tidak ditemukan: {file_path}')
            results.append((feat, False))
    
    # 5. VERIFIKASI PENGGABUNGAN YANG TIDAK SESUAI
    print('\n[5] MEMVERIFIKASI PENGGABUNGAN ASET...')
    
    winfut_count = 0
    dolfut_count = 0
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'WINFUT' in content and 'mapa-ativos' not in content and 'importar_historico' not in str(py_file):
            winfut_count += 1
        if 'DOLFUT' in content and 'importar_historico' not in str(py_file):
            dolfut_count += 1
    
    if winfut_count == 0:
        print('  [OK] Tidak ada penggabungan WIN/WINFUT yang tidak sesuai')
        results.append(('WIN/WINFUT', True))
    else:
        print(f'  [WARN] WINFUT ditemukan di {winfut_count} file')
        results.append(('WIN/WINFUT', False))
    
    if dolfut_count == 0:
        print('  [OK] Tidak ada penggabungan WDO/DOLFUT yang tidak sesuai')
        results.append(('WDO/DOLFUT', True))
    else:
        print(f'  [WARN] DOLFUT ditemukan di {dolfut_count} file')
        results.append(('WDO/DOLFUT', False))
    
    # 6. VERIFIKASI BUKU
    print('\n[6] MEMVERIFIKASI BUKU...')
    
    try:
        from features.book_features import BookLevelFeatures
        blf = BookLevelFeatures()
        snap = {'bid_preco': [100, 99, 98], 'bid_vol': [10, 20, 30],
                'ask_preco': [101, 102, 103], 'ask_vol': [15, 25, 35]}
        result = blf.calcular(snap, 'WINV26', 1000)
        if result and 'spread' in result and 'microprice' in result:
            print(f'  [OK] Fitur buku: spread={result["spread"]}, microprice={result["microprice"]}')
            results.append(('BookLevelFeatures', True))
        else:
            print('  [FAIL] Fitur buku tidak lengkap')
            results.append(('BookLevelFeatures', False))
    except Exception as e:
        print(f'  [FAIL] BookLevelFeatures: {e}')
        results.append(('BookLevelFeatures', False))
    
    # 7. VERIFIKASI VOLUME
    print('\n[7] MEMVERIFIKASI VOLUME...')
    
    try:
        from features.volume_relativo import VolumeRelativoTracker
        vrt = VolumeRelativoTracker()
        vrt.update(10, 1000)
        vrt.update(15, 1100)
        snap = vrt.snapshot()
        if 'volume_relativo' in snap:
            print(f'  [OK] Volume relatif: {snap["volume_relativo"]}')
            results.append(('VolumeRelativoTracker', True))
        else:
            print('  [FAIL] Volume relatif tidak dihitung')
            results.append(('VolumeRelativoTracker', False))
    except Exception as e:
        print(f'  [FAIL] VolumeRelativoTracker: {e}')
        results.append(('VolumeRelativoTracker', False))
    
    # RINGKASAN
    print('\n' + '='*70)
    print('RINGKASAN AUDIT')
    print('='*70)
    
    ok_count = sum(1 for _, v in results if v)
    fail_count = sum(1 for _, v in results if not v)
    
    print(f'\nTotal: {ok_count} OK, {fail_count} FAIL')
    
    if fail_count == 0:
        print('\n[AUDIT OK] Tidak ada masalah kritis yang ditemukan.')
        return 0
    else:
        print(f'\n[AUDIT PERHATIAN] {fail_count} masalah ditemukan.')
        return 1

if __name__ == '__main__':
    sys.exit(main())
