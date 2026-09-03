#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug da divergencia batch vs live."""

import sys
from pathlib import Path

FREEBUFF_PATH = Path(r"C:\freebuff")
sys.path.insert(0, str(FREEBUFF_PATH))

import random
from tests.test_feature_parity_batch_live import generate_dataset
from tests.test_feature_parity import run_batch_pipeline, run_live_pipeline

trades = generate_dataset(seed=42)
books = []  # books vazio (não gerado pelo dataset)
batch_results = run_batch_pipeline(trades, books)
live_results = run_live_pipeline(trades, books)

print("\n" + "="*70)
print("DEBUG: DIVERGENCIA BATCH vs LIVE em t=1001")
print("="*70)

# Trades by timestamp
print("\nTrades nos primeiros 2 segundos:")
for i, t in enumerate(trades[:10]):
    print(f"  [{i}] ts_ms={t['ts_ms']}  sec={t['ts_ms']//1000}  preco={t['preco']:.2f}  qtd={t['qtd']}  agr={t['agressor']}")

# Batch results
print("\nBatch results (FeatureEngine):")
for ts in sorted(batch_results.keys())[:5]:
    feat = batch_results[ts]
    print(f"  t={ts}: n={feat.get('n')}, vol_compr={feat.get('vol_compr')}, vol_vend={feat.get('vol_vend')}, aggr_imb={feat.get('aggr_imb'):.4f}, cvd_total={feat.get('cvd_total')}")

# Live results
print("\nLive results (GeradorJanelas):")
for ts in sorted(live_results.keys())[:5]:
    snap = live_results[ts]
    print(f"  t={ts}: vol_compra={snap.get('vol_compra')}, vol_venda={snap.get('vol_venda')}, aggr_imb={snap.get('aggr_imb'):.4f}, cvd_total={snap.get('cvd_total')}")

# Comparacao direta em t=1001
print("\n" + "-"*70)
print("COMPARACAO DIRETA EM t=1001:")
print("-"*70)

ts = 1001
batch_feat = batch_results.get(ts, {})
live_snap = live_results.get(ts, {})

print(f"\nBatch:")
for k in ['n', 'vol_compr', 'vol_vend', 'vol_total', 'aggr_imb', 'cvd_total', 'delta_preco']:
    print(f"  {k:20s} = {batch_feat.get(k)}")

print(f"\nLive:")
for k in ['n', 'vol_compra', 'vol_venda', 'vol_total', 'aggr_imb', 'cvd_total', 'delta_preco_janela']:
    print(f"  {k:20s} = {live_snap.get(k)}")

print("\n" + "="*70)
print("RAIZ DA DIVERGENCIA:")
print("="*70)
print("""
1. BATCH (FeatureEngine.processar_lote):
   - Processa negócios UM POR UM, agrupando por segundo (seg = ts // 1000)
   - Em t=1001 (seg=1001), so tem 1 negocio (o primeiro trade do dataset)
   - vol_compr=1, vol_vend=0 -> aggr_imb=1.0, cvd_total=0

2. LIVE (GeradorJanelas):
   - Acumula negocios em janela deslizante de 1000ms
   - Em t=1001, tem 62 negocios na janela (todos os trades com ts_ms entre 1000000 e 1001999)
   - vol_compra=30, vol_venda=32 -> aggr_imb=-0.032, cvd_total=-2.0

PROBLEMA IDENTIFICADO:
   - BATCH agrupa por segundo (timestamp // 1000)
   - LIVE agrupa por janela deslizante de 1000ms
   - Sao modelos diferentes de aggregacao!
""")
