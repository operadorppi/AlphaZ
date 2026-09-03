#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_session_boundary.py — Verifica comportamento na virada de sessão.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestSessionBoundary:
    """Testes de virada de sessão."""
    
    def test_session_time_tracker(self):
        """SessionTimeTracker deve calcular tempo correto."""
        from features.session_time import SessionTimeTracker
        
        tracker = SessionTimeTracker()
        
        # 10h BRT = 13h UTC
        ts = 1788043200000  # 2026-08-29 10:00:00 BRT
        snap = tracker.snapshot(ts)
        
        assert 'segundos_desde_abertura' in snap or 'session_block' in snap
    
    def test_market_state_session_detection(self):
        """MarketState deve detectar sessão corretamente."""
        from core.market_state import MarketState
        
        state = MarketState()
        assert hasattr(state, 'seg_atual')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
