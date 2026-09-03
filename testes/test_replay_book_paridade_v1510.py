# -*- coding: utf-8 -*-
"""
testes/test_replay_book_paridade_v1510.py — Paridade live/replay do BOOK (P0-A14).

Antes do fix, o ReplayEngine jamais alimentava o book: processar_lote lia o
OFITracker (alimentado somente por MarketState.alimentar_book), então
ofi_total/ofi_ewma ficavam ZERADOS no replay enquanto eram reais no live —
o sinal do replay divergia do sinal real (pesos de ofi/book_* em learning.py).

Cenários:
  1. _ler_book_hive lê snapshots do Parquet Hive e monta bids desc / asks asc
  2. Book alimentado ANTES do trade do mesmo instante (merge temporal)
  3. Sem BOOK Hive → replay segue funcionando (comportamento legado)
  4. Sem book no dia → aviso e _books vazio (sem crash)
"""

import os
import sys
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

from replay_engine import ReplayEngine  # noqa: E402

DIA = "20260901"


def _escrever_book_hive(base_dir, dia=DIA):
    """Grava 2 snapshots de BOOK (WIN) num tmp dir com o schema real do RAW."""
    from adapters.file_storage import BOOK_SCHEMA

    hdir = Path(base_dir) / "RAW" / "data_type=BOOK" / f"date={dia}" / "asset=WIN"
    hdir.mkdir(parents=True, exist_ok=True)

    # Snapshot 1 (t=1000ms): bid 500 @ 170000.0 / ask 400 @ 170000.2 — 3 níveis
    # Snapshot 2 (t=2000ms): bid 600 @ 170000.0 / ask 300 @ 170000.2 — book muda
    rows = []
    snap1 = [
        # nivel, bid, bid_volume, ask, ask_volume
        (0, 170000.0, 500, 170000.2, 400),
        (1, 169999.8, 300, 170000.4, 250),
        (2, 169999.6, 100, 170000.6, 50),
    ]
    snap2 = [
        (0, 170000.0, 600, 170000.2, 300),
        (1, 169999.8, 350, 170000.4, 200),
        (2, 169999.6, 120, 170000.6, 40),
    ]
    for snap, ts in ((snap1, 1000), (snap2, 2000)):
        ts_ns = ts * 1_000_000
        for nivel, bid, bv, ask, av in snap:
            rows.append({
                "ts_ns": ts_ns,
                "received_at_ns": ts_ns + 1_000_000,
                "sequence_id": 1,
                "ativo": "WINV26",
                "asset_partition": "WIN",
                "janela_id": 1,
                "window_name": "BOOK1",
                "nivel": nivel,
                "bid": bid,
                "ask": ask,
                "bid_volume": bv,
                "ask_volume": av,
                "bid_vol_total": 900,
                "ask_vol_total": 700,
                "por_corretora": "{}",
                "ofi": None,
            })
    tbl = pa.Table.from_pylist(rows, schema=BOOK_SCHEMA)
    pq.write_table(tbl, hdir / "part-0000.parquet", compression="snappy")


