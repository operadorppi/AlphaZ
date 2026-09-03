#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_leakage_duracao_label.py — Verifica se duracao_label_ms não vaza para o dataset de treino.
"""
import sys
import pytest
import pandas as pd
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestLeakageDuracaoLabel:
    """Testes de leakage de duracao_label_ms."""
    
    def test_duracao_label_nao_esta_em_colunas_validas(self):
        """duracao_label_ms não deve estar na lista de colunas válidas."""
        from ml.retreinar_lgbm_limpo import colunas_validas, LEAKAGE_FEATURES
        
        assert 'duracao_label_ms' in LEAKAGE_FEATURES
        
        df = pd.DataFrame({
            'feature1': [1, 2, 3],
            'duracao_label_ms': [30000, 45000, 60000],
            'label': [1, 0, -1],
        })
        
        valid_cols = colunas_validas(df)
        assert 'duracao_label_ms' not in valid_cols
    
    def test_leakage_features_inclui_duracao_label(self):
        """LEAKAGE_FEATURES deve incluir duracao_label_ms."""
        from ml.retreinar_lgbm_limpo import LEAKAGE_FEATURES
        
        assert 'duracao_label_ms' in LEAKAGE_FEATURES


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
