#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/verificar_importancia.py — Auditoria de pesos do modelo.
"""
import sys
import os
import pickle
import pandas as pd
from pathlib import Path

# Adiciona a raiz ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ml.treino_lib import feature_importances

def verificar(modelo_path):
    if not os.path.exists(modelo_path):
        print(f"Erro: Modelo não encontrado em {modelo_path}")
        return

    with open(modelo_path, 'rb') as f:
        blob = pickle.load(f)
    
    imp = feature_importances(blob['modelo'], blob['features'], top_n=100, importance_type='gain')
    
    print(f"\n--- Top 20 Features (GAIN) de {os.path.basename(modelo_path)} ---")
    print(imp.head(20))
    
    l500 = imp[imp.index.str.contains('L500')]
    print("\n--- Features de Profundidade L500 ---")
    print(l500 if not l500.empty else "Nenhuma feature L500 encontrada ou importância é zero.")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "D:\\MarketData\\mimo\\modelo_lgbm_v4_limpo.pkl"
    verificar(path)