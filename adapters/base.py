# -*- coding: utf-8 -*-
"""
adapters/base.py — Interfaces base para fontes de dados de mercado.
"""

from abc import ABC, abstractmethod
from typing import Iterator, Optional
from core.contracts import MarketEvent

class MarketDataSource(ABC):
    """Interface abstrata para fontes de dados (Live Profit RTD ou Replay)."""
    
    @abstractmethod
    def connect(self) -> bool:
        """Inicia a conexão com a fonte de dados."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Encerra a conexão e libera recursos (COM, File Handles)."""
        pass

    @abstractmethod
    def events(self) -> Iterator[MarketEvent]:
        """
        Gerador que emite eventos normalizados.
        O Core consumirá esta stream de forma agnóstica à origem.
        """
        pass

    @abstractmethod
    def get_health(self) -> dict:
        """Retorna dicionário com métricas de latência e conectividade."""
        pass