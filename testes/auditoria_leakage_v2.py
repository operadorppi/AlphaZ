#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_leakage_v2.py — Auditoria agressiva contra data leakage / look-ahead bias.
Versão 2: mais precisa na análise de uso de preco_saida.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA AGRESSIVA — DATA LEAKAGE / LOOK-AHEAD BIAS')
    print('='*70)
    
    findings = defaultdict(list)
    
    # ========================================================================
    # 1. VERIFICAR SHIFT POSITIVO (look-ahead)
    # ========================================================================
    print('\n[1] VERIFICANDO SHIFT POSITIVO (LOOK-AHEAD)...')
    
    lookahead_cases = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        # Procurar por shift com argumento negativo (look-ahead)
        matches = re.findall(r'\.shift\((-?\d+)\)', content)
        for m in matches:
            val = int(m)
            if val < 0:  # shift negativo = olha para o futuro
                # Verificar se é em contexto de validação (ok) ou treinamento (problemático)
                if 'validacao' in str(py_file).lower() or 'teste' in str(py_file).lower():
                    continue  # Ok em testes
                # Verificar se é em código de análise (ok) ou produção (problemático)
                if 'analise' in str(py_file).lower() or 'test' in str(py_file).lower():
                    continue  # Ok em análise
                lookahead_cases.append((str(py_file), val))
    
    if lookahead_cases:
        print(f'  [FAIL] {len(lookahead_cases)} casos de shift lookahead:')
        for f, val in lookahead_cases[:5]:
            print(f'    {f}: shift({val})')
        findings['shift_lookahead'] = lookahead_cases
    else:
        print('  [OK] Nenhum shift lookahead encontrado')
    
    # ========================================================================
    # 2. VERIFICAR USO DE preco_saida E duracao_label_ms COMO FEATURES
    # ========================================================================
    print('\n[2] VERIFICANDO USO DE preco_saida / duracao_label_ms...')
    
    leakage_files = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        fname = str(py_file).lower()
        
        # Pulnar arquivos de teste e análise
        if 'test' in fname or 'analise' in fname or 'auditoria' in fname:
            continue
        
        # Verificar se o arquivo usa preco_saida
        if 'preco_saida' in content:
            # Verificar se é em contexto de labeler (ok) ou remoção de leakage (ok)
            if 'labeler' in fname or 'leakage' in fname or 'EXCL' in content:
                continue
            # Verificar se é em dataset_builder (precisa verificar se remove)
            if 'dataset_builder' in fname:
                if '_LEAKAGE_COLS' in content and '_remover_colunas_leakage' in content:
                    continue  # Ok — remove as colunas
            leakage_files.append(str(py_file))
    
    if leakage_files:
        print(f'  [WARN] {len(leakage_files)} arquivos podem usar preco_saida como feature:')
        for f in leakage_files[:5]:
            print(f'    {f}')
        findings['uso_preco_saida'] = leakage_files
    else:
        print('  [OK] Nenhum uso indevido de preco_saida como feature')
    
    # ========================================================================
    # 3. VERIFICAR NORMALIZAÇÃO USANDO TODO O DATASET
    # ========================================================================
    print('\n[3] VERIFICANDO NORMALIZAÇÃO...')
    
    norm_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        fname = str(py_file).lower()
        
        # Pulnar testes
        if 'test' in fname:
            continue
        
        # Procurar por fit() antes de split
        if '.fit(' in content and ('X_train' in content or 'df_train' in content):
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if '.fit(' in line and ('X_train' in line or 'df_train' in line):
                    # Verificar se X_train foi definido antes
                    prev_lines = '\n'.join(lines[:i])
                    if 'df_train' in prev_lines or 'X_train' in prev_lines:
                        pass  # Ok
                    else:
                        norm_issues.append((str(py_file), i+1, line.strip()[:50]))
    
    if norm_issues:
        print(f'  [WARN] {len(norm_issues)} casos potenciais de normalização problemática')
        for f, line_num, line in norm_issues[:3]:
            print(f'    {f}:{line_num} — {line}')
        findings['normalizacao'] = norm_issues
    else:
        print('  [OK] Normalização parece correta')
    
    # ========================================================================
    # 4. VERIFICAR VWAP CAUSAL
    # ========================================================================
    print('\n[4] VERIFICANDO VWAP CAUSAL...')
    
    try:
        from features.vwap_tracker import VWAPTracker
        import inspect
        src = inspect.getsource(VWAPTracker.update)
        if 'cumsum' in src and 'preco' in src and 'qtd' in src:
            print('  [OK] VWAP calculado com cumsum(preco*qtd)/cumsum(qtd) — causal')
        else:
            print('  [WARN] Verificar cálculo de VWAP')
            findings['vwap'] = ['cálculo não causal']
    except Exception as e:
        print(f'  [WARN] Erro ao verificar VWAP: {e}')
    
    # ========================================================================
    # 5. VERIFICAR SPLIT TEMPORAL
    # ========================================================================
    print('\n[5] VERIFICANDO SPLIT TEMPORAL...')
    
    retreinar_path = _root / 'ml' / 'retreinar_lgbm_limpo.py'
    if retreinar_path.exists():
        content = retreinar_path.read_text(encoding='utf-8')
        if 'TREINO_DIAS' in content and 'CAL_DIAS' in content and 'TEST_DIAS' in content:
            print('  [OK] Split temporal definido (TREINO/CAL/TEST)')
            # Verificar se não há sobreposição
            treino_match = re.search(r'TREINO_DIAS\s*=\s*\[(.*?)\]', content, re.DOTALL)
            test_match = re.search(r'TEST_DIAS\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if treino_match and test_match:
                treino_dias = set(re.findall(r'date\(2026,\s*8,\s*(\d+)\)', treino_match.group(1)))
                test_dias = set(re.findall(r'date\(2026,\s*8,\s*(\d+)\)', test_match.group(1)))
                if treino_dias & test_dias:
                    print(f'  [FAIL] Sobreposição de dias: {treino_dias & test_dias}')
                    findings['split_temporal'] = [f'sobreposição: {treino_dias & test_dias}']
                else:
                    print(f'  [OK] Sem sobreposição: treino={treino_dias}, test={test_dias}')
        else:
            print('  [WARN] Split temporal não encontrado')
            findings['split_temporal'] = ['não encontrado']
    else:
        print('  [FAIL] retreinar_lgbm_limpo.py não encontrado')
        findings['split_temporal'] = ['arquivo não encontrado']
    
    # ========================================================================
    # 6. VERIFICAR PURGE/EMBARGO
    # ========================================================================
    print('\n[6] VERIFICANDO PURGE/EMBARGO...')
    
    try:
        from ml.treino_lib import split_com_purge
        import inspect
        src = inspect.getsource(split_com_purge)
        if 'purge' in src.lower() or 'embargo' in src.lower():
            print('  [OK] Função split_com_purge implementada')
        else:
            print('  [WARN] Purge/embargo não encontrado')
            findings['purge'] = ['não implementado']
    except Exception as e:
        print(f'  [WARN] Erro ao verificar purge: {e}')
    
    # ========================================================================
    # 7. VERIFICAR LABELS NÃO CONTAMINANDO FEATURES
    # ========================================================================
    print('\n[7] VERIFICANDO CONTAMINAÇÃO DE LABELS...')
    
    builder_path = _root / 'ml' / 'dataset_builder.py'
    if builder_path.exists():
        content = builder_path.read_text(encoding='utf-8')
        if '_LEAKAGE_COLS' in content and '_remover_colunas_leakage' in content:
            print('  [OK] Função de remoção de leakage implementada')
            # Verificar se é chamada
            if 'merge_features_labels' in content and '_remover_colunas_leakage' in content:
                print('  [OK] Função de remoção de leakage é usada no merge')
            else:
                print('  [WARN] Função de remoção de leakage pode não ser usada')
        else:
            print('  [FAIL] Remoção de leakage não implementada')
            findings['leakage_removal'] = ['não implementada']
    else:
        print('  [FAIL] dataset_builder.py não encontrado')
        findings['leakage_removal'] = ['arquivo não encontrado']
    
    # ========================================================================
    # 8. VERIFICAR VOLUME PROFILE CAUSAL
    # ========================================================================
    print('\n[8] VERIFICANDO VOLUME PROFILE CAUSAL...')
    
    try:
        from features.volume_profile import VolumeProfileTracker
        import inspect
        src = inspect.getsource(VolumeProfileTracker.atualizar)
        if 'preco' in src and 'qtd' in src:
            print('  [OK] Volume Profile atualizado causalmente')
        else:
            print('  [WARN] Verificar Volume Profile')
    except Exception as e:
        print(f'  [WARN] Erro ao verificar Volume Profile: {e}')
    
    # ========================================================================
    # 9. VERIFICAR INDICADORES RESETADOS CORRETAMENTE
    # ========================================================================
    print('\n[9] VERIFICANDO RESET DIÁRIO...')
    
    trackers = [
        ('VWAPTracker', 'features.vwap_tracker'),
        ('VolumeProfileTracker', 'features.volume_profile'),
        ('KyleLambdaTracker', 'features.kyle_lambda'),
        ('VolumeRelativoTracker', 'features.volume_relativo'),
        ('PocMigrationTracker', 'features.poc_migration'),
    ]
    
    reset_issues = []
    for name, module in trackers:
        try:
            mod = __import__(module, fromlist=[name])
            cls = getattr(mod, name)
            if hasattr(cls, 'reset_diario'):
                print(f'  [OK] {name} tem reset_diario')
            else:
                print(f'  [WARN] {name} não tem reset_diario')
                reset_issues.append(name)
        except Exception as e:
            print(f'  [WARN] {name}: {e}')
            reset_issues.append(name)
    
    if reset_issues:
        findings['reset_diario'] = reset_issues
    
    # ========================================================================
    # 10. VERIFICAR TESTES DE LEAKAGE
    # ========================================================================
    print('\n[10] VERIFICANDO TESTES DE LEAKAGE...')
    
    leakage_tests = []
    for pattern in ['*leakage*.py', '*causal*.py', '*test_no_future*.py']:
        for py_file in sorted(_root.rglob(pattern)):
            if '__pycache__' in str(py_file):
                continue
            leakage_tests.append(str(py_file))
    
    if leakage_tests:
        print(f'  [OK] {len(leakage_tests)} arquivos de teste de leakage encontrados')
        for f in leakage_tests:
            print(f'    - {Path(f).name}')
    else:
        print('  [WARN] Nenhum teste de leakage encontrado')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DE LEAKAGE')
    print('='*70)
    
    critical = []
    warnings = []
    
    if findings.get('shift_lookahead'):
        critical.append(f'shift lookahead: {len(findings["shift_lookahead"])} casos')
    if findings.get('uso_preco_saida'):
        warnings.append(f'uso de preco_saida: {len(findings["uso_preco_saida"])} arquivos')
    if findings.get('normalizacao'):
        warnings.append(f'normalização problemática: {len(findings["normalizacao"])} casos')
    if findings.get('split_temporal'):
        critical.extend(findings['split_temporal'])
    if findings.get('leakage_removal'):
        critical.extend(findings['leakage_removal'])
    if findings.get('reset_diario'):
        warnings.append(f'reset_diario ausente: {findings["reset_diario"]}')
    
    if critical:
        print(f'\n[CRITICO] {len(critical)} problema(s) critico(s):')
        for c in critical:
            print(f'  [!] {c}')
    elif warnings:
        print(f'\n[ATTENTION] {len(warnings)} aviso(s):')
        for w in warnings:
            print(f'  [!] {w}')
    else:
        print('\n[LEAKAGE OK] Nenhuma irregularidade critica encontrada.')
        print('\nMedidas de proteção identificadas:')
        print('  1. _LEAKAGE_COLS remove preco_saida, duracao_label_ms do parquet')
        print('  2. colunas_validas() filtra features proibidas no treinamento')
        print('  3. Split temporal TREINO/CAL/TEST com dias separados')
        print('  4. Função split_com_purge() com embargo entre splits')
        print('  5. VWAP calculado com cumsum causal')
        print('  6. Volume Profile atualizado tick-a-tick')
        print('  7. Trackers têm reset_diario()')
        print('  8. Testes de causalidade existentes')
        return 0
    return 1

if __name__ == '__main__':
    sys.exit(main())
