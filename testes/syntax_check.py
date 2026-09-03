#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
syntax_check.py — Verifica sintaxe de todos os arquivos Python.
"""
from pathlib import Path
import sys

def main():
    errors = []
    ok_count = 0
    skipped = 0
    
    for py_file in sorted(Path('.').rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            skipped += 1
            continue
        try:
            # Usar optimize=0 para evitar escrever .pyc
            compile(py_file.read_text(encoding='utf-8'), str(py_file), 'exec')
            ok_count += 1
        except SyntaxError as e:
            errors.append((str(py_file), f"SyntaxError: {e.msg} (line {e.lineno})"))
        except Exception as e:
            # Ignorar erros de permissão (arquivos .pyc travados)
            if 'PermissionError' not in str(type(e)):
                errors.append((str(py_file), str(e)))
    
    print(f"Check completo: {ok_count} arquivos OK, {skipped} pulados, {len(errors)} erros")
    if errors:
        print("\nErros encontrados:")
        for f, e in errors:
            print(f"  {f}")
            print(f"    {e}")
        return 1
    else:
        print("Nenhum erro de sintaxe encontrado.")
        return 0

if __name__ == '__main__':
    sys.exit(main())
