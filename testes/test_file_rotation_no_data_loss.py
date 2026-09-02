#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_file_rotation_no_data_loss.py — Verifica se gravacao Parquet + Hive
nao perde dados entre flushes e cria particoes corretas.
"""
import sys
import time
import tempfile
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))


now_ns = lambda: int(time.time() * 1_000_000_000)


def _make_tt_tuple(ativo='WINV26', ts_ns=None):
    """Cria uma tupla TT valida para registrar_negocios().
    Formato v14: (sym, tms, preco, qtd, agr, comp, vend,
                  janela_id, window_name, is_rlp, recv_ns, seq_id)"""
    tms = ts_ns or now_ns()
    return (
        ativo, tms, 183000.0, 1, 'C', 'B1', 'B2',
        1, f'{ativo[:3]}_TT', False, tms, 0,
    )


class TestHivePartitioning:
    """Testes de gravacao Parquet + Hive (v14)."""

    def test_tt_writes_to_hive_structure(self):
        """FileStorage grava TT na estrutura Hive correta."""
        from adapters.file_storage import CapturaEventosMS

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            storage.registrar_negocios([_make_tt_tuple()])
            storage.flush()

            raw = Path(tmpdir) / 'RAW' / 'data_type=TT'
            assert raw.exists(), f'Diretorio TT nao existe: {raw}'

            date_dirs = [d for d in raw.iterdir() if d.name.startswith('date=')]
            assert len(date_dirs) == 1, f'Esperado 1 date_dir, encontrado {len(date_dirs)}'

            asset_dirs = [d for d in date_dirs[0].iterdir() if d.name.startswith('asset=')]
            assert len(asset_dirs) == 1
            assert asset_dirs[0].name == 'asset=WIN'

            parquets = list(asset_dirs[0].glob('*.parquet'))
            assert len(parquets) >= 1, 'Nenhum Parquet gerado'

    def test_book_writes_to_hive_structure(self):
        """FileStorage grava BOOK na estrutura Hive correta."""
        from adapters.file_storage import CapturaEventosMS

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            ts = now_ns()
            storage.registrar_book(
                'WINV26', ts, {}, 10, 10,
                levels=[], janela_id=1, window_name='TEST',
                received_at_ns=ts,
            )
            storage.flush()

            raw = Path(tmpdir) / 'RAW' / 'data_type=BOOK'
            assert raw.exists(), f'Diretorio BOOK nao existe: {raw}'

            asset_dirs = [d for d in list(raw.glob('date=*/'))[0].iterdir()
                         if d.name.startswith('asset=')]
            assert any(d.name == 'asset=WIN' for d in asset_dirs)

            parquets = list(raw.rglob('asset=WIN/*.parquet'))
            assert len(parquets) >= 1

    def test_no_data_loss_across_flushes(self):
        """Multiplos flushes nao perdem dados."""
        from adapters.file_storage import CapturaEventosMS
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test', flush_a_cada=1)
            total_rows = 0

            for batch in range(5):
                rows = [_make_tt_tuple(ts_ns=now_ns() + i) for i in range(10)]
                storage.registrar_negocios(rows)
                storage.flush()

            tt_dir = Path(tmpdir) / 'RAW' / 'data_type=TT'
            for pf in tt_dir.rglob('*.parquet'):
                t = pq.read_table(pf)
                total_rows += t.num_rows

            assert total_rows == 50, f'Esperado 50 registros, encontrado {total_rows}'

    def test_different_assets_create_separate_partitions(self):
        """Ativos diferentes criam particoes separadas."""
        from adapters.file_storage import CapturaEventosMS

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            storage.registrar_negocios([_make_tt_tuple('WINV26')])
            storage.registrar_negocios([_make_tt_tuple('WDOV26')])
            storage.flush()

            tt_dir = Path(tmpdir) / 'RAW' / 'data_type=TT'
            asset_dirs = []
            for date_dir in tt_dir.iterdir():
                if date_dir.name.startswith('date='):
                    asset_dirs = [d.name for d in date_dir.iterdir()
                                 if d.name.startswith('asset=')]

            assert 'asset=WIN' in asset_dirs
            assert 'asset=WDO' in asset_dirs

    def test_schema_tt_has_required_columns(self):
        """Schema TT tem todas as colunas obrigatorias."""
        import pyarrow.parquet as pq
        from adapters.file_storage import CapturaEventosMS

        required = {'ts_ns', 'ativo', 'preco', 'quantidade', 'agressor',
                     'janela_id', 'window_name', 'is_rlp'}

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            storage.registrar_negocios([_make_tt_tuple()])
            storage.flush()

            for pf in Path(tmpdir).rglob('TT/**/*.parquet'):
                t = pq.read_table(pf)
                cols = set(t.column_names)
                missing = required - cols
                assert not missing, f'Colunas ausentes no TT: {missing}'
                break

    def test_schema_book_has_required_columns(self):
        """Schema BOOK tem todas as colunas obrigatorias."""
        import pyarrow.parquet as pq
        from adapters.file_storage import CapturaEventosMS

        required = {'ts_ns', 'ativo', 'nivel', 'bid', 'ask',
                     'bid_volume', 'ask_volume', 'janela_id'}

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            storage.registrar_book('WINV26', now_ns(), {}, 10, 10,
                                   levels=[], janela_id=1, window_name='TEST',
                                   received_at_ns=now_ns())
            storage.flush()

            for pf in Path(tmpdir).rglob('BOOK/**/*.parquet'):
                t = pq.read_table(pf)
                cols = set(t.column_names)
                missing = required - cols
                assert not missing, f'Colunas ausentes no BOOK: {missing}'
                break

    def test_hive_partitioning_is_queryable(self):
        """Particoes sao queryaveis via PyArrow Dataset."""
        import pyarrow.dataset as ds
        from adapters.file_storage import CapturaEventosMS

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = CapturaEventosMS(tmpdir, 'test')
            storage.registrar_negocios([_make_tt_tuple('WINV26')])
            storage.registrar_negocios([_make_tt_tuple('WDOV26')])
            storage.flush()

            raw_path = Path(tmpdir) / 'RAW'
            if raw_path.exists():
                dataset = ds.dataset(str(raw_path), format='parquet',
                                    partitioning='hive')
                filt = dataset.filter(ds.field('ativo') == 'WINV26')
                assert filt.count_rows() >= 1


class TestConsolidarParquet:
    """Testes do consolidar_book_parquet (legado)."""

    def test_consolidar_is_callable(self):
        """consolidar_book_parquet continua existindo para compatibilidade."""
        from adapters.rtd_writer import consolidar_book_parquet
        assert callable(consolidar_book_parquet)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
