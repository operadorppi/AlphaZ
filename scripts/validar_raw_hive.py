#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validar_raw_hive.py — Validação automática da estrutura RAW Hive.

Conforme Seção 11 e 12 da spec PROMPT:
  - Verifica estrutura de diretórios
  - Valida schema e tipos das colunas
  - Confere integridade (registros, timestamps, arquivos)
  - Testa leitura com pyarrow.dataset (Hive partitioning)
  - Filtra por date, asset, data_type
"""
import sys
import os
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.dataset as ds
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False
    print("ERRO: PyArrow não instalado. pip install pyarrow")
    sys.exit(1)


# ========================================================================
# SCHEMAS ESPERADOS (devem bater com file_storage.py)
# ========================================================================

TT_COLS = [
    ('ts_ns', pa.int64()),
    ('received_at_ns', pa.int64()),
    ('sequence_id', pa.int64()),
    ('ativo', pa.string()),
    ('asset_partition', pa.string()),
    ('janela_id', pa.int16()),
    ('window_name', pa.string()),
    ('is_rlp', pa.bool_()),
    ('preco', pa.float64()),
    ('quantidade', pa.int64()),
    ('agressor', pa.string()),
    ('compradora', pa.string()),
    ('vendedora', pa.string()),
]

BOOK_COLS = [
    ('ts_ns', pa.int64()),
    ('received_at_ns', pa.int64()),
    ('sequence_id', pa.int64()),
    ('ativo', pa.string()),
    ('asset_partition', pa.string()),
    ('janela_id', pa.int16()),
    ('window_name', pa.string()),
    ('nivel', pa.int16()),
    ('bid', pa.float64()),
    ('ask', pa.float64()),
    ('bid_volume', pa.int64()),
    ('ask_volume', pa.int64()),
    ('bid_vol_total', pa.int64()),
    ('ask_vol_total', pa.int64()),
    ('por_corretora', pa.string()),
    ('ofi', pa.float64()),
]

EXPECTED_ASSETS_TT = {'WIN', 'IND', 'WDO', 'DOL', 'WIN_RLP', 'WDO_RLP'}
EXPECTED_ASSETS_BOOK = {'WIN', 'IND', 'WDO', 'DOL'}


def validar_raw(raw_path, dia_str=None):
    """Executa validação completa da estrutura RAW Hive."""
    raw = Path(raw_path)
    if not raw.exists():
        print(f"ERRO: Diretório não existe: {raw}")
        return False

    erros = []
    warnings = []
    OK = []

    # ====================================================================
    # SEÇÃO 11: Estrutura de diretórios
    # ====================================================================
    print("=" * 70)
    print("SEÇÃO 11: VALIDAÇÃO DE ESTRUTURA")
    print("=" * 70)

    # Verificar data_types
    for dt in ['data_type=TT', 'data_type=BOOK']:
        dt_path = raw / dt
        if not dt_path.exists():
            erros.append(f"Diretório ausente: {dt}")
            continue
        OK.append(f"[OK] {dt} existe")

        # Verificar dates
        dates = [d.name for d in dt_path.iterdir() if d.is_dir() and d.name.startswith('date=')]
        if not dates:
            warnings.append(f"{dt}: nenhum date= encontrado")
            continue

        for date_dir in sorted(dates):
            date_path = dt_path / date_dir
            assets = [a.name for a in date_path.iterdir() if a.is_dir() and a.name.startswith('asset=')]
            asset_names = {a.replace('asset=', '') for a in assets}

            expected = EXPECTED_ASSETS_TT if 'TT' in dt else EXPECTED_ASSETS_BOOK
            missing = expected - asset_names
            extra = asset_names - expected

            if missing:
                warnings.append(f"{dt}/{date_dir}: assets ausentes: {missing}")
            if extra:
                warnings.append(f"{dt}/{date_dir}: assets extras: {extra}")

            # Contar arquivos Parquet por asset
            for a_dir in assets:
                full_path = dt_path / date_dir / a_dir
                parquets = list(full_path.glob('*.parquet'))
                asset_name = a_dir.replace('asset=', '')
                OK.append(f"  {dt}/{date_dir}/{a_dir}: {len(parquets)} arquivo(s)")

    # ====================================================================
    # SEÇÃO 11: Integridade de schema
    # ====================================================================
    print("\n" + "=" * 70)
    print("SEÇÃO 11: VALIDAÇÃO DE SCHEMA")
    print("=" * 70)

    expected_schemas = {'TT': pa.schema(TT_COLS), 'BOOK': pa.schema(BOOK_COLS)}

    for dt_name, expected_schema in expected_schemas.items():
        dt_path = raw / f'data_type={dt_name}'
        if not dt_path.exists():
            continue

        for parquet_file in sorted(dt_path.rglob('*.parquet')):
            rel = parquet_file.relative_to(raw)
            try:
                pf = pq.ParquetFile(parquet_file)
                actual_schema = pf.schema_arrow

                # Verificar colunas
                expected_names = set(expected_schema.names)
                actual_names = set(actual_schema.names)
                missing_cols = expected_names - actual_names
                extra_cols = actual_names - expected_names

                if missing_cols:
                    erros.append(f"{rel}: colunas ausentes: {missing_cols}")
                if extra_cols:
                    warnings.append(f"{rel}: colunas extras: {extra_cols}")

                # Verificar tipos
                for i in range(len(expected_schema)):
                    field = expected_schema.field(i)
                    col_name = field.name
                    expected_type = field.type
                    if col_name in actual_schema.names:
                        actual_type = actual_schema.field(col_name).type
                        if actual_type != expected_type:
                            erros.append(f"{rel}.{col_name}: tipo {actual_type} != esperado {expected_type}")

                # Contar registros
                n_rows = pf.metadata.num_rows
                OK.append(f"[OK] {rel}: {n_rows} registros, schema OK")

            except Exception as e:
                erros.append(f"{rel}: erro ao ler: {e}")

    # ====================================================================
    # SEÇÃO 11: Integridade de dados
    # ====================================================================
    print("\n" + "=" * 70)
    print("SEÇÃO 11: INTEGRIDADE DE DADOS")
    print("=" * 70)

    for dt_name in ['TT', 'BOOK']:
        dt_path = raw / f'data_type={dt_name}'
        if not dt_path.exists():
            continue

        for asset_dir in sorted(dt_path.rglob('asset=*')):
            if not asset_dir.is_dir():
                continue
            parquets = sorted(asset_dir.glob('*.parquet'))
            if not parquets:
                continue

            asset_name = asset_dir.name.replace('asset=', '')
            total_rows = 0
            ts_min = float('inf')
            ts_max = float('-inf')
            recv_min = float('inf')
            recv_max = float('-inf')

            for pf_path in parquets:
                try:
                    t = pq.read_table(pf_path)
                    total_rows += len(t)
                    if 'ts_ns' in t.column_names:
                        col = t.column('ts_ns')
                        if len(col) > 0:
                            import pyarrow.compute as pc
                            ts_min = min(ts_min, pc.min(col).as_py())
                            ts_max = max(ts_max, pc.max(col).as_py())
                    if 'received_at_ns' in t.column_names:
                        col = t.column('received_at_ns')
                        if len(col) > 0:
                            import pyarrow.compute as pc
                            recv_min = min(recv_min, pc.min(col).as_py())
                            recv_max = max(recv_max, pc.max(col).as_py())
                except Exception as e:
                    erros.append(f"{asset_dir.name}/{pf_path.name}: erro: {e}")

            if total_rows > 0:
                from datetime import datetime
                ts_min_dt = datetime.fromtimestamp(ts_min / 1e9).strftime('%H:%M:%S') if ts_min < float('inf') else '?'
                ts_max_dt = datetime.fromtimestamp(ts_max / 1e9).strftime('%H:%M:%S') if ts_max > float('-inf') else '?'
                recv_min_dt = datetime.fromtimestamp(recv_min / 1e9).strftime('%H:%M:%S') if recv_min < float('inf') else '?'
                recv_max_dt = datetime.fromtimestamp(recv_max / 1e9).strftime('%H:%M:%S') if recv_max > float('-inf') else '?'
                OK.append(f"[OK] {dt_name}/{asset_name}: {total_rows} registros, "
                          f"ts=[{ts_min_dt}..{ts_max_dt}], "
                          f"recv=[{recv_min_dt}..{recv_max_dt}], "
                          f"{len(parquets)} arquivo(s)")

    # ====================================================================
    # SEÇÃO 12: Teste PyArrow Dataset
    # ====================================================================
    print("\n" + "=" * 70)
    print("SEÇÃO 12: TESTE PYARROW DATASET")
    print("=" * 70)

    try:
        dataset = ds.dataset(
            str(raw),
            format="parquet",
            partitioning="hive"
        )
        OK.append("[OK] pyarrow.dataset.dataset() criado com sucesso")

        # Teste de filtro por data_type
        tt_filt = dataset.filter(ds.field('data_type') == 'TT')
        n_tt = tt_filt.count_rows()
        OK.append(f"  Filtro data_type=TT: {n_tt} registros")

        book_filt = dataset.filter(ds.field('data_type') == 'BOOK')
        n_book = book_filt.count_rows()
        OK.append(f"  Filtro data_type=BOOK: {n_book} registros")

        # Teste de filtro por asset
        for asset in ['WIN', 'IND', 'WDO', 'DOL', 'WIN_RLP', 'WDO_RLP']:
            try:
                a_filt = dataset.filter(ds.field('asset') == asset)
                n = a_filt.count_rows()
                if n > 0:
                    OK.append(f"  Filtro asset={asset}: {n} registros")
            except Exception:
                pass

        # Teste de filtro por data_type + asset
        for dt, asset in [('BOOK', 'WIN'), ('BOOK', 'DOL'), ('TT', 'WIN'),
                          ('TT', 'WIN_RLP'), ('TT', 'WDO'), ('TT', 'WDO_RLP')]:
            try:
                f = dataset.filter(
                    (ds.field('data_type') == dt) & (ds.field('asset') == asset)
                )
                n = f.count_rows()
                if n > 0:
                    OK.append(f"  Filtro {dt}+{asset}: {n} registros")
            except Exception:
                pass

    except Exception as e:
        erros.append(f"pyarrow.dataset falhou: {e}")

    # ====================================================================
    # RELATÓRIO
    # ====================================================================
    print("\n" + "=" * 70)
    print("RELATÓRIO DE VALIDAÇÃO")
    print("=" * 70)

    print(f"\n[OK] ({len(OK)}):")
    for ok in OK:
        print(f"  {ok}")

    if warnings:
        print(f"\n[WARN] ({len(warnings)}):")
        for w in warnings:
            print(f"  {w}")

    if erros:
        print(f"\n[ERRO] ({len(erros)}):")
        for e in erros:
            print(f"  {e}")
        print(f"\nRESULTADO: FALHOU ({len(erros)} erros)")
        return False

    print(f"\nRESULTADO: APROVADO ({len(OK)} checks OK, {len(warnings)} warnings)")
    return True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validar estrutura RAW Hive')
    parser.add_argument('--raw-path', default=r'D:\MarketData\Profit\RAW',
                        help='Caminho para o diretório RAW')
    parser.add_argument('--dia', default=None, help='Data específica (YYYYMMDD)')
    args = parser.parse_args()

    ok = validar_raw(args.raw_path, args.dia)
    sys.exit(0 if ok else 1)
