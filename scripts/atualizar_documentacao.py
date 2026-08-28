#!/usr/bin/env python3
# NOTA v9.19: O trabalho de documentacao agora e feito a mao (DOCUMENTACAO.md).
# Este script e mantido por referencia historica; NAO use para doc nova.
"""
atualizar_documentacao.py — Gera e atualiza DOCUMENTACAO_TESTES.md automaticamente.
Rode: python atualizar_documentacao.py
"""

import os
import re
import ast
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# ============================================================
#  Configuração
# ============================================================

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "DOCUMENTACAO_TESTES.md"

# Arquivos principais para documentar
SCRIPTS_PRINCIPAIS = {
    "labeler_vectorizado.py": "Labeler",
    "labeler.py": "Labeler Original",
    "walk_forward.py": "Walk-Forward",
    "lightgbm_quick_test.py": "LightGBM Quick Test",
    "lightgbm_tune.py": "LightGBM Tune",
    "treino_lib.py": "Treino Lib",
    "features_lib.py": "Features Lib",
    "dataset_builder.py": "Dataset Builder",
    "test_features.py": "Testes Unitários",
    "motor_rt_alphaz.py": "Motor RT",
    "watchdog.py": "Watchdog",
    "pipeline_diario.py": "Pipeline Diário",
}

# Arquivos de resultado
RESULTADOS = {
    "walk_forward_resultado.json": "Walk-Forward Original",
    "walk_forward_v2.json": "Walk-Forward v2 (corrigido)",
    "walk_forward_v2_lgbm.json": "Walk-Forward LightGBM",
    "lightgbm_tune_results.json": "LightGBM Tuning",
}


# ============================================================
#  Parsing de código
# ============================================================

def extrair_docstring(filepath: Path) -> str:
    """Extrai docstring do módulo."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        return ast.get_docstring(tree) or ""
    except Exception:
        return ""


def extrair_classes(filepath: Path) -> List[Dict]:
    """Extrai classes e seus métodos."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                metodos = []
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        metodos.append({
                            "nome": item.name,
                            "args": [arg.arg for arg in item.args.args if arg.arg != 'self'],
                            "docstring": ast.get_docstring(item) or ""
                        })
                classes.append({
                    "nome": node.name,
                    "docstring": ast.get_docstring(node) or "",
                    "metodos": metodos
                })
        return classes
    except Exception:
        return []


def extrair_funcoes(filepath: Path) -> List[Dict]:
    """Extrai funções de nível superior."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        
        funcoes = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                funcoes.append({
                    "nome": node.name,
                    "args": [arg.arg for arg in node.args.args],
                    "docstring": ast.get_docstring(node) or ""
                })
        return funcoes
    except Exception:
        return []


def extrair_config(filepath: Path) -> Dict:
    """Extrai configurações de parâmetros."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        configs = {}
        
        # Procura por argparse
        if 'add_argument' in content:
            args = re.findall(r"add_argument\('([^']+)'", content)
            configs['argumentos'] = args
        
        # Procura por constantes importantes
        consts = re.findall(r'^([A-Z_]+)\s*=\s*(.+)$', content, re.MULTILINE)
        if consts:
            configs['constantes'] = {c[0]: c[1][:50] for c in consts[:10]}
        
        return configs
    except Exception:
        return {}


# ============================================================
#  Geração de documentação
# ============================================================

def gerar_secao_scripts() -> str:
    """Gera seção sobre scripts principais."""
    linhas = ["## 📁 Scripts Principais\n"]
    linhas.append("| Script | Descrição | Funções | Classes |")
    linhas.append("|--------|-----------|---------|---------|\n")
    
    for arquivo, desc in SCRIPTS_PRINCIPAIS.items():
        filepath = BASE_DIR / arquivo
        if not filepath.exists():
            continue
        
        docstring = extrair_docstring(filepath)
        funcoes = extrair_funcoes(filepath)
        classes = extrair_classes(filepath)
        
        # Primeira linha da docstring
        desc_curta = docstring.split('\n')[0] if docstring else desc
        
        linhas.append(f"| `{arquivo}` | {desc_curta[:60]} | {len(funcoes)} | {len(classes)} |")
    
    return "\n".join(linhas)


