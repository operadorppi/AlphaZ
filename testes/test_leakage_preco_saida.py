#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_leakage_preco_saida.py — Verifica se preco_saida não vaza para o dataset de treino.
"""
import sys
import os
import pytest
import pandas as pd
import numpy as np
from pathlib import Path

# Adiciona o root ao path
_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestLeakagePrecoSaida:
    """Testes de leakage de preco_saida."""
    
    def test_preco_saida_nao_esta_em_colunas_validas(self):
        """preco_saida não deve estar na lista de colunas válidas."""
        from ml.retreinar_lgbm_limpo import colunas_validas, LEAKAGE_FEATURES
        
        # Verifica se preco_saida está na lista de leakage
        assert 'preco_saida' in LEAKAGE_FEATURES, "preco_saida deve estar em LEAKAGE_FEATURES"
        
        # Cria um DataFrame fake com preco_saida
        df = pd.DataFrame({
            'feature1': [1, 2, 3],
            'preco_saida': [100, 200, 300],
            'label': [1, 0, -1],
        })
        
        # Colunas válidas não devem incluir preco_saida
        valid_cols = colunas_validas(df)
        assert 'preco_saida' not in valid_cols, "preco_saida não deve estar em colunas válidas"
    
    def test_leakage_features_inclui_preco_saida(self):
        """LEAKAGE_FEATURES deve incluir preco_saida."""
        from ml.retreinar_lgbm_limpo import LEAKAGE_FEATURES
        
        assert 'preco_saida' in LEAKAGE_FEATURES
    
    def test_proibidas_inclui_preco_saida(self):
        """PROIBIDAS deve incluir preco_saida."""
        from ml.retreinar_lgbm_limpo import PROIBIDAS
        
        assert 'preco_saida' in PROIBIDAS


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
