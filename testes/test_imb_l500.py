#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
testes/test_imb_l500.py — Validação do cálculo de imbalance para 500 níveis.
"""
import unittest
import sys
import os
from pathlib import Path

# Adiciona a raiz do projeto ao path para localizar os módulos
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features.book_features import BookLevelFeatures

class TestBookImbalanceL500(unittest.TestCase):
    def setUp(self):
        self.tracker = BookLevelFeatures()

    def test_calculo_imb_l500_equilibrado(self):
        """Valida que o imbalance L500 é 0.0 quando bid e ask têm mesmo volume em 500 níveis."""
        # Simulando 500 níveis no total (250 cada lado ou 500 cada lado dependendo da extração)
        # Para este teste, assumimos que o snapshot já vem com 500 entradas de vol.
        n = 500
        snapshot = {
            'bid_preco': [100000 - i*5 for i in range(n)],
            'bid_vol': [10] * n,   # Volume acumulado: 5000
            'ask_preco': [100005 + i*5 for i in range(n)],
            'ask_vol': [10] * n    # Volume acumulado: 5000
        }
        
        # Imbalance = (SumBid - SumAsk) / (SumBid + SumAsk)
        # (5000 - 5000) / 10000 = 0.0
        res = self.tracker.calcular(snapshot, "WINV26", 1600000000000)
        self.assertIsNotNone(res)
        self.assertIn('imb_L500', res)
        self.assertEqual(res['imb_L500'], 0.0)
        self.assertEqual(res['n_bid_levels'], 500)
        self.assertEqual(res['n_ask_levels'], 500)

    def test_calculo_imb_l500_viesado(self):
        """Valida o cálculo do imbalance L500 com volumes fortemente assimétricos."""
        n = 500
        # Compra com 10 por nível (Total 5000), Venda com 30 por nível (Total 15000)
        # Imbalance = (5000 - 15000) / (5000 + 15000) = -10000 / 20000 = -0.5
        
        snapshot = {
            'bid_preco': [100000 - i*5 for i in range(n)],
            'bid_vol': [10] * n,
            'ask_preco': [100005 + i*5 for i in range(n)],
            'ask_vol': [30] * n
        }
        
        res = self.tracker.calcular(snapshot, "WINV26", 1600000000000)
        self.assertEqual(res['imb_L500'], -0.5)

    def test_calculo_imb_l500_limite_dados(self):
        """Garante que o imb_L500 funciona mesmo se houver menos de 500 níveis disponíveis."""
        snapshot = {
            'bid_preco': [100.0, 95.0],
            'bid_vol': [10, 10],   # Total 20
            'ask_preco': [105.0, 110.0],
            'ask_vol': [30, 30]    # Total 60
        }
        # (20 - 60) / 80 = -0.5
        res = self.tracker.calcular(snapshot, "WINV26", 1600000000000)
        self.assertEqual(res['imb_L500'], -0.5)
        self.assertEqual(res['n_bid_levels'], 2)

if __name__ == '__main__':
    unittest.main()