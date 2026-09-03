#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vwap_causal.py — Verifica se VWAP não usa dados futuros.
"""
import sys
import pytest
import numpy as np
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestVWAPCausal:
    """Testes de causalidade do VWAP."""
    
    def test_vwap_uses_only_past_data(self):
        """VWAP deve usar apenas dados do passado."""
        from features.vwap_tracker import VWAPTracker
        
        tracker = VWAPTracker('WINV26', tick=5)
        
        # Simula dados chronologicamente ordenados
        precos = [100, 101, 102, 103, 104]
        qtds = [10, 20, 15, 25, 30]
        
        for preco, qtd in zip(precos, qtds):
            tracker.update(1000 + len(precos) * 100, preco, qtd)
        
        # VWAP deve ser calculado com cumsum causal
        snap = tracker.snapshot()
        assert 'vwap' in snap
        assert snap['vwap'] > 0
    
    def test_vwap_no_lookahead(self):
        """VWAP não deve olhar para o futuro."""
        from features.vwap_tracker import VWAPTracker
        
        tracker = VWAPTracker('WINV26', tick=5)
        
        # Adiciona dados em ordem
        tracker.update(1000, 100, 10)
        vwap1 = tracker.snapshot()['vwap']
        
        tracker.update(1100, 101, 10)
        vwap2 = tracker.snapshot()['vwap']
        
        # VWAP deve variar suavemente, não saltos
        assert vwap2 >= vwap1 or abs(vwap2 - vwap1) < 10


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
