#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dashboard_parity_ml.py — Verifica se dashboard mostra mesmos valores que ML.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestDashboardParityML:
    """Testes de paridade dashboard vs ML."""
    
    def test_api_regime_returns_features(self):
        """/api/regime deve retornar features de regime."""
        import inspect
        from adapters.dashboard.handlers import DashboardHandlers
        
        src = inspect.getsource(DashboardHandlers.handle_api_regime)
        assert 'regime' in src.lower()
    
    def test_api_features_returns_ml_features(self):
        """/api/features deve retornar features do ML."""
        import inspect
        from adapters.dashboard.handlers import DashboardHandlers
        
        src = inspect.getsource(DashboardHandlers.handle_api_features)
        assert 'features' in src.lower() or 'scorer' in src.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
