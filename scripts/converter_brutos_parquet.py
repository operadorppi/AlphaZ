#!/usr/bin/env python3
"""
converter_brutos_parquet.py — Valida dados RAW na estrutura Hive Parquet.

v14: Os dados JÁ SÃO Parquet em estrutura Hive. Este script apenas
valida e gera um relatório de integridade.

Estrutura:
  RAW/data_type=TT/date=YYYYMMDD/asset=WIN/part-0.parquet
  RAW/data_type=BOOK/date=YYYYMMDD/asset=WIN/part-0.parquet

Uso:
  python scripts/converter_brutos_parquet.py --dia 20260901
  python scripts/converter_brutos_parquet.py --save-dir D:\\MarketData\\Profit
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import date, timedelta

try:
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters.file_storage import find_hive_files


def main():
    ap = argparse.ArgumentParser(description='Valida dados RAW na estrutura Hive')
    ap.add_argument('--dia', default=None, help='YYYYMMDD (default: ontem)')
    ap.add_argument('--save-dir', default=None)
    args = ap.parse_args()

    save_dir = args.save_dir or os.environ.get('SINAL_RT_DIR') or r'D:\MarketData\Profit'
    dia = args.dia or (date.today() - timedelta(days=1)).strftime('%Y%m%d')

    if not HAS_PYARROW:
        print('[ERRO] PyArrow não instalado.')
        sys.exit(1)

    print(f'=== Validação RAW Hive ===')
    print(f'Dia: {dia}')
    print(f'Source: {save_dir}')
    print()

    total_registros = 0
    total_arquivos = 0

    for dt in ['TT', 'BOOK']:
        files = find_hive_files(save_dir, dia_str=dia, data_type=dt)
        if not files:
            print(f'[{dt}] Nenhum arquivo encontrado')
            continue

        print(f'\n[{dt}] {len(files)} arquivo(s)')
        for f in files:
            try:
                pf = pq.read_table(f)
                n = len(pf)
                # Extrair asset do caminho
                parts = f.parts
                asset_part = [p for p in parts if p.startswith('asset=')]
                asset = asset_part[0].replace('asset=', '') if asset_part else '?'

                # Extrair colunas
                cols = pf.column_names

                print(f'  {asset}: {n:>6} registros, {len(cols)} colunas → {f.name}')
                total_registros += n
                total_arquivos += 1
            except Exception as e:
                print(f'  ERRO {f.name}: {e}')

    print(f'\n=== Resumo ===')
    print(f'Total: {total_registros} registros em {total_arquivos} arquivos Parquet')


if __name__ == '__main__':
    main()
