#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_testes.py — Auditoria da cobertura de testes.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DE TESTES')
    print('='*70)
    
    # ========================================================================
    # 1. CONTAR TESTES EXISTENTES
    # ========================================================================
    print('\n[1] CONTANDO TESTES EXISTENTES...')
    
    test_files = list(_root.rglob('test*.py')) + list(_root.rglob('*_test.py'))
    test_files = [f for f in test_files if '__pycache__' not in str(f) and '.agnes' not in str(f)]
    
    total_tests = 0
    test_functions = []
    
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        # Contar funções de teste
        tests = re.findall(r'def test_\w+', content)
        total_tests += len(tests)
        test_functions.extend([(test_file.name, t) for t in tests])
    
    print(f'  Arquivos de teste: {len(test_files)}')
    print(f'  Funções de teste: {total_tests}')
    
    # ========================================================================
    # 2. VERIFICAR COBERTURA DE LEAKAGE
    # ========================================================================
    print('\n[2] VERIFICANDO COBERTURA DE LEAKAGE...')
    
    leakage_tests = []
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        if 'leakage' in content.lower() or 'preco_saida' in content:
            leakage_tests.append(test_file.name)
    
    print(f'  Testes de leakage: {len(leakage_tests)}')
    for t in leakage_tests:
        print(f'    - {t}')
    
    # ========================================================================
    # 3. VERIFICAR COBERTURA DE EDGE CASES
    # ========================================================================
    print('\n[3] VERIFICANDO COBERTURA DE EDGE CASES...')
    
    edge_cases = {
        'timestamp_zero': False,
        'timestamp_negativo': False,
        'preco_zero': False,
        'preco_negativo': False,
        'volume_zero': False,
        'book_vazio': False,
        'fila_saturada': False,
        'memoria_insuficiente': False,
        'network_timeout': False,
        'disk_full': False,
    }
    
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        
        if 'timestamp' in content.lower() and 'zero' in content.lower():
            edge_cases['timestamp_zero'] = True
        if 'volume' in content.lower() and 'zero' in content.lower():
            edge_cases['volume_zero'] = True
        if 'book' in content.lower() and 'vazio' in content.lower():
            edge_cases['book_vazio'] = True
        if 'fila' in content.lower() and 'satur' in content.lower():
            edge_cases['fila_saturada'] = True
    
    print('  Edge cases cobertos:')
    for case, covered in edge_cases.items():
        status = '[OK]' if covered else '[FAIL]'
        print(f'    {status} {case}')
    
    # ========================================================================
    # 4. VERIFICAR COBERTURA DE FEATURES
    # ========================================================================
    print('\n[4] VERIFICANDO COBERTURA DE FEATURES...')
    
    # Features críticas que devem ser testadas
    critical_features = [
        'aggr_imb',
        'cvd_total',
        'spread',
        'microprice',
        'vwap',
        'vp_vp_total',
        'kyle_lambda',
        'vpin',
        'atr_14',
        'regime_realiz_vol',
        'volume_relativo',
    ]
    
    tested_features = []
    untested_features = []
    
    for feature in critical_features:
        found = False
        for test_file in sorted(test_files):
            content = test_file.read_text(encoding='utf-8', errors='ignore')
            if feature in content:
                found = True
                break
        if found:
            tested_features.append(feature)
        else:
            untested_features.append(feature)
    
    print(f'  Features testadas: {len(tested_features)}/{len(critical_features)}')
    if untested_features:
        print(f'  Features NÃO testadas:')
        for f in untested_features:
            print(f'    - {f}')
    
    # ========================================================================
    # 5. VERIFICAR COBERTURA DE DASHBOARD
    # ========================================================================
    print('\n[5] VERIFICANDO COBERTURA DE DASHBOARD...')
    
    dashboard_tests = []
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        if 'dashboard' in content.lower() or '/api/' in content:
            dashboard_tests.append(test_file.name)
    
    print(f'  Testes de dashboard: {len(dashboard_tests)}')
    for t in dashboard_tests:
        print(f'    - {t}')
    
    # ========================================================================
    # 6. VERIFICAR COBERTURA DE INTEGRACAO
    # ========================================================================
    print('\n[6] VERIFICANDO COBERTURA DE INTEGRAÇÃO...')
    
    integration_tests = []
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        if 'integracao' in content.lower() or 'end_to_end' in content.lower():
            integration_tests.append(test_file.name)
    
    print(f'  Testes de integração: {len(integration_tests)}')
    for t in integration_tests:
        print(f'    - {t}')
    
    # ========================================================================
    # 7. TESTES QUE PASSAM MAS NÃO VALIDAM
    # ========================================================================
    print('\n[7] IDENTIFICANDO TESTES PROBLEMÁTICOS...')
    
    problematic_tests = []
    for test_file in sorted(test_files):
        content = test_file.read_text(encoding='utf-8', errors='ignore')
        
        # Testes que não têm assert
        if 'def test_' in content and 'assert' not in content:
            problematic_tests.append((test_file.name, 'sem assert'))
        
        # Testes com assert True
        if content.count('assert True') > 0:
            problematic_tests.append((test_file.name, f'{content.count("assert True")} assert True'))
        
        # Testes que passam sempre
        if 'pass' in content and 'assert' not in content:
            problematic_tests.append((test_file.name, 'só tem pass'))
    
    if problematic_tests:
        print(f'  {len(problematic_tests)} testes problemáticos:')
        for t, issue in problematic_tests[:10]:
            print(f'    - {t}: {issue}')
    else:
        print('  Nenhum teste problemático identificado')
    
    # ========================================================================
    # 8. TESTES QUE DEVERIAM EXISTIR
    # ========================================================================
    print('\n[8] TESTES QUE DEVERIAM EXISTIR...')
    
    missing_tests = [
        ('test_leakage_preco_saida_no_dataset', 'Verificar se preco_saida não está no dataset de treino'),
        ('test_leakage_duracao_label_no_dataset', 'Verificar se duracao_label_ms não está no dataset de treino'),
        ('test_vwap_causal_no_lookahead', 'Verificar se VWAP não usa dados futuros'),
        ('test_atr_consistente_batch_live', 'Verificar se ATR é igual no batch e live'),
        ('test_regime_reset_diario', 'Verificar se regime reseta entre dias'),
        ('test_book_split_edge_cases', 'Testar book_split=0, negativo, muito grande'),
        ('test_timestamp_timezone', 'Verificar conversão correta de timezone'),
        ('test_deduplication_trades', 'Verificar se trades duplicados são removidos'),
        ('test_book_timestamp_sync', 'Verificar se book e T&T têm timestamps sincronizados'),
        ('test_feature_parity_batch_live', 'Verificar se features são iguais no batch e live'),
        ('test_dashboard_parity_ml', 'Verificar se dashboard mostra mesmos valores que ML'),
        ('test_queue_no_loss_on_overflow', 'Verificar se fila não perde dados ao satura'),
        ('test_file_rotation_no_data_loss', 'Verificar se rotação de arquivo não perde dados'),
        ('test_contracts_rollover', 'Verificar reset ao mudar de contrato'),
        ('test_session_boundary', 'Verificar comportamento na virada de sessão'),
    ]
    
    for test_name, description in missing_tests:
        print(f'  [FAIL] {test_name}')
        print(f'     {description}')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DE TESTES')
    print('='*70)
    
    print(f'\n  Total de testes: {total_tests}')
    print(f'  Testes de leakage: {len(leakage_tests)}')
    print(f'  Features testadas: {len(tested_features)}/{len(critical_features)}')
    print(f'  Testes de dashboard: {len(dashboard_tests)}')
    print(f'  Testes de integração: {len(integration_tests)}')
    print(f'  Testes problemáticos: {len(problematic_tests)}')
    print(f'  Testes faltantes: {len(missing_tests)}')
    
    print('\nRecomendações:')
    print('  1. Criar testes para features não testadas')
    print('  2. Adicionar testes de integridade temporal')
    print('  3. Criar testes de paridade batch vs live')
    print('  4. Adicionar testes de borda para queue e arquivos')
    print('  5. Remover testes problemáticos (sem assert)')

if __name__ == '__main__':
    main()
