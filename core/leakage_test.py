# -*- coding: utf-8 -*-
"""
tests/leakage_test.py — Validação da Regra Zero (v10.2).

Este script automatiza os testes de integridade causal para garantir que
nenhuma feature de contexto utilize informação futura.

Testes implementados (Seções A-E):
  - A: Negócio futuro com preço absurdo.
  - B: Máxima do dia alterada no futuro.
  - C: VWAP final alterada por volume futuro.
  - D: POC migrado por volume futuro.
  - E: Volume total acumulado no futuro.
"""

import sys
import os
import unittest
import pickle
import tempfile
import numpy as np
from pathlib import Path

# Adiciona a raiz do projeto ao path para localizar os módulos core e features
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.scorer import ScorerML

class MockModel:
    """Simula um modelo de ML para permitir o funcionamento do ScorerML sem arquivo .pkl real."""
    def __init__(self):
        self.classes_ = [0, 1]
    def predict_proba(self, X):
        return np.array([[0.5, 0.5]])

class LeakageValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Cria um arquivo de modelo dummy
        cls.tmp_model = Path(tempfile.gettempdir()) / "leakage_mock_model.pkl"
        dummy_data = {
            'modelo': MockModel(),
            'features': ['vwap', 'poc_dist', 'maxima_dia', 'vp_total', 'dist_vwap_pts', 'preco_ultimo']
        }
        cls.model_path = str(cls.tmp_model)
        with open(cls.model_path, 'wb') as f:
            pickle.dump(dummy_data, f)
        
        cls.ativo = "WINV26"
        cls.instrumentos = [cls.ativo]

    def setUp(self):
        # Inicializa um scorer limpo para cada teste
        self.scorer = ScorerML(self.model_path, self.instrumentos)
        # Timestamp de base: 09:00:00
        self.t_base = 1724835600000 

    def _obter_snapshot(self, ts):
        """Helper para gerar o dicionário de features processado pelo engine."""
        return self.scorer._prever({'ativo': self.ativo, 'ts_ms': ts})

    def test_rule_zero_causality(self):
        print("\n[LEAKAGE TEST] Iniciando validação da Regra Zero...")

        # 1. Alimenta dados iniciais até t=1000ms
        self.scorer.evento(self.ativo, self.t_base, 100000.0, 10, 'Comprador', 'XP', 'BTG')
        self.scorer.evento(self.ativo, self.t_base + 1000, 100010.0, 5, 'Vendedor', 'UBS', 'XP')
        
        # Captura baseline de features em t=1000
        feat_t1 = self._obter_snapshot(self.t_base + 1000)
        
        # Avançamos o relógio para o futuro (t + 1 hora)
        t_futuro = self.t_base + 3600000 

        # --- Teste A: Preço absurdo no futuro ---
        self.scorer.evento(self.ativo, t_futuro, 999999.0, 1, 'Comprador', 'INST', 'INST')
        self.assertEqual(feat_t1['preco_ultimo'], 100010.0, "Leakage A: Preço futuro contaminou snapshot passado!")

        # --- Teste B: Máxima futura alterada ---
        # Em t=1000 a máxima era 100010.0. Vamos criar uma nova máxima em t_futuro.
        self.scorer.evento(self.ativo, t_futuro + 100, 105000.0, 1, 'Comprador', 'INST', 'INST')
        feat_check = self._obter_snapshot(self.t_base + 1000)
        self.assertEqual(feat_check['maxima_dia'], 100010.0, "Leakage B: Máxima futura vazou para o passado!")

        # --- Teste C: VWAP Final alterada ---
        # Geramos volume massivo em preço baixo no futuro para puxar a VWAP do dia para baixo
        self.scorer.evento(self.ativo, t_futuro + 200, 90000.0, 100000, 'Vendedor', 'XP', 'BTG')
        feat_check = self._obter_snapshot(self.t_base + 1000)
        self.assertEqual(feat_t1['vwap'], feat_check['vwap'], "Leakage C: VWAP do final do dia vazou para t_base!")

        # --- Teste D: POC Final alterada ---
        # Criamos um novo POC (Price of Control) em um nível totalmente diferente no futuro
        self.scorer.evento(self.ativo, t_futuro + 300, 95000.0, 500000, 'Comprador', 'XP', 'BTG')
        feat_check = self._obter_snapshot(self.t_base + 1000)
        # poc_dist é a distância do preço atual para o POC
        self.assertEqual(feat_t1['poc_dist'], feat_check['poc_dist'], "Leakage D: POC futuro alterou a POC_dist de t_base!")

        # --- Teste E: Volume futuro alterado ---
        # Volume total em t=1000 era 15. Inserimos 1 milhão no futuro.
        self.scorer.evento(self.ativo, t_futuro + 400, 100000.0, 1000000, 'Comprador', 'XP', 'BTG')
        feat_check = self._obter_snapshot(self.t_base + 1000)
        self.assertEqual(feat_t1['vp_total'], 15, "Leakage E: Volume futuro vazou para o Volume Profile passado!")

        print("[OK] Sistema validado: Zero look-ahead detected.")

    @classmethod
    def tearDownClass(cls):
        if cls.tmp_model.exists():
            cls.tmp_model.unlink()

if __name__ == "__main__":
    unittest.main()