def gerar_secao_testes() -> str:
    """Gera seção de testes unitários."""
    filepath = BASE_DIR / "test_features.py"
    if not filepath.exists():
        return "## 🧪 Testes Unitários\n\nArquivo não encontrado."
    
    classes = extrair_classes(filepath)
    
    linhas = ["## 🧪 Testes Unitários\n"]
    linhas.append(f"**Arquivo:** `test_features.py`  ")
    linhas.append(f"**Total de classes:** {len(classes)}\n")
    linhas.append("### Como Rodar\n")
    linhas.append("```bash\npython -m pytest test_features.py -v\n```\n")
    linhas.append("### Cobertura\n")
    linhas.append("| Classe | Métodos | Descrição |")
    linhas.append("|--------|---------|-----------|")
    
    total_testes = 0
    for cls in classes:
        metodos_teste = [m for m in cls['metodos'] if m['nome'].startswith('test_')]
        total_testes += len(metodos_teste)
        doc = cls['docstring'].split('\n')[0] if cls['docstring'] else "Testes"
        linhas.append(f"| `{cls['nome']}` | {len(metodos_teste)} | {doc[:50]} |")
    
    linhas.append(f"\n**Total de testes:** {total_testes}")
    
    return "\n".join(linhas)


def gerar_secao_labeler() -> str:
    """Gera seção sobre o labeler."""
    linhas = ["## 🏷️ Labeler\n"]
    
    # Labeler vectorizado
    filepath = BASE_DIR / "labeler_vectorizado.py"
    if filepath.exists():
        docstring = extrair_docstring(filepath)
        configs = extrair_config(filepath)
        funcoes = extrair_funcoes(filepath)
        
        linhas.append("### Labeler Vectorizado (Atual)\n")
        linhas.append(f"**Descrição:** {docstring.split(chr(10))[0] if docstring else 'N/A'}\n")
        
        if 'argumentos' in configs:
            linhas.append("**Argumentos CLI:**")
            for arg in configs['argumentos']:
                linhas.append(f"- `{arg}`")
            linhas.append("")
        
        linhas.append("**Funções:**")
        for func in funcoes:
            if not func['nome'].startswith('_'):
                args_str = ', '.join(func['args'][:3])
                linhas.append(f"- `{func['nome']}({args_str})`")
        linhas.append("")
    
    # Labeler original
    filepath_orig = BASE_DIR / "labeler.py"
    if filepath_orig.exists():
        linhas.append("### Labeler Original (Legado)\n")
        linhas.append("**Status:** ⚠️ Descontinuado — problemas com mistura de ativos e embargo\n")
    
    return "\n".join(linhas)


def gerar_secao_modelos() -> str:
    """Gera seção sobre modelos e comparação."""
    linhas = ["## 🤖 Modelos\n"]
    
    # Walk-forward
    filepath = BASE_DIR / "walk_forward.py"
    if filepath.exists():
        configs = extrair_config(filepath)
        linhas.append("### Configuração Padrão\n")
        linhas.append("```python")
        linhas.append("# RandomForest")
        linhas.append("n_estimators=300")
        linhas.append("max_depth=10")
        linhas.append("class_weight='balanced'")
        linhas.append("```\n")
    
    # LightGBM configs
    filepath_lgbm = BASE_DIR / "lightgbm_quick_test.py"
    if filepath_lgbm.exists():
        content = filepath_lgbm.read_text(encoding='utf-8')
        configs = re.findall(r"'num_leaves':\s*(\d+).*?'min_child_samples':\s*(\d+).*?'learning_rate':\s*([\d.]+).*?'n_estimators':\s*(\d+)", content, re.DOTALL)
        
        if configs:
            linhas.append("### LightGBM Configs Testadas\n")
            linhas.append("| Leaves | Child | LR | Est |")
            linhas.append("|--------|-------|-----|-----|")
            for c in configs[:10]:
                linhas.append(f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} |")
            linhas.append("")
    
    return "\n".join(linhas)


def gerar_secao_resultados() -> str:
    """Gera seção de resultados."""
    import json
    
    linhas = ["## 📊 Resultados\n"]
    
    for arquivo, desc in RESULTADOS.items():
        filepath = BASE_DIR / arquivo
        if not filepath.exists():
            continue
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            linhas.append(f"### {desc}\n")
            
            if 'metricas' in data:
                m = data['metricas']
                linhas.append("| Métrica | Valor |")
                linhas.append("|---------|-------|")
                for k, v in m.items():
                    if v is not None:
                        if isinstance(v, float):
                            linhas.append(f"| {k} | {v:.4f} |")
                        else:
                            linhas.append(f"| {k} | {v} |")
                linhas.append("")
            
            if 'feature_importances' in data:
                fi = data['feature_importances']
                top5 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]
                linhas.append("**Top 5 Features:**")
                for feat, imp in top5:
                    if isinstance(imp, float):
                        linhas.append(f"- {feat}: {imp:.4f}")
                    else:
                        linhas.append(f"- {feat}: {imp}")
                linhas.append("")
        
        except Exception as e:
            linhas.append(f"**Erro ao ler {arquivo}:** {e}\n")
    
    return "\n".join(linhas)


def gerar_secao_bugs() -> str:
    """Gera seção de bugs conhecidos."""
    linhas = ["## 🐛 Bugs Conhecidos\n"]
    
    bugs = [
        {
            "titulo": "UnicodeEncodeError no pipeline_diario.py",
            "severidade": "Média",
            "status": "Pendente",
            "desc": "Caracteres especiais (⚠️) causam erro em terminais Windows cp1252"
        },
        {
            "titulo": "Walk-Forward v1 com data leakage",
            "severidade": "Crítica",
            "status": "Corrigido",
            "desc": "Labeler gerava 99.99% de labels neutros — corrigido com labeler_vectorizado"
        },
        {
            "titulo": "Mistura WIN/WDO no labeler",
            "severidade": "Crítica",
            "status": "Corrigido",
            "desc": "Ativos processados juntos — corrigido com --ativo separado"
        },
        {
            "titulo": "Preços zero no labeler",
            "severidade": "Alta",
            "status": "Corrigido",
            "desc": "4.48% dos preços eram zero — corrigido com filtro preco > 0"
        }
    ]
    
    linhas.append("| Bug | Severidade | Status |")
    linhas.append("|-----|------------|--------|")
    for bug in bugs:
        status_icon = "✅" if bug['status'] == 'Corrigido' else "❌"
        linhas.append(f"| {bug['titulo']} | {bug['severidade']} | {status_icon} {bug['status']} |")
    
    linhas.append("\n### Detalhes\n")
    for bug in bugs:
        linhas.append(f"**{bug['titulo']}**  ")
        linhas.append(f"{bug['desc']}\n")
    
    return "\n".join(linhas)


def gerar_secao_como_rodar() -> str:
    """Gera seção de como rodar os testes."""
    linhas = ["## 🚀 Como Rodar\n"]
    
    linhas.append("### Testes Unitários\n")
    linhas.append("```bash")
    linhas.append("python -m pytest test_features.py -v")
    linhas.append("```\n")
    
    linhas.append("### Labeler\n")
    linhas.append("```bash")
    linhas.append('# WINV26')
    linhas.append('python labeler_vectorizado.py --input "D:\\MarketData\\mimo\\dataset_100ms_WINV26_4-17.jsonl" --ativo WINV26 --tp 100 --sl 50')
    linhas.append("")
    linhas.append('# WDOU26')
    linhas.append('python labeler_vectorizado.py --input "D:\\MarketData\\mimo\\dataset_100ms_WDOU26_4-17.jsonl" --ativo WDOU26 --tp 1 --sl 0.5')
    linhas.append("```\n")
    
    linhas.append("### Walk-Forward\n")
    linhas.append("```bash")
    linhas.append('python walk_forward.py --dataset "D:\\MarketData\\mimo\\dataset_final_v2_win.parquet"')
    linhas.append("```\n")
    
    linhas.append("### LightGBM Tuning\n")
    linhas.append("```bash")
    linhas.append("python lightgbm_quick_test.py")
    linhas.append("```\n")
    
    linhas.append("### Atualizar Esta Documentação\n")
    linhas.append("```bash")
    linhas.append("python atualizar_documentacao.py")
    linhas.append("```\n")
    
    return "\n".join(linhas)


# ============================================================
#  Main
# ============================================================

def main():
    """Gera DOCUMENTACAO_TESTES.md atualizado."""
    print("🔄 Atualizando DOCUMENTACAO_TESTES.md...")
    
    now = datetime.now()
    
    linhas = []
    
    # Header
    linhas.append("# 📋 Documentação de Testes — Freebuff Desktop\n")
    linhas.append(f"> **Versão:** {now.strftime('%Y.%m.%d')}  ")
    linhas.append(f"> **Última atualização:** {now.strftime('%d/%m/%Y %H:%M')}  ")
    linhas.append(f"> **Gerado por:** `atualizar_documentacao.py`\n")
    
    # Índice
    linhas.append("---\n")
    linhas.append("## 📑 Índice\n")
    linhas.append("1. [Scripts Principais](#1-scripts-principais)")
    linhas.append("2. [Testes Unitários](#2-testes-unitários)")
    linhas.append("3. [Labeler](#3-labeler)")
    linhas.append("4. [Modelos](#4-modelos)")
    linhas.append("5. [Resultados](#5-resultados)")
    linhas.append("6. [Bugs Conhecidos](#6-bugs-conhecidos)")
    linhas.append("7. [Como Rodar](#7-como-rodar)\n")
    linhas.append("---\n")
    
    # Seções
    linhas.append("## 1. Scripts Principais\n")
    linhas.append(gerar_secao_scripts())
    linhas.append("\n---\n")
    
    linhas.append("## 2. Testes Unitários\n")
    linhas.append(gerar_secao_testes())
    linhas.append("\n---\n")
    
    linhas.append("## 3. Labeler\n")
    linhas.append(gerar_secao_labeler())
    linhas.append("\n---\n")
    
    linhas.append("## 4. Modelos\n")
    linhas.append(gerar_secao_modelos())
    linhas.append("\n---\n")
    
    linhas.append("## 5. Resultados\n")
    linhas.append(gerar_secao_resultados())
    linhas.append("\n---\n")
    
    linhas.append("## 6. Bugs Conhecidos\n")
    linhas.append(gerar_secao_bugs())
    linhas.append("\n---\n")
    
    linhas.append("## 7. Como Rodar\n")
    linhas.append(gerar_secao_como_rodar())
    
    # Footer
    linhas.append("---\n")
    linhas.append(f"> **Nota:** Este documento é gerado automaticamente.  ")
    linhas.append(f"> Para atualizar, execute: `python atualizar_documentacao.py`  ")
    linhas.append(f"> Última geração: {now.strftime('%d/%m/%Y %H:%M:%S')}\n")
    
    # Escreve
    content = "\n".join(linhas)
    OUTPUT_FILE.write_text(content, encoding='utf-8')
    
    print(f"✅ Documentação atualizada: {OUTPUT_FILE}")
    print(f"   Tamanho: {len(content):,} caracteres")
    print(f"   Linhas: {len(linhas):,}")


if __name__ == "__main__":
    main()
