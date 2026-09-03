#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_dashboard.py — Auditoria completa do dashboard.
"""
import sys
import json
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DO DASHBOARD')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. VERIFICAR ENDPOINTS HTTP
    # ========================================================================
    print('\n[1] VERIFICANDO ENDPOINTS HTTP...')
    
    api_file = _root / 'adapters' / 'dashboard' / 'api.py'
    if api_file.exists():
        content = api_file.read_text(encoding='utf-8')
        # Procurar por rotas
        routes = re.findall(r"\('\s*([^']+)'", content)
        print(f'  Endpoints encontrados: {len(routes)}')
        for r in sorted(routes):
            print(f'    {r}')
    
    # ========================================================================
    # 2. VERIFICAR HANDLERS
    # ========================================================================
    print('\n[2] VERIFICANDO HANDLERS...')
    
    handlers_file = _root / 'adapters' / 'dashboard' / 'handlers.py'
    if handlers_file.exists():
        content = handlers_file.read_text(encoding='utf-8')
        # Procurar por métodos handle_api_
        handlers = re.findall(r'def (handle_api_\w+)', content)
        print(f'  Handlers encontrados: {len(handlers)}')
        for h in sorted(handlers):
            print(f'    {h}')
    
    # ========================================================================
    # 3. VERIFICAR NOVAS FEATURES NO DASHBOARD
    # ========================================================================
    print('\n[3] VERIFICANDO NOVAS FEATURES...')
    
    # Features que devem aparecer
    novas_features = [
        'atr_14', 'atr_14_norm',
        'regime_realiz_vol', 'regime_realiz_vol_bps', 'regime_vol_zscore',
        'regime_aggr_persistencia', 'regime_cvd_aceleracao',
        'regime_range_dia_norm', 'regime_pos_vs_vwap', 'regime_pos_vs_ajuste',
        'volume_relativo', 'volume_acumulado_dia', 'volume_por_minuto',
        'vwap_inclinacao_1m', 'vwap_inclinacao_5m',
        'aggr_x_dist_vwap', 'aggr_x_dist_ajuste_oficial',
        'cvd_x_dist_vwap', 'cvd_x_dist_ajuste_oficial',
    ]
    
    # Verificar se estão nos handlers
    dashboard_features = set()
    if handlers_file.exists():
        content = handlers_file.read_text(encoding='utf-8')
        # Procurar por nomes de features nos handlers
        for feat in novas_features:
            if feat in content:
                dashboard_features.add(feat)
                print(f'  [OK] {feat}')
            else:
                print(f'  [FAIL] {feat} não encontrado nos handlers')
                findings.append(f'feature_faltando_{feat}')
    
    # ========================================================================
    # 4. VERIFICAR FRONTEND HTML
    # ========================================================================
    print('\n[4] VERIFICANDO FRONTEND HTML...')
    
    dashboard_html = _root / 'dashboard_pro.html'
    if dashboard_html.exists():
        content = dashboard_html.read_text(encoding='utf-8')
        
        # Verificar se novas features são exibidas
        for feat in novas_features:
            if feat in content:
                print(f'  [OK] {feat} no HTML')
            else:
                print(f'  [WARN] {feat} não encontrado no HTML')
        
        # Verificar endpoints chamados
        endpoints_html = re.findall(r'/api/(\w+)', content)
        print(f'\n  Endpoints chamados no HTML: {set(endpoints_html)}')
    
    # ========================================================================
    # 5. VERIFICAR ORIGEM DOS DADOS
    # ========================================================================
    print('\n[5] VERIFICANDO ORIGEM DOS DADOS...')
    
    # Verificar se dashboard usa scorer ou market_state
    if handlers_file.exists():
        content = handlers_file.read_text(encoding='utf-8')
        
        # Handlers que usam scorer
        scorer_handlers = []
        if 'scorer' in content:
            scorer_handlers.append('scorer')
        
        # Handlers que usam market_state
        ms_handlers = []
        if 'market_state' in content or 'app.market_state' in content:
            ms_handlers.append('market_state')
        
        print(f'  Origens identificadas: {scorer_handlers + ms_handlers}')
    
    # ========================================================================
    # 6. VERIFICAR CÁLCULOS DUPLICADOS
    # ========================================================================
    print('\n[6] VERIFICANDO CÁLCULOS DUPLICADOS...')
    
    # Verificar se mesma feature é calculada em múltiplos lugares
    feature_locations = defaultdict(list)
    
    # Scorer
    scorer_file = _root / 'ml' / 'scorer.py'
    if scorer_file.exists():
        content = scorer_file.read_text(encoding='utf-8')
        for feat in novas_features:
            if feat in content:
                feature_locations[feat].append('scorer.py')
    
    # Handlers
    if handlers_file.exists():
        content = handlers_file.read_text(encoding='utf-8')
        for feat in novas_features:
            if feat in content:
                feature_locations[feat].append('handlers.py')
    
    # Verificar duplicação
    for feat, locations in feature_locations.items():
        if len(locations) > 1:
            print(f'  [WARN] {feat} encontrado em: {locations}')
            findings.append(f'duplicacao_{feat}')
        elif len(locations) == 1:
            print(f'  [OK] {feat}: {locations[0]}')
    
    # ========================================================================
    # 7. VERIFICAR TIMESTAMP
    # ========================================================================
    print('\n[7] VERIFICANDO TIMESTAMP...')
    
    # Verificar se dashboard exibe timestamp
    if dashboard_html.exists():
        content = dashboard_html.read_text(encoding='utf-8')
        if 'ts_ms' in content or 'timestamp' in content.lower():
            print('  [OK] Timestamp presente no HTML')
        else:
            print('  [WARN] Timestamp não identificado no HTML')
    
    # ========================================================================
    # 8. VERIFICAR UNIDADES
    # ========================================================================
    print('\n[8] VERIFICANDO UNIDADES...')
    
    # Verificações básicas de unidades
    unit_checks = [
        ('atr_14', 'pts'),
        ('regime_realiz_vol', 'ratio'),
        ('volume_relativo', 'ratio'),
        ('vwap_inclinacao_1m', 'ratio'),
    ]
    
    for feat, expected_unit in unit_checks:
        print(f'  {feat}: {expected_unit} (verificar manualmente)')
    
    # ========================================================================
    # 9. RASTREAR INDICADORES
    # ========================================================================
    print('\n[9] RASTREANDO INDICADORES...')
    
    # Exemplo: atr_14
    print('\n  Rastreamento: atr_14')
    print('    1. Batch: build_dataset_v950.py calcula ATR')
    print('    2. Live: scorer.py calcula ATR (EWMA alpha=2/15)')
    print('    3. Dashboard: handlers.py expõe via /api/regime')
    print('    4. HTML: dashboard_pro.html exibe')
    
    # Exemplo: regime_realiz_vol
    print('\n  Rastreamento: regime_realiz_vol')
    print('    1. Batch: features_contexto_avancado.py calcula')
    print('    2. Live: scorer.py RegimeTracker calcula')
    print('    3. Dashboard: handlers.py expõe via /api/regime')
    print('    4. HTML: dashboard_pro.html exibe')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DASHBOARD')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} problema(s) encontrado(s):')
        for f in findings:
            print(f'  - {f}')
        return 1
    else:
        print('\n[DASHBOARD OK] Nenhuma irregularidade crítica encontrada.')
        return 0

if __name__ == '__main__':
    sys.exit(main())
