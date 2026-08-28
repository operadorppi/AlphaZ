# -*- coding: utf-8 -*-
"""
adapters/replay.py — Implementação de fonte de dados para replay determinístico.
"""

import json
import logging
from typing import Iterator
from pathlib import Path
from adapters.base import MarketDataSource
from core.contracts import MarketEvent, TradeEvent, BookSnapshot, BookLevel

log = logging.getLogger(__name__)

class ReplayAdapter(MarketDataSource):
    """Lê arquivos JSONL capturados e os transforma em uma stream de MarketEvents."""
    
    def __init__(self, base_dir: str, session_ts: str):
        self.base_dir = Path(base_dir)
        self.session_ts = session_ts
        self._neg_file = self.base_dir / f"raw_negocios_ms_{self.session_ts}.jsonl"
        self._book_file = self.base_dir / f"raw_book_ms_{self.session_ts}.jsonl"

    def connect(self) -> bool:
        if not self._neg_file.exists():
            log.warning(f"Arquivo de negócios não encontrado: {self._neg_file}")
            return False
        return True

    def disconnect(self) -> None:
        pass

    def events(self) -> Iterator[MarketEvent]:
        """Emite eventos do arquivo de negócios capturados."""
        if not self._neg_file.exists():
            return

        with open(self._neg_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    ts = data.get('ts_ms')
                    
                    # Converte JSON bruto para Contrato de Trade
                    trade = TradeEvent(
                        symbol=data['ativo'],
                        timestamp_ms=ts,
                        price=data['preco'],
                        quantity=data['qtd'],
                        aggressor=data['agressor'],
                        buyer=data.get('compradora', ''),
                        seller=data.get('vendedora', ''),
                        received_at=ts
                    )
                    
                    yield MarketEvent(
                        type='TRADE',
                        payload=trade,
                        timestamp_ms=ts
                    )
                except Exception as e:
                    log.error(f"Erro ao parsear linha de replay: {e}")
                    continue

    def get_health(self) -> dict:
        """Retorna o status do motor de replay."""
        return {
            "status": "ok",
            "sessao": self.session_ts,
            "arquivo_origem": str(self._neg_file)
        }