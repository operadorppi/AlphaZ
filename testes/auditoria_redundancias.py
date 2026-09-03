#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_redundancias.py — Auditoria completa de redundâncias no código.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DE REDUNDÂNCIAS')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. FUNÇÕES DUPLICADAS
    # ========================================================================
    print('\n[1] BUSCANDO FUNÇÕES DUPLICADAS...')
    
    functions = defaultdict(list)
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        # Procurar definições de funções
        for match in re.finditer(r'def\s+(\w+)\s*\(', content):
            func_name = match.group(1)
            functions[func_name].append(str(py_file.relative_to(_root)))
    
    # Encontrar funções com múltiplas implementações
    duplicate_funcs = {k: v for k, v in functions.items() if len(v) > 1 and not k.startswith('_')}
    if duplicate_funcs:
        print(f'  [WARN] {len(duplicate_funcs)} funções com múltiplas implementações:')
        for func, locations in list(duplicate_funcs.items())[:10]:
            print(f'    {func}: {locations}')
            findings.append(f'dup_func:{func}')
    else:
        print('  [OK] Nenhuma função duplicada encontrada')
    
    # ========================================================================
    # 2. FEATURES DUPLICADAS
    # ========================================================================
    print('\n[2] BUSCANDO FEATURES DUPLICADAS...')
    
    # Procurar por nomes de features similares
    feature_names = set()
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        # Procurar assignments de features
        for match in re.finditer(r"['\"]([a-z_]+)['\"]\s*=", content):
            feature_names.add(match.group(1))
        for match in re.finditer(r"row\['([a-z_]+)'\]", content):
            feature_names.add(match.group(1))
    
    # Encontrar features similares (mesmo significado, nomes diferentes)
    similar_features = []
    feature_list = sorted(feature_names)
    for i, f1 in enumerate(feature_list):
        for f2 in feature_list[i+1:]:
            # Verificar similaridade por substring
            if f1 in f2 or f2 in f1:
                # Verificar se são semanticamente similares
                if any(kw in f1 and kw in f2 for kw in ['vwap', 'atr', 'vol', 'regime', 'cross', 'imb', 'cvd']):
                    similar_features.append((f1, f2))
    
    if similar_features:
        print(f'  [INFO] {len(similar_features)} pares de features similares:')
        for f1, f2 in similar_features[:10]:
            print(f'    {f1} <-> {f2}')
    
    # ========================================================================
    # 3. MÓDULOS ANTIGOS
    # ========================================================================
    print('\n[3] BUSCANDO MÓDULOS ANTIGOS...')
    
    # Verificar imports de módulos obsoletos
    old_modules = [
        'motor_web',
        'motor_rt_alphaz',
        'features_lib',  # Shim de compatibilidade
    ]
    
    for module in old_modules:
        # Verificar se ainda é importado
        for py_file in sorted(_root.rglob('*.py')):
            if '__pycache__' in str(py_file):
                continue
            content = py_file.read_text(encoding='utf-8', errors='ignore')
            if f'import {module}' in content or f'from {module}' in content:
                print(f'  [WARN] {module} ainda importado em {py_file.name}')
                findings.append(f'modulo_antigo:{module}')
    
    # ========================================================================
    # 4. CÓDIGO MORTO
    # ========================================================================
    print('\n[4] BUSCANDO CÓDIGO MORTO...')
    
    # Verificar funções não chamadas (análise básica)
    # Esta é uma verificação simplificada
    
    # Procurar por variáveis não usadas (simplificado)
    unused_vars = []
    for py_file in sorted(_root.rglob('*.py'))[:50]:  # Limitar para performance
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        lines = content.split('\n')
        for i, line in enumerate(lines):
            # Procurar assignments de variáveis
            match = re.match(r'\s*(\w+)\s*=', line)
            if match and not line.strip().startswith('#'):
                var_name = match.group(1)
                if var_name in ('self', 'cls', 'True', 'False', 'None'):
                    continue
                # Verificar se a variável é usada no restante do arquivo
                rest_of_file = '\n'.join(lines[i+1:])
                if var_name not in rest_of_file and not var_name.startswith('_'):
                    unused_vars.append((py_file.name, i+1, var_name))
    
    if unused_vars:
        print(f'  [INFO] {len(unused_vars)} variáveis potencialmente não usadas:')
        for fname, line, var in unused_vars[:10]:
            print(f'    {fname}:{line} - {var}')
    
    # ========================================================================
    # 5. SCRIPTS OBSOLETOS
    # ========================================================================
    print('\n[5] BUSCANDO SCRIPTS OBSOLETOS...')
    
    old_scripts = [
        'build_dataset_v940.py',
        'build_dataset_v950.py',
        'labeler.py',  # Substituído por labeler_vectorizado.py
        'retreinar_sem_leak.py',  # Substituído por retreinar_lgbm_limpo.py
    ]
    
    for script in old_scripts:
        script_path = _root / 'ml' / script
        if script_path.exists():
            print(f'  [WARN] Script obsoleto encontrado: {script}')
            findings.append(f'script_obsoleto:{script}')
        else:
            print(f'  [OK] {script} removido')
    
    # ========================================================================
    # 6. PIPELINES PARALELOS
    # ========================================================================
    print('\n[6] BUSCANDO PIPELINES PARALELOS...')
    
    # Verificar se há múltiplos scripts de treino
    train_scripts = list(_root.rglob('*treino*.py')) + list(_root.rglob('*train*.py'))
    train_scripts += list(_root.rglob('*retreinar*.py'))
    train_scripts = sorted(set(train_scripts))
    
    print(f'  Scripts de treinamento encontrados: {len(train_scripts)}')
    for s in train_scripts:
        print(f'    - {s.name}')
    
    # ========================================================================
    # 7. NOMES DIVERGENTES
    # ========================================================================
    print('\n[7] BUSCANDO NOMES DIVERGENTES...')
    
    # Verificar alias de variáveis
    aliases = {
        'posicao_range_dia': 'posicao_relativa',  # Corrigido
        'cross_lag': 'lag_ms',  # Mesmo conceito
    }
    
    for orig, alias in aliases.items():
        print(f'  [INFO] Alias documentado: {orig} <-> {alias}')
    
    # ========================================================================
    # 8. MÉTRICAS DUPLICADAS
    # ========================================================================
    print('\n[8] BUSCANDO MÉTRICAS DUPLICADAS...')
    
    # Verificar cálculos de AUC, ECE, etc.
    metric_implementations = defaultdict(list)
    
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar implementações de métricas
        if 'def calcular_ece' in content or 'def _calcular_ece' in content:
            metric_implementations['ece'].append(py_file.name)
        if 'def auc' in content or 'roc_auc_score' in content:
            metric_implementations['auc'].append(py_file.name)
        if 'def profit_factor' in content or 'profit_factor' in content:
            metric_implementations['profit_factor'].append(py_file.name)
    
    for metric, files in metric_implementations.items():
        if len(files) > 1:
            print(f'  [WARN] Métrica {metric} implementada em {len(files)} lugares:')
            for f in files:
                print(f'    - {f}')
            findings.append(f'metric_dup:{metric}')
        else:
            print(f'  [OK] Métrica {metric}: {files}')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DE REDUNDÂNCIAS')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} redundância(ões) encontrada(s):')
        for f in findings:
            print(f'  - {f}')
        return 1
    else:
        print('\n[REDUNDANCY OK] Nenhuma redundância crítica encontrada.')
        return 0

if __name__ == '__main__':
    sys.exit(main())
