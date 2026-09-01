# -*- coding: utf-8 -*-
"""
adapters/replay.py — Implementação de fonte de dados para replay determinístico.

v14: Lê Parquet da estrutura Hive:
  RAW/data_type=TT/date=20260901/asset=WIN/part-0.parquet
"""

import json
import logging
from typing import Iterator
from pathlib import Path
from adapters.base import MarketDataSource
from adapters.file_storage import find_hive_files
from core.contracts import MarketEvent, TradeEvent, BookSnapshot, BookLevel

log = logging.getLogger(__name__)


class ReplayAdapter(MarketDataSource):
    """Lê Parquet hive e emite MarketEvents para replay."""

    def __init__(self, base_dir: str, dia_str: str = None,
                 ativo: str = None, session_ts: str = None):
        self.base_dir = Path(base_dir)
        self.dia_str = dia_str
        self.ativo = ativo

        # Buscar arquivos Parquet hive
        # Converter ativo (ex: WINV26) para asset_partition (ex: WIN)
        asset_part = ativo
        if ativo:
            for suffix in ('V26', 'V25', 'V24', 'M26', 'M25', 'M24', 'U26', 'U25'):
                if ativo.endswith(suffix):
                    asset_part = ativo[:-len(suffix)]
                    break
        self._tt_files = find_hive_files(base_dir, dia_str=dia_str,
                                         data_type='TT', asset=asset_part)
        self._book_files = find_hive_files(base_dir, dia_str=dia_str,
                                           data_type='BOOK', asset=asset_part)

        # Fallback: formato legado (JSONL)
        if not self._tt_files and session_ts:
            # Tentar JSONL legado
            legado_neg = self.base_dir / f"raw_negocios_ms_{session_ts}.jsonl"
            if legado_neg.exists():
                self._tt_files = [legado_neg]

    def connect(self) -> bool:
        if not self._tt_files:
            log.warning(f"Nenhum arquivo TT encontrado")
            return False
        return True

    def disconnect(self) -> None:
        pass

    def events(self) -> Iterator[MarketEvent]:
        """Emite eventos de todos os arquivos Parquet TT."""
        try:
            import pyarrow.parquet as pq
        except ImportError:
            log.error("PyArrow necessário para replay v14")
            return

        for tt_file in self._tt_files:
            try:
                table = pq.read_table(tt_file)
                # Converter para dicts
                records = table.to_pydict()
                n_rows = len(records.get('ts_ns', records.get('ts_ms', [])))

                for i in range(n_rows):
                    # v14.1: schema usa ts_ns e quantidade
                    ts_ns = records.get('ts_ns', [0]*n_rows)[i]
                    ts_ms = ts_ns // 1_000_000 if ts_ns > 1e15 else (records.get('ts_ms', [0]*n_rows)[i])
                    is_rlp = records.get('is_rlp', [False]*n_rows)[i] if 'is_rlp' in records else False
                    recv_ns = records.get('received_at_ns', [ts_ns]*n_rows)[i] if 'received_at_ns' in records else ts_ns

                    trade = TradeEvent(
                        symbol=records['ativo'][i],
                        timestamp_ms=ts_ms,
                        price=records['preco'][i],
                        quantity=int(records.get('quantidade', records.get('qtd', [0]*n_rows))[i]),
                        aggressor=records['agressor'][i],
                        buyer=records.get('compradora', ['']*n_rows)[i],
                        seller=records.get('vendedora', ['']*n_rows)[i],
                        received_at_ns=recv_ns,
                    )

                    event_type = 'RLP' if is_rlp else 'TRADE'
                    janela_id = records.get('janela_id', [0]*n_rows)[i] if 'janela_id' in records else 0
                    window_name = records.get('window_name', ['']*n_rows)[i] if 'window_name' in records else ''

                    yield MarketEvent(
                        type=event_type,
                        payload=trade,
                        timestamp_ms=ts_ms,
                        symbol=trade.symbol,
                        janela_id=janela_id,
                        window_name=window_name,
                        is_rlp=is_rlp,
                    )
            except Exception as e:
                log.error(f"Erro ao ler {tt_file}: {e}")
                continue

    def get_health(self) -> dict:
        return {
            "status": "ok",
            "arquivos_tt": [str(f) for f in self._tt_files],
            "arquivos_book": [str(f) for f in self._book_files],
        }
