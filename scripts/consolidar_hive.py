#!/usr/bin/env python3
"""
consolidar_hive.py — Consolida arquivos Parquet Hive fragmentados.

Reduce thousands of small parquet files per asset into 1 larger file.
Reads all fragments, concatenates, writes single file per asset.

Usage:
  python scripts/consolidar_hive.py --dia 20260902
  python scripts/consolidar_hive.py --dia 20260902 --data-type TT
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def consolidar(dia_str, data_type=None, base_dir=None):
    base = Path(base_dir or r'D:\MarketData\mimo\RAW')
    data_types = [data_type] if data_type else ['TT', 'BOOK']

    for dt in data_types:
        dt_dir = base / f'data_type={dt}' / f'date={dia_str}'
        if not dt_dir.exists():
            print(f'  {dt}: no directory, skipping')
            continue

        for asset_dir in sorted(dt_dir.iterdir()):
            if not asset_dir.is_dir() or not asset_dir.name.startswith('asset='):
                continue

            asset = asset_dir.name
            files = sorted(asset_dir.glob('part-*.parquet'))
            if len(files) <= 1:
                print(f'  {dt}/{asset}: {len(files)} file(s) — nothing to consolidate')
                continue

            t0 = time.time()
            print(f'  {dt}/{asset}: {len(files)} files -> consolidating...', end=' ', flush=True)

            # Read all files
            tables = []
            total_rows = 0
            for f in files:
                t = pq.read_table(f)
                tables.append(t)
                total_rows += len(t)

            # Concatenate and write
            combined = pa.concat_tables(tables)
            out_path = asset_dir / 'part-0000.parquet'
            pq.write_table(combined, out_path, compression='snappy')

            # Remove old fragments (keep the new consolidated file)
            for f in files:
                if f != out_path:
                    f.unlink()

            elapsed = time.time() - t0
            print(f'{total_rows:,} rows -> 1 file in {elapsed:.1f}s')

    print('\nDone.')


def main():
    ap = argparse.ArgumentParser(description='Consolidar Parquet Hive fragmentado')
    ap.add_argument('--dia', required=True, help='YYYYMMDD')
    ap.add_argument('--data-type', default=None, help='TT or BOOK (default: both)')
    ap.add_argument('--base-dir', default=None)
    args = ap.parse_args()

    consolidar(args.dia, args.data_type, args.base_dir)


if __name__ == '__main__':
    main()
