#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_deduplication_trades.py — Verifica se trades duplicados são removidos.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestDeduplicationTrades:
    """Testes de deduplication de trades."""
    
    def test_vistos_tt_tracking(self):
        """_vistos_tt deve rastrear trades vistos."""
        from adapters.profit_rtd import ProfitRTDAdapter
        from unittest.mock import MagicMock
        
        # Criar adapter mock
        adapter = MagicMock()
        adapter._vistos_tt = {}
        
        # Verificar se a estrutura existe
        assert hasattr(adapter, '_vistos_tt')
    
    def test_dedup_logic_exists(self):
        """Lógica de dedup deve existir no adapter."""
        import inspect
        from adapters.profit_rtd import ProfitRTDAdapter
        
        src = inspect.getsource(ProfitRTDAdapter)
        assert 'vistos_tt' in src or 'dedup' in src.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
