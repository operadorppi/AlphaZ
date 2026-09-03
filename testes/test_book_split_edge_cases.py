#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_book_split_edge_cases.py — Testa edge cases de book_split.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestBookSplitEdgeCases:
    """Testes de edge cases para book_split."""
    
    def test_book_split_zero(self):
        """book_split=0 deve criar listas vazias ou usar default."""
        from core.market_state import MarketState
        import config
        
        original = config.CONFIG.get('book_split', 30)
        config.CONFIG['book_split'] = 0
        
        try:
            state = MarketState()
            # Com book_split=0, deve usar default (30) ou criar lista vazia
            assert hasattr(state, 'book_bid')
            assert hasattr(state, 'book_ask')
        finally:
            config.CONFIG['book_split'] = original
    
    def test_book_split_negative_raises(self):
        """book_split negativo deve levantar ValueError."""
        from core.market_state import MarketState
        import config
        
        original = config.CONFIG.get('book_split', 30)
        config.CONFIG['book_split'] = -1
        
        try:
            # v14.8: MarketState() agora lê o CONFIG legado como fallback
            # (mesma resolução do App) — book_split negativo levanta ValueError
            with pytest.raises(ValueError):
                MarketState()
        finally:
            config.CONFIG['book_split'] = original
    
    def test_book_split_large(self):
        """book_split muito grande não deve causar problema."""
        from core.market_state import MarketState
        import config
        
        original = config.CONFIG.get('book_split', 30)
        config.CONFIG['book_split'] = 100
        
        try:
            state = MarketState()
            # Pode usar default se houver limitação
            assert hasattr(state, 'book_bid')
            assert len(state.book_bid) > 0
        finally:
            config.CONFIG['book_split'] = original


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
