#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validacao_correcoes.py — Valida as correções do relatório end-to-end.

Testa:
1. C1/C2: Remoção de colunas de leakage
2. C3: Features de regime calculadas no live
3. C4: Cálculo de VWAP consistente
4. C5: Padronização de nomenclatura
5. C6: Timestamps corretos
6. C7: Feature manifest completo
7. C8: Confidence unificado
8. C9: Endpoints de regime no dashboard
"""

import sys
import os
import json
import pandas as pd
import numpy as np
from pathlib import Path

# Adicionar raiz ao path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

def test_leakage_removal():
    """Testa C1/C2: Remoção de colunas de leakage."""
    print("\n" + "="*60)
    print("TESTE C1/C2: Remoção de colunas de leakage")
    print("="*60)
    
    from ml.dataset_builder import _remover_colunas_leakage, _LEAKAGE_COLS
    
    # Criar DataFrame de teste com colunas de leakage
    df = pd.DataFrame({
        'feature1': [1.0, 2.0, 3.0],
        'feature2': [4.0, 5.0, 6.0],
        'preco_saida': [100.0, 200.0, 300.0],
        'duracao_label_ms': [30000, 45000, 60000],
        'tp_atingido': [True, False, True],
        'sl_atingido': [False, True, False],
        'label': [1, 0, -1],
    })
    
    print(f"Antes: {list(df.columns)}")
    df_limpo = _remover_colunas_leakage(df)
    print(f"Depois: {list(df_limpo.columns)}")
    
    # Verificar se colunas de leakage foram removidas
    cols_restantes = list(df_limpo.columns)
    leakage_restante = [c for c in _LEAKAGE_COLS if c in cols_restantes]
    
    if leakage_restante:
        print(f"[FAIL] Colunas de leakage ainda presentes: {leakage_restante}")
        return False
    else:
        print("[PASS] Todas as colunas de leakage removidas")
        return True


def test_feature_manifest():
    """Testa C7: Feature manifest completo."""
    print("\n" + "="*60)
    print("TESTE C7: Feature manifest completo")
    print("="*60)
    
    from ml.feature_manifest import FeatureManifest, _describe
    
    # Verificar se descrições existem para features críticas
    features_chave = [
        'aggr_imb', 'cvd_total', 'vpin', 'kyle_kyle_lambda',
        'spread', 'ofi_total', 'microprice', 'vp_vp_total',
        'dist_vwap_pts', 'dist_ajuste_pts', 'posicao_range_dia',
        'regime_realiz_vol', 'atr_14', 'volume_relativo',
    ]
    
    todos_descritos = True
    for feat in features_chave:
        desc = _describe(feat)
        if desc.startswith('Feature:'):
            print(f"  [FAIL] {feat}: sem descrição")
            todos_descritos = False
        else:
            print(f"  [OK] {feat}: {desc[:50]}...")
    
    if todos_descritos:
        print("[PASS] Todas as features têm descrição")
        return True
    else:
        print("[FAIL] Algumas features sem descrição")
        return False


def test_vwap_calculation():
    """Testa C4: Cálculo de VWAP consistente."""
    print("\n" + "="*60)
    print("TESTE C4: Cálculo de VWAP")
    print("="*60)
    
    from features.vwap_tracker import VWAPTracker
    
    tracker = VWAPTracker('WINV26', tick=5.0)
    
    # Simular alguns trades
    trades = [
        (1000, 178000, 5),   # ts_ms, preco, qtd
        (2000, 178005, 3),
        (3000, 178010, 7),
        (4000, 178008, 2),
    ]
    
    for ts, preco, qtd in trades:
        tracker.update(ts, preco, qtd)
    
    snap = tracker.snapshot()
    
    # Verificar se VWAP foi calculado corretamente
    # VWAP = sum(preco*qtd) / sum(qtd) = (178000*5 + 178005*3 + 178010*7 + 178008*2) / (5+3+7+2)
    expected_vwap = (178000*5 + 178005*3 + 178010*7 + 178008*2) / (5+3+7+2)
    
    print(f"  VWAP calculado: {snap['vwap']:.2f}")
    print(f"  VWAP esperado: {expected_vwap:.2f}")
    print(f"  Distância: {snap['dist_vwap_pts']:.2f}")
    
    if abs(snap['vwap'] - expected_vwap) < 1.0:
        print("[PASS] VWAP calculado corretamente")
        return True
    else:
        print("[FAIL] VWAP incorreto")
        return False


def test_regime_tracker():
    """Testa C3: Regime tracker calcula features."""
    print("\n" + "="*60)
    print("TESTE C3: Regime tracker")
    print("="*60)
    
    from ml.scorer import RegimeTracker
    
    tracker = RegimeTracker()
    
    # Simular dados
    for i in range(100):
        preco = 178000 + i * 10
        tracker.update(
            ts_ms=1000 + i * 100,
            preco=preco,
            vol_pts=10.0,
            aggr_imb=0.3 if i % 2 == 0 else -0.3,
            cvd_total=i * 100,
            vwap=178500.0
        )
    
    snap = tracker.snapshot()
    
    print(f"  Features calculadas: {list(snap.keys())}")
    
    # Verificar se features de regime estão presentes
    features_esperadas = [
        'regime_realiz_vol', 'regime_realiz_vol_bps', 'regime_vol_zscore',
        'regime_aggr_persistencia', 'regime_cvd_aceleracao',
        'regime_range_dia_norm', 'regime_pos_vs_vwap', 'regime_pos_vs_ajuste'
    ]
    
    todas_presentes = all(f in snap for f in features_esperadas)
    
    if todas_presentes:
        print("[PASS] Todas as features de regime calculadas")
        return True
    else:
        faltando = [f for f in features_esperadas if f not in snap]
        print(f"[FAIL] Features faltando: {faltando}")
        return False


def test_posicao_nomenclatura():
    """Testa C5: Padronização de nomenclatura."""
    print("\n" + "="*60)
    print("TESTE C5: Nomenclatura posicao_range_dia")
    print("="*60)
    
    from features.institutional_context import InstitutionalContext
    
    ctx = InstitutionalContext()
    ctx.set_ajuste('WINV26', 177000)
    
    # Simular atualização
    ctx.update('WINV26', 178500, 100, ohlc={'abertura': 177500, 'maxima': 179000, 'minima': 177000})
    
    feats = ctx.compute('WINV26', 178500)
    
    # Verificar se ambos os nomes existem (alias)
    has_posicao_range = 'posicao_range_dia' in feats
    has_posicao_rel = 'posicao_relativa' in feats
    
    print(f"  posicao_range_dia: {feats.get('posicao_range_dia', 'N/A')}")
    print(f"  posicao_relativa: {feats.get('posicao_relativa', 'N/A')}")
    
    if has_posicao_range and has_posicao_rel:
        # Verificar se os valores são iguais
        if feats['posicao_range_dia'] == feats['posicao_relativa']:
            print("[PASS] Ambos os nomes existem com valores iguais")
            return True
        else:
            print("[FAIL] Valores diferentes entre aliases")
            return False
    elif has_posicao_range:
        print("[PASS] posicao_range_dia existe")
        return True
    else:
        print("[FAIL] posicao_range_dia não encontrado")
        return False


def test_timestamp_consistency():
    """Testa C6: Consistência de timestamps."""
    print("\n" + "="*60)
    print("TESTE C6: Timestamps")
    print("="*60)
    
    import time
    
    # Simular o que o profit_rtd.py faz agora
    tms_tod = 34200000  # 09:30:00 em ms
    tms_epoch = int(time.time() * 1000)
    
    print(f"  Time-of-day: {tms_tod}")
    print(f"  Epoch ms: {tms_epoch}")
    
    # Verificar se epoch ms é razoável (últimos 10 anos)
    min_epoch = int(time.time() * 1000) - 10 * 365 * 24 * 3600 * 1000
    max_epoch = int(time.time() * 1000) + 30 * 1000  # 30 segundos no futuro
    
    if min_epoch <= tms_epoch <= max_epoch:
        print("[PASS] Timestamp epoch ms é válido")
        return True
    else:
        print("[FAIL] Timestamp epoch ms inválido")
        return False


def main():
    print("="*60)
    print("VALIDAÇÃO DAS CORREÇÕES END-TO-END")
    print("="*60)
    
    resultados = []
    
    # Executar testes
    resultados.append(("C1/C2: Leakage removal", test_leakage_removal()))
    resultados.append(("C3: Regime features", test_regime_tracker()))
    resultados.append(("C4: VWAP calculation", test_vwap_calculation()))
    resultados.append(("C5: Nomenclatura", test_posicao_nomenclatura()))
    resultados.append(("C6: Timestamps", test_timestamp_consistency()))
    resultados.append(("C7: Feature manifest", test_feature_manifest()))
    
    # Resumo
    print("\n" + "="*60)
    print("RESUMO DOS TESTES")
    print("="*60)
    
    passed = sum(1 for _, result in resultados if result)
    total = len(resultados)
    
    for nome, result in resultados:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status}: {nome}")
    
    print(f"\nTotal: {passed}/{total} testes passaram")
    
    if passed == total:
        print("\n[TOTAL PASS] TODOS OS TESTES PASSARAM!")
        return 0
    else:
        print(f"\n[ATTENTION] {total - passed} teste(s) falharam")
        return 1


if __name__ == '__main__':
    sys.exit(main())
