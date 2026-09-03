#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_check.py — Verifica imports e problemas potenciais.
"""
import sys
import ast
from pathlib import Path
from collections import defaultdict

def check_file(filepath):
    """Verifica imports e problemas em um arquivo Python."""
    issues = []
    
    try:
        source = filepath.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        issues.append(f"SyntaxError: {e.msg} (line {e.lineno})")
        return issues
    except Exception as e:
        issues.append(f"Read error: {e}")
        return issues
    
    # Verificar imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
    
    # Verificar variáveis não utilizadas (simples)
    defined_vars = set()
    used_vars = set()
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_vars.add(target.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_vars.add(node.id)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            defined_vars.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined_vars.add(node.name)
    
    # Variáveis definidas mas não usadas (exceto padrões Python)
    python_builtins = {'self', 'cls', 'e', 'exc', 'ex', 'err', 'logger', 'log'}
    unused = defined_vars - used_vars - python_builtins - {'__name__', '__doc__', '__file__'}
    
    if unused:
        issues.append(f"Variáveis possivelmente não usadas: {', '.join(sorted(unused)[:10])}")
    
    return issues

def main():
    print("="*60)
    print("VERIFICAÇÃO DE IMPORTS E PROBLEMAS POTENCIAIS")
    print("="*60)
    
    all_issues = defaultdict(list)
    
    for py_file in sorted(Path('.').rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        
        issues = check_file(py_file)
        if issues:
            all_issues[str(py_file)] = issues
    
    if all_issues:
        print(f"\n{len(all_issues)} arquivo(s) com problemas potenciais:\n")
        for filepath, issues in sorted(all_issues.items()):
            print(f"{filepath}:")
            for issue in issues:
                print(f"  - {issue}")
    else:
        print("\nNenhum problema potencial encontrado.")
    
    print(f"\nTotal: {len(all_issues)} arquivos com issues")
    return 1 if all_issues else 0

if __name__ == '__main__':
    sys.exit(main())
