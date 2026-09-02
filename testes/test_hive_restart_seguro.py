# -*- coding: utf-8 -*-
"""
test_hive_restart_seguro.py — Testes dos 3 fixes v14.7 de captura RTD.

1. Contador de parte persiste no restart (não sobrescreve part-0000)
2. Partição Hive usa a data da SESSÃO, não date.today()
3. Consolidação de fragmentos no startup
"""
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.file_storage import CapturaEventosMS

TMP = Path(__file__).resolve().parent.parent / '_tmp_hive_test'


def _tt_tuple(sym='WINV26', ts=None, preco=170000.0, qtd=1):
    ts = ts or int(time.time() * 1000)
    return (sym, ts, preco, qtd, 'Comprador', 'XP', 'BTG',
            0, '', False, ts * 1_000_000, 1)


@pytest.fixture(autouse=True)
def _limpar_tmp():
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    yield
    if TMP.exists():
        shutil.rmtree(TMP)


def test_restart_nao_sobrescreve_part0000():
    """Fix 1: após restart, nova sessão continua numeração em vez de
    sobrescrever part-0000 existente."""
    # Sessão 1: grava 1 lote -> part-0000
    s1 = CapturaEventosMS(str(TMP), '20260902_100000', flush_a_cada=1)
    s1.registrar_negocios([_tt_tuple()])
    s1.flush()
    part0000 = TMP / 'RAW' / 'data_type=TT' / 'date=20260902' / 'asset=WIN' / 'part-0000.parquet'
    assert part0000.exists(), 'part-0000 deve existir após sessão 1'

    # Sessão 2 (restart): grava outro lote
    s2 = CapturaEventosMS(str(TMP), '20260902_110000', flush_a_cada=1)
    s2.registrar_negocios([_tt_tuple()])
    s2.flush()

    files = sorted((TMP / 'RAW' / 'data_type=TT' / 'date=20260902' / 'asset=WIN').glob('part-*.parquet'))
    assert len(files) >= 2, f'Esperado >=2 arquivos (restart não sobrescreve), got {len(files)}'
    # Ambos devem ter 1 linha (nenhum foi sobrescrito)
    import pyarrow.parquet as pq
    for f in files:
        assert pq.read_metadata(str(f)).num_rows == 1, f'{f.name} deveria ter 1 linha'


def test_particao_usa_data_da_sessao():
    """Fix 2: partição Hive usa session_ts (YYYYMMDD), não date.today()."""
    s = CapturaEventosMS(str(TMP), '20251231_235900', flush_a_cada=1)
    s.registrar_negocios([_tt_tuple()])
    s.flush()
    # Deve estar em date=20251231, independente da data de hoje
    d = TMP / 'RAW' / 'data_type=TT' / 'date=20251231' / 'asset=WIN'
    assert d.exists(), f'Partição deveria ser date=20251231, mas {d} não existe'


def test_consolidacao_no_startup():
    """Fix 3: fragmentos existentes são consolidados em 1 arquivo no startup."""
    # Cria 3 fragmentos manualmente na partição (sem passar pelo __init__
    # que já consolida — aqui simulamos fragmentos de sessões legadas)
    asset_dir = TMP / 'RAW' / 'data_type=TT' / 'date=20260902' / 'asset=WIN'
    asset_dir.mkdir(parents=True, exist_ok=True)
    import pyarrow as pa
    import pyarrow.parquet as pq
    for i in range(3):
        row = [{
            'ts_ns': int(time.time() * 1e9) + i,
            'received_at_ns': int(time.time() * 1e9) + i,
            'sequence_id': i,
            'ativo': 'WINV26',
            'asset_partition': 'WIN',
            'janela_id': 0,
            'window_name': '',
            'is_rlp': False,
            'preco': 170000.0 + i,
            'quantidade': 1,
            'agressor': 'Comprador',
            'compradora': 'XP',
            'vendedora': 'BTG',
        }]
        pq.write_table(pa.Table.from_pylist(row), str(asset_dir / f'part-{i:04d}.parquet'),
                       compression='snappy')

    files = list(asset_dir.glob('part-*.parquet'))
    assert len(files) == 3, f'Pré-condição: 3 fragmentos, got {len(files)}'

    # Novo startup: deve consolidar em 1 arquivo com 3 linhas
    s4 = CapturaEventosMS(str(TMP), '20260902_130000', flush_a_cada=1)
    files_apos = list(asset_dir.glob('part-*.parquet'))
    assert len(files_apos) == 1, f'Após consolidação: 1 arquivo, got {len(files_apos)}'

    import pyarrow.parquet as pq
    assert pq.read_metadata(str(files_apos[0])).num_rows == 3, '3 linhas consolidadas'

    # Nova escrita continua a numeração (part-0001)
    s4.registrar_negocios([_tt_tuple(preco=170003.0)])
    s4.flush()
    files_final = sorted(asset_dir.glob('part-*.parquet'))
    assert len(files_final) == 2, f'part-0000 (consolidado) + part-0001 (novo), got {len(files_final)}'
    assert pq.read_metadata(str(files_final[0])).num_rows == 3
    assert pq.read_metadata(str(files_final[1])).num_rows == 1


def test_consolidacao_falha_nao_perde_dados():
    """Fix 3: se a consolidação falhar, fragmentos originais permanecem."""
    s1 = CapturaEventosMS(str(TMP), '20260902_100000', flush_a_cada=1)
    s1.registrar_negocios([_tt_tuple()])
    s1.flush()

    asset_dir = TMP / 'RAW' / 'data_type=TT' / 'date=20260902' / 'asset=WIN'
    # Corrompe um fragmento
    bad = asset_dir / 'part-0001.parquet'
    bad.write_bytes(b'corrupt data not parquet')

    # Startup: consolidação deve falhar mas não apagar nada
    s2 = CapturaEventosMS(str(TMP), '20260902_110000', flush_a_cada=1)
    files = list(asset_dir.glob('part-*.parquet'))
    assert len(files) >= 2, 'Fragmentos originais preservados apesar da falha'