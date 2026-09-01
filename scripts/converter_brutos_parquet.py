#!/usr/bin/env python3
"""
converter_brutos_parquet.py — Converte JSONL brutos em Parquet por ativo.

Lê raw_negocios_ms_*.jsonl, raw_book_ms_*.jsonl e raw_rlp_ms_*.jsonl
para um dado dia, funde por ativo e salva como Parquet.

Uso:
  python scripts/converter_brutos_parquet.py --dia 20260901
  python scripts/converter_brutos_parquet.py                  # ontem
  python scripts/converter_brutos_parquet.py --save-dir D:\\MarketData\\mimo
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def find_jsonl_files(save_dir: str, dia: str, prefix: str) -> list:
    """Encontra todos os JSONL com o prefixo e dia especificados."""
    files = []
    for f in Path(save_dir).glob(f'{prefix}*{dia}*.jsonl'):
        if f.stat().st_size > 0:
            files.append(f)
    return sorted(files)


def load_jsonl(files: list) -> list:
    """Carrega todos os registros de uma lista de arquivos JSONL."""
    records = []
    for f in files:
        with open(f, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def split_by_ativo(records: list) -> dict:
    """Divide registros por ativo."""
    by_ativo = defaultdict(list)
    for r in records:
        ativo = r.get('ativo', 'UNKNOWN')
        by_ativo[ativo].append(r)
    return dict(by_ativo)


def save_parquet_pandas(records: list, out_path: Path):
    """Salva como Parquet usando pandas."""
    df = pd.DataFrame(records)
    df = df.sort_values('ts_ms').reset_index(drop=True)
    df.to_parquet(out_path, index=False, engine='pyarrow')


def save_parquet_pyarrow(records: list, out_path: Path):
    """Salva como Parquet usando pyarrow (fallback sem pandas)."""
    if not records:
        return
    # Normalizar todos os registros para ter as mesmas chaves
    all_keys = set()
    for r in records:
        all_keys.update(r.keys())
    columns = {}
    for key in sorted(all_keys):
        values = []
        for r in records:
            v = r.get(key)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            values.append(v)
        columns[key] = values
    table = pa.table(columns)
    table = table.sort_by('ts_ms')
    pq.write_table(table, out_path)


def main():
    ap = argparse.ArgumentParser(description='Converte JSONL brutos em Parquet por ativo')
    ap.add_argument('--dia', default=None, help='YYYYMMDD (default: ontem)')
    ap.add_argument('--save-dir', default=None)
    args = ap.parse_args()

    save_dir = args.save_dir or os.environ.get('SINAL_RT_DIR') or r'D:\MarketData\mimo'
    dia = args.dia or (date.today() - timedelta(days=1)).strftime('%Y%m%d')

    out_dir = Path(save_dir) / f'parquet_{dia}'
    out_dir.mkdir(exist_ok=True)

    print(f'=== Conversao JSONL -> Parquet ===')
    print(f'Dia: {dia}')
    print(f'Source: {save_dir}')
    print(f'Output: {out_dir}')

    if not HAS_PYARROW and not HAS_PANDAS:
        print('[ERRO] Nem pyarrow nem pandas instalados. Instale: pip install pyarrow pandas')
        sys.exit(1)

    total_registros = 0
    total_parquets = 0

    for tipo, prefix in [('negocios', 'raw_negocios_ms_'),
                          ('book', 'raw_book_ms_'),
                          ('rlp', 'raw_rlp_ms_')]:
        files = find_jsonl_files(save_dir, dia, prefix)
        if not files:
            print(f'\n[{tipo.upper()}] Nenhum arquivo encontrado para {dia}')
            continue

        print(f'\n[{tipo.upper()}] {len(files)} arquivo(s) encontrado(s)')
        records = load_jsonl(files)
        print(f'  Registros totais: {len(records)}')

        if not records:
            continue

        by_ativo = split_by_ativo(records)
        for ativo, regs in sorted(by_ativo.items()):
            out_path = out_dir / f'{tipo}_{ativo}_{dia}.parquet'
            try:
                if HAS_PANDAS:
                    save_parquet_pandas(regs, out_path)
                else:
                    save_parquet_pyarrow(regs, out_path)
                size_mb = out_path.stat().st_size / (1024 * 1024)
                print(f'  OK {ativo}: {len(regs)} registros -> {out_path.name} ({size_mb:.1f} MB)')
                total_registros += len(regs)
                total_parquets += 1
            except Exception as e:
                print(f'  ERRO {ativo}: {e}')

    print(f'\n=== Resumo ===')
    print(f'Total: {total_registros} registros -> {total_parquets} arquivos Parquet')
    print(f'Diretório: {out_dir}')

    return out_dir


if __name__ == '__main__':
    main()