def _escrever_tt_hive(base_dir, dia=DIA):
    """Grava 2 trades de WIN (t=1000ms e t=3000ms) com o schema real do RAW."""
    from adapters.file_storage import TT_SCHEMA

    hdir = Path(base_dir) / "RAW" / "data_type=TT" / f"date={dia}" / "asset=WIN"
    hdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for ts in (1000, 3000):
        rows.append({
            "ts_ns": ts * 1_000_000,
            "received_at_ns": ts * 1_000_000 + 1_000_000,
            "sequence_id": ts,
            "ativo": "WINV26",
            "asset_partition": "WIN",
            "janela_id": 1,
            "window_name": "TT1",
            "is_rlp": False,
            "preco": 170000.0,
            "quantidade": 5,
            "agressor": "Comprador",
            "compradora": "XP",
            "vendedora": "BTG",
        })
    tbl = pa.Table.from_pylist(rows, schema=TT_SCHEMA)
    pq.write_table(tbl, hdir / "part-0000.parquet", compression="snappy")


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow ausente")
class TestReplayBookParidade:
    def test_ler_book_hive_monta_snapshots_ordenados(self, tmp_path):
        """Snapshot com bids desc (melhor primeiro) e asks asc."""
        _escrever_book_hive(str(tmp_path))
        eng = ReplayEngine(config={"save_dir": str(tmp_path)}, instrumentos=["WINV26"])
        books = eng._ler_book_hive(str(tmp_path), dia_str=DIA)
        assert "WINV26" in books, f"snapshots esperados p/ WINV26, veio: {list(books)}"
        snaps = books["WINV26"]
        assert len(snaps) == 2
        ts0, bp0, bv0, ap0, av0 = snaps[0]
        assert ts0 == 1000
        assert bp0 == [170000.0, 169999.8, 169999.6]  # desc
        assert ap0 == [170000.2, 170000.4, 170000.6]  # asc
        assert bv0 == [500, 300, 100]
        assert snaps[1][0] == 2000  # ordenado por ts

    def test_book_alimentado_antes_do_trade(self, tmp_path):
        """Merge temporal: ao processar trade t=3000ms, os 2 snapshots de book
        (t=1000 e t=2000) ja foram alimentados — OFI e book_stats reais."""
        _escrever_book_hive(str(tmp_path))
        eng = ReplayEngine(config={"save_dir": str(tmp_path)}, instrumentos=["WINV26"])
        eng._init_camadas()
        eng._books = eng._ler_book_hive(str(tmp_path), dia_str=DIA)
        eng._book_idx = {}
        assert "WINV26" in eng._books

        eng._process_neg({
            "ativo": "WINV26", "ts_ms": 3000, "preco": 170000.0, "qtd": 5,
            "agressor": "Comprador", "compradora": "XP", "vendedora": "BTG",
        })

        # Os 2 snapshots devem ter sido alimentados (indice no fim)
        assert eng._book_idx["WINV26"] == 2
        bs = eng.state.book_stats.get("WINV26")
        assert bs is not None and bs != {}, "book_stats vazio — book nao alimentado"
        # 2o snapshot tem ant -> imb calculado
        assert "imb" in bs
        # OFI tracker alimentado (volume mudou entre snapshots)
        ofi_d = eng.state.trackers["WINV26"]["ofi"].get_ofi()
        assert ofi_d["ofi_total"] != 0, "OFI zerado — tracker nao alimentado pelo book"

    def test_book_nao_alimentado_sem_arquivos(self, tmp_path):
        """Sem BOOK Hive: _ler_book_hive retorna {} e o replay nao crasha."""
        eng = ReplayEngine(config={"save_dir": str(tmp_path)}, instrumentos=["WINV26"])
        eng._init_camadas()
        books = eng._ler_book_hive(str(tmp_path), dia_str=DIA)
        assert books == {}
        # trade normal continua funcionando
        eng._process_neg({
            "ativo": "WINV26", "ts_ms": 3000, "preco": 170000.0, "qtd": 5,
            "agressor": "Comprador", "compradora": "XP", "vendedora": "BTG",
        })

    def test_book_apos_trade_nao_vaza_para_frente(self, tmp_path):
        """Book com ts futuro nao e alimentado pelo trade presente."""
        _escrever_book_hive(str(tmp_path))
        eng = ReplayEngine(config={"save_dir": str(tmp_path)}, instrumentos=["WINV26"])
        eng._init_camadas()
        eng._books = eng._ler_book_hive(str(tmp_path), dia_str=DIA)
        eng._book_idx = {}
        # trade em t=100ms: nenhum snapshot (t>=1000) deve ser alimentado
        eng._process_neg({
            "ativo": "WINV26", "ts_ms": 100, "preco": 170000.0, "qtd": 5,
            "agressor": "Comprador", "compradora": "XP", "vendedora": "BTG",
        })
        assert eng._book_idx.get("WINV26", 0) == 0
        assert eng.state.book_stats.get("WINV26") in (None, {})
