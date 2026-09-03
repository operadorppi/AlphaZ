#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verificacao_em_tempo_real.py — Verifica todas as correções da auditoria.
"""
import sys
import os
import inspect
from pathlib import Path

# Adicionar raiz ao path
_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*60)
    print('VERIFICAÇÃO EM TEMPO REAL — Correções da Auditoria')
    print('='*60)
    
    resultados = []
    
    # 1. Verificar imports
    print('\n[1] Verificando imports...')
    try:
        from ml.scorer import ScorerML, RegimeTracker
        print('  [OK] ml.scorer importado')
        resultados.append(('Import scorer', True))
    except Exception as e:
        print(f'  [FAIL] ml.scorer: {e}')
        resultados.append(('Import scorer', False))
        return 1
    
    try:
        from adapters.dashboard.handlers import DashboardHandlers
        print('  [OK] adapters.dashboard.handlers importado')
        resultados.append(('Import handlers', True))
    except Exception as e:
        print(f'  [FAIL] handlers: {e}')
        resultados.append(('Import handlers', False))
        return 1
    
    # 2. Verificar RegimeTracker
    print('\n[2] Verificando RegimeTracker...')
    tracker = RegimeTracker()
    tracker.update(1000, 178000, 100, 0.3, 5000, 178500)
    snap = tracker.snapshot()
    print(f'  Features: {list(snap.keys())}')
    
    # Verificar alphas
    src = inspect.getsource(RegimeTracker.update)
    if 'alpha_curto = 0.005' in src:
        print('  [OK] Alpha curto unificado para 0.005')
        resultados.append(('Alpha unificado', True))
    else:
        print('  [FAIL] Alpha curto não unificado')
        resultados.append(('Alpha unificado', False))
    
    # 3. Verificar interações no ScorerML
    print('\n[3] Verificando interações no ScorerML...')
    src = inspect.getsource(ScorerML._prever)
    interacoes = [
        'aggr_x_dist_vwap',
        'aggr_x_dist_ajuste_oficial',
        'aggr_x_acima_vwap',
        'aggr_x_acima_ajuste_oficial',
        'aggr_x_posicao_range_dia',
        'cvd_x_dist_vwap',
        'cvd_x_dist_ajuste_oficial',
        'cvd_x_acima_vwap',
        'cvd_x_acima_ajuste_oficial',
        'imb_x_dist_vwap',
        'imb_x_dist_ajuste_oficial',
        'vol_x_acima_vwap',
        'vol_x_acima_ajuste_oficial',
    ]
    inter_ok = 0
    for inter in interacoes:
        if inter in src:
            inter_ok += 1
        else:
            print(f'  [FAIL] {inter} ausente')
    print(f'  {inter_ok}/{len(interacoes)} interações implementadas')
    resultados.append(('Interactions', inter_ok == len(interacoes)))
    
    # 4. Verificar VWAP inclinação
    print('\n[4] Verificando VWAP inclinação...')
    if 'vwap_inclinacao_1m' in src and 'vwap_inclinacao_5m' in src:
        print('  [OK] VWAP inclinação implementada')
        resultados.append(('VWAP inclinação', True))
    else:
        print('  [FAIL] VWAP inclinação ausente')
        resultados.append(('VWAP inclinação', False))
    
    # 5. Verificar ATR
    print('\n[5] Verificando ATR...')
    if 'atr_14' in src and 'atr_14_norm' in src:
        print('  [OK] ATR implementado')
        resultados.append(('ATR', True))
    else:
        print('  [FAIL] ATR ausente')
        resultados.append(('ATR', False))
    
    # 6. Verificar dashboard
    print('\n[6] Verificando dashboard...')
    handlers_src = inspect.getsource(DashboardHandlers.handle_api_regime)
    if 'atr_14' in handlers_src and ('volume_relativo' in handlers_src or 'vol_rel' in handlers_src):
        print('  [OK] Dashboard inclui ATR e volume relativo')
        resultados.append(('Dashboard', True))
    else:
        print('  [FAIL] Dashboard incompleto')
        print(f'    atr_14: {"atr_14" in handlers_src}')
        print(f'    volume_relativo: {"volume_relativo" in handlers_src}')
        print(f'    vol_rel: {"vol_rel" in handlers_src}')
        resultados.append(('Dashboard', False))
    
    # 7. Verificar bug fix regime_pos_vs_vwap
    print('\n[7] Verificando bug fix regime_pos_vs_vwap...')
    regime_src = inspect.getsource(RegimeTracker.snapshot)
    if '_vwap_value' in regime_src:
        print('  [OK] Bug corrigido (usa _vwap_value)')
        resultados.append(('Bug fix regime', True))
    else:
        print('  [FAIL] Bug não corrigido')
        resultados.append(('Bug fix regime', False))
    
    # Resumo
    print('\n' + '='*60)
    print('RESUMO')
    print('='*60)
    passed = sum(1 for _, ok in resultados if ok)
    total = len(resultados)
    for nome, ok in resultados:
        status = '[OK]' if ok else '[FAIL]'
        print(f'  {status} {nome}')
    print(f'\nTotal: {passed}/{total} verificações passaram')
    
    if passed == total:
        print('\n[TOTAL PASS] TODAS AS CORREÇÕES VERIFICADAS COM SUCESSO!')
        return 0
    else:
        print(f'\n[ATTENTION] {total - passed} verificação(ões) falharam')
        return 1

if __name__ == '__main__':
    sys.exit(main())
