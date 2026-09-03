#!/usr/bin/env python3
"""Teste rápido da estrutura Hive Parquet."""
import sys, time, os
sys.path.insert(0, '.')
import types
sys.modules['comtypes'] = types.ModuleType('comtypes')
sys.modules['comtypes.client'] = types.ModuleType('comtypes.client')

from adapters.file_storage import CapturaEventosMS, _asset_partition
import tempfile

print('=== _asset_partition ===')
for s, r in [('WINV26', False), ('INDV26', False), ('WDOV26', False), ('DOLV26', False),
             ('WINV26', True), ('WDOV26', True)]:
    print(f'  {s} rlp={r} -> {_asset_partition(s, r)}')

print('\n=== Gravacao Hive ===')
with tempfile.TemporaryDirectory() as td:
    fs = CapturaEventosMS(td, session_ts='test_hive')
    now = int(time.time() * 1000)

    tt = [
        ('WINV26', now, 183000.0, 10, 'Comprador', 'C1', 'V1', 1, 'T&T1', False),
        ('INDV26', now+1, 183005.0, 5, 'Vendedor', 'C2', 'V2', 0, 'T&T0', False),
        ('WDOV26', now+2, 5175.0, 3, 'Comprador', 'C3', 'V3', 2, 'T&T2', False),
        ('DOLV26', now+3, 5176.0, 2, 'Vendedor', 'C4', 'V4', 3, 'T&T3', False),
        ('WINV26', now+4, 183010.0, 8, 'Comprador', 'C1', 'V5', 4, 'T&T4', True),
        ('WDOV26', now+5, 5177.0, 4, 'Vendedor', 'C5', 'V6', 5, 'T&T5', True),
    ]
    fs.registrar_negocios(tt)

    for a in ['WINV26', 'INDV26', 'WDOV26', 'DOLV26']:
        j_idx = {'WINV26': 1, 'INDV26': 0, 'WDOV26': 2, 'DOLV26': 3}[a]
        fs.registrar_book(a, now, {}, 100, 200,
                         levels={'bid_preco': [1], 'bid_vol': [10], 'ask_preco': [2], 'ask_vol': [10]},
                         janela_id=j_idx, window_name=f'BOOK{j_idx}')

    fs.flush()
    fs.fechar()

    print('\nEstrutura:')
    for root, dirs, files in os.walk(os.path.join(td, 'RAW')):
        level = root.replace(td, '').count(os.sep)
        indent = '  ' * level
        print(f'{indent}{os.path.basename(root)}/')
        for f in sorted(files):
            fpath = os.path.join(root, f)
            sz = os.path.getsize(fpath)
            print(f'{indent}  {f} ({sz} bytes)')

    print('\nValidacao:')
    import pyarrow.parquet as pq
    for root, dirs, files in os.walk(os.path.join(td, 'RAW')):
        for f in sorted(files):
            if f.endswith('.parquet'):
                fpath = os.path.join(root, f)
                t = pq.read_table(fpath)
                ativos = set(t.column('ativo').to_pylist())
                rel = os.path.relpath(fpath, td)
                cols = t.column_names
                print(f'  {rel}: {len(t)} rows, ativos={ativos}, cols={cols}')
