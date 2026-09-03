# -*- coding: utf-8 -*-
"""Varredura AST: acha imports que nao resolvem dentro de testes/ e tests/.

Motivacao: pytest so falha na COLETA por imports de modulo (topo do arquivo).
Imports feitos DENTRO de funcoes (lazy) passam pela coleta e so estouram em
runtime — foram assim que `motor_web` e `captura_eventos_ms` passaram
despercebidos. Este script resolve os dois casos.

Uso: python scripts/check_test_imports.py [dir1 dir2 ...]
"""
import ast
import os
import sys
import importlib.util

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in [BASE, os.path.join(BASE, "ml"), os.path.join(BASE, "scripts"),
          os.path.join(BASE, "adapters")]:
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

# Modulos que existem mas sao pesados/efemeros — nao tentar importar de fato
SKIP_IMPORT = {"comtypes", "comtypes.client", "comtypes.gen", "win32com",
               "win32com.client", "pythoncom", "pywintypes", "lightgbm"}


def resolve(name):
    """True se o modulo `name` resolve para algo no filesystem/stdlib/site."""
    top = name.split(".")[0]
    if name in SKIP_IMPORT or top in SKIP_IMPORT:
        return True, "skip"
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError, ModuleNotFoundError):
        return False, "find_spec estourou"
    if spec is None:
        return False, "find_spec -> None"
    # submodulo (ex: adapters.rtd_writer)
    if "." in name:
        try:
            mod = importlib.import_module(name)
            return True, "ok"
        except Exception as e:
            return False, f"import falhou: {type(e).__name__}: {e}"
    return True, "ok"


def scan_file(path):
    """Retorna lista de (linha, modulo, motivo, contexto)."""
    problemas = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except SyntaxError as e:
        return [(0, path, f"SyntaxError: {e.msg}", "")]

    # profundidade: 0 = topo (pego na coleta), >0 = dentro de funcao (invisivel)
    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0

        def _visit_scope(self, node):
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_FunctionDef = _visit_scope
        visit_AsyncFunctionDef = _visit_scope
        visit_ClassDef = _visit_scope

        def visit_Import(self, node):
            for a in node.names:
                ok, motivo = resolve(a.name)
                if not ok:
                    problemas.append((node.lineno, a.name, motivo,
                                      "topo" if self.depth == 0 else "LAZY"))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if node.level and node.level > 0:
                self.generic_visit(node)
                return  # import relativo
            mod = node.module or ""
            ok, motivo = resolve(mod)
            if not ok:
                problemas.append((node.lineno, mod, motivo,
                                  "topo" if self.depth == 0 else "LAZY"))
            self.generic_visit(node)

    V().visit(tree)
    return problemas


def main(dirs):
    total = 0
    for d in dirs:
        alvo = os.path.join(BASE, d)
        if not os.path.isdir(alvo):
            continue
        for fn in sorted(os.listdir(alvo)):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(alvo, fn)
            probs = scan_file(path)
            if probs:
                print(f"\n### {d}/{fn}")
                for linha, mod, motivo, ctx in probs:
                    print(f"   L{linha:<5} [{ctx:<4}] {mod:<28} -> {motivo}")
                    total += 1
    print(f"\n{'=' * 60}")
    if total:
        print(f"{total} import(s) sem resolucao")
    else:
        print("Nenhum import sem resolucao.")
    return total


if __name__ == "__main__":
    args = sys.argv[1:] or ["testes", "tests"]
    sys.exit(0 if main(args) == 0 else 1)
