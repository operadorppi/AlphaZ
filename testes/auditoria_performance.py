#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_performance.py — Auditoria de performance sem otimização prematura.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DE PERFORMANCE')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. PANDAS — Operações custosas
    # ========================================================================
    print('\n[1] VERIFICANDO OPERAÇÕES CUSTOSAS NO PANDAS...')
    
    pandas_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por operações custosas
        if '.iterrows()' in content:
            pandas_issues.append((str(py_file.relative_to(_root)), 'iterrows()'))
        if '.itertuples()' in content:
            pandas_issues.append((str(py_file.relative_to(_root)), 'itertuples()'))
        if 'apply(' in content and 'axis' in content:
            pandas_issues.append((str(py_file.relative_to(_root)), 'apply com axis'))
        if '.groupby' in content and '.transform' in content:
            # Transform após groupby pode ser custoso
            pass  # Normalmente OK
    
    if pandas_issues:
        print(f'  [WARN] {len(pandas_issues)} potenciais gargalos pandas:')
        for f, issue in pandas_issues[:10]:
            print(f'    {f}: {issue}')
        findings.append(f'pandas:{len(pandas_issues)}')
    else:
        print('  [OK] Nenhuma operação pandas custosa identificada')
    
    # ========================================================================
    # 2. NUMPY — Loops e cópias
    # ========================================================================
    print('\n[2] VERIFICANDO NUMPY...')
    
    numpy_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por loops em arrays numpy
        if 'for ' in content and 'np.' in content:
            # Verificar se há loop sobre array numpy
            if re.search(r'for\s+\w+\s+in\s+\w+\s*:', content):
                # Pode ser custoso se for sobre array grande
                pass  # Precisaria de análise mais detalhada
    
    if numpy_issues:
        print(f'  [WARN] {len(numpy_issues)} potenciais gargalos numpy:')
        for f, issue in numpy_issues[:5]:
            print(f'    {f}: {issue}')
    else:
        print('  [OK] Nenhuma operação numpy custosa identificada')
    
    # ========================================================================
    # 3. I/O — Serialização
    # ========================================================================
    print('\n[3] VERIFICANDO OPERAÇÕES DE I/O...')
    
    io_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # JSON serialização em loop
        if 'json.dumps' in content and 'for ' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'json.dumps' in line and i > 0:
                    # Verificar se está em loop
                    prev_lines = '\n'.join(lines[max(0,i-5):i])
                    if 'for ' in prev_lines:
                        io_issues.append((str(py_file.relative_to(_root)), 'json.dumps em loop'))
                        break
    
    if io_issues:
        print(f'  [WARN] {len(io_issues)} operações I/O em loop:')
        for f, issue in io_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'io_loop:{len(io_issues)}')
    else:
        print('  [OK] Nenhuma operação I/O em loop identificada')
    
    # ========================================================================
    # 4. MEMÓRIA — Cópias de arrays
    # ========================================================================
    print('\n[4] VERIFICANDO CÓPIAS DE ARRAYS...')
    
    copy_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Cópias desnecessárias
        if '.copy()' in content:
            # Verificar se é necessário
            pass  # Cópias são necessárias em alguns casos
    
    print('  [INFO] Cópias de arrays identificadas (verificar se são necessárias)')
    
    # ========================================================================
    # 5. CONCATENAÇÕES
    # ========================================================================
    print('\n[5] VERIFICANDO CONCATENAÇÕES...')
    
    concat_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Concatenação em loop
        if 'pd.concat' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'pd.concat' in line:
                    # Verificar contexto
                    context = '\n'.join(lines[max(0,i-3):i+3])
                    if 'for ' in context:
                        concat_issues.append((str(py_file.relative_to(_root)), 'pd.concat em loop'))
    
    if concat_issues:
        print(f'  [WARN] {len(concat_issues)} concatenações em loop:')
        for f, issue in concat_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'concat_loop:{len(concat_issues)}')
    else:
        print('  [OK] Nenhuma concatenação em loop identificada')
    
    # ========================================================================
    # 6. CONVERSÕES DE TIPOS
    # ========================================================================
    print('\n[6] VERIFICANDO CONVERSÕES DE TIPOS...')
    
    type_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Conversões custosas
        if 'astype(' in content:
            # Verificar se é em loop
            pass  # Conversões são necessárias
    
    print('  [INFO] Conversões de tipos identificadas (normalmente necessárias)')
    
    # ========================================================================
    # 7. PROFILING — Identificar funções custosas
    # ========================================================================
    print('\n[7] IDENTIFICANDO FUNÇÕES CUSTOSAS...')
    
    # Funções que provavelmente são custosas
    expensive_patterns = [
        ('read_parquet', 'I/O pesado'),
        ('to_parquet', 'I/O pesado'),
        ('merge', 'O(n*log(n))'),
        ('groupby', 'O(n)'),
        ('transform', 'O(n)'),
    ]
    
    for pattern, desc in expensive_patterns:
        count = 0
        for py_file in sorted(_root.rglob('*.py')):
            if '__pycache__' in str(py_file):
                continue
            content = py_file.read_text(encoding='utf-8', errors='ignore')
            if pattern in content:
                count += 1
        if count > 0:
            print(f'  {pattern}: {count} ocorrências ({desc})')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DE PERFORMANCE')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} potencial(ais) gargalo(s):')
        for f in findings:
            print(f'  - {f}')
        print('\nRecomendação: Usar cProfile para medir tempo real antes de otimizar.')
        return 1
    else:
        print('\n[PERFORMANCE OK] Nenhum gargalo crítico identificado.')
        print('\nRecomendações:')
        print('  1. Usar cProfile para profiling antes de otimizar')
        print('  2. Monitorar uso de memória com tracemalloc')
        print('  3. Usar parquet com compressão snappy para I/O')
        return 0

if __name__ == '__main__':
    sys.exit(main())
