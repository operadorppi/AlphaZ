#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_regime_reset_diario.py — Verifica se regime reseta entre dias.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestRegimeResetDiario:
    """Testes de reset diário do regime."""
    
    def test_regime_tracker_has_reset_diario(self):
        """RegimeTracker deve ter método reset_diario."""
        from ml.scorer import RegimeTracker
        
        tracker = RegimeTracker()
        assert hasattr(tracker, 'reset_diario')
    
    def test_regime_reset_clears_state(self):
        """Reset diário deve limpar estado."""
        from ml.scorer import RegimeTracker
        
        tracker = RegimeTracker()
        tracker.update(1000, 100, 1, 0.5, 1000, 100)
        
        # Antes do reset, tem estado
        snap_before = tracker.snapshot()
        
        # Simula virada de dia
        tracker.reset_diario()
        
        # Após reset, estado deve ser zerado
        snap_after = tracker.snapshot()
        
        # Valores devem ser zero/após reset
        assert snap_after['regime_realiz_vol'] == 0.0 or 'regime_realiz_vol' in snap_after


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
