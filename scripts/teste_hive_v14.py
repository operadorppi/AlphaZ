#!/usr/bin/env python3
"""Teste do schema v14.1 completo."""
import sys, time, os
sys.path.insert(0, '.')
import types
sys.modules['comtypes'] = types.ModuleType('comtypes')
sys.modules['comtypes.client'] = types.ModuleType('comtypes.client')

from adapters.file_storage import CapturaEventosMS, _asset_partition
import tempfile

print('=== Schema v14.1 Completo ===')

with tempfile.TemporaryDirectory() as td:
    fs = CapturaEventosMS(td, session_ts='test_v14')
    now_ms = int(time.time() * 1000)
    now_ns = time.time_ns()
    seq = 1

    # TT: 4 ativos + 2 RLP
    tt = [
        ('WINV26', now_ms, 183000.0, 10, 'Comprador', 'C1', 'V1', 1, 'T&T1', False, now_ns, seq),
        ('INDV26', now_ms+1, 183005.0, 5, 'Vendedor', 'C2', 'V2', 0, 'T&T0', False, now_ns+1000000, seq+1),
        ('WDOV26', now_ms+2, 5175.0, 3, 'Comprador', 'C3', 'V3', 2, 'T&T2', False, now_ns+2000000, seq+2),
        ('DOLV26', now_ms+3, 5176.0, 2, 'Vendedor', 'C4', 'V4', 3, 'T&T3', False, now_ns+3000000, seq+3),
        ('WINV26', now_ms+4, 183010.0, 8, 'Comprador', 'C1', 'V5', 4, 'T&T4', True, now_ns+4000000, seq+4),
        ('WDOV26', now_ms+5, 5177.0, 4, 'Vendedor', 'C5', 'V6', 5, 'T&T5', True, now_ns+5000000, seq+5),
    ]
    fs.registrar_negocios(tt)

    # Book: 4 ativos com 3 niveis cada
    for a, j_idx in [('WINV26', 1), ('INDV26', 0), ('WDOV26', 2), ('DOLV26', 3)]:
        base_p = 183000.0 if 'WIN' in a or 'IND' in a else 5175.0
        fs.registrar_book(a, now_ms, {}, 300, 400,
                         levels={
                             'bid_preco': [base_p-5, base_p-10, base_p-15],
                             'bid_vol': [10, 20, 30],
                             'ask_preco': [base_p+5, base_p+10, base_p+15],
                             'ask_vol': [15, 25, 35],
                             'ofi': 0.15,
                         },
                         janela_id=j_idx, window_name='BOOK%d' % j_idx,
                         received_at_ns=now_ns)

    fs.flush()
    fs.fechar()

    # Listar estrutura
    print('\nEstrutura:')
    for root, dirs, files in os.walk(os.path.join(td, 'RAW')):
        level = root.replace(td, '').count(os.sep)
        indent = '  ' * level
        print('%s%s/' % (indent, os.path.basename(root)))
        for f in sorted(files):
            fpath = os.path.join(root, f)
            sz = os.path.getsize(fpath)
            print('%s  %s (%d bytes)' % (indent, f, sz))

    # Validar schema
    import pyarrow.parquet as pq
    print('\nSchema TT:')
    for root, dirs, files in os.walk(os.path.join(td, 'RAW', 'data_type=TT')):
        for f in sorted(files):
            if f.endswith('.parquet'):
                t = pq.read_table(os.path.join(root, f))
                asset = os.path.basename(root).replace('asset=', '')
                print('  %s: %d rows' % (asset, len(t)))
                print('    cols:', t.column_names)
                print('    ts_ns[0]:', t.column('ts_ns')[0].as_py())
                print('    received_at_ns[0]:', t.column('received_at_ns')[0].as_py())
                print('    is_rlp[0]:', t.column('is_rlp')[0].as_py())

    print('\nSchema BOOK:')
    for root, dirs, files in os.walk(os.path.join(td, 'RAW', 'data_type=BOOK')):
        for f in sorted(files):
            if f.endswith('.parquet'):
                t = pq.read_table(os.path.join(root, f))
                asset = os.path.basename(root).replace('asset=', '')
                print('  %s: %d rows (3 niveis)' % (asset, len(t)))
                print('    cols:', t.column_names)
                print('    nivel:', t.column('nivel').to_pylist())
                print('    bid:', t.column('bid').to_pylist())
                print('    ask:', t.column('ask').to_pylist())
                print('    bid_volume:', t.column('bid_volume').to_pylist())
                print('    ask_volume:', t.column('ask_volume').to_pylist())
                if 'ofi' in t.column_names:
                    print('    ofi:', t.column('ofi').to_pylist())
