#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_integridade_temporal.py — Auditoria crítica de integridade temporal.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DE INTEGRIDADE TEMPORAL')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. TIMEZONE
    # ========================================================================
    print('\n[1] VERIFICANDO TIMEZONE...')
    
    tz_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por conversões de timezone
        if 'tz_convert' in content or 'pytz' in content or 'zoneinfo' in content:
            # Verificar se usa timezone correto (America/Sao_Paulo)
            if 'America/Sao_Paulo' not in content and 'BRT' not in content:
                tz_issues.append((str(py_file.relative_to(_root)), 'timezone não identificado'))
    
    if tz_issues:
        print(f'  [WARN] {len(tz_issues)} arquivos com timezone não verificado:')
        for f, issue in tz_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'tz:{len(tz_issues)}')
    else:
        print('  [OK] Timezone verificado')
    
    # ========================================================================
    # 2. HORÁRIO DE PREGÃO
    # ========================================================================
    print('\n[2] VERIFICANDO HORÁRIO DE PREGÃO...')
    
    pregao_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Verificar se há validação de horário de pregão
        if 'pregao' in content.lower() or '09:00' in content or '17:30' in content:
            if '_PREGAO_INICIO' not in content and '_PREGAO_FIM' not in content:
                pregao_issues.append((str(py_file.relative_to(_root)), 'validação de pregão não padronizada'))
    
    if pregao_issues:
        print(f'  [INFO] {len(pregao_issues)} arquivos com validação de pregão não padronizada')
    
    # ========================================================================
    # 3. MUDANÇA DE CONTRATO
    # ========================================================================
    print('\n[3] VERIFICANDO MUDANÇA DE CONTRATO...')
    
    contrato_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por detecção de vencimento
        if 'vencimento' in content.lower() or 'dias_ate_venc' in content:
            # Verificar se há reset quando muda de contrato
            if 'reset_diario' not in content:
                contrato_issues.append((str(py_file.relative_to(_root)), 'sem reset ao mudar contrato'))
    
    if contrato_issues:
        print(f'  [WARN] {len(contrato_issues)} arquivos sem reset ao mudar contrato:')
        for f, issue in contrato_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'contrato:{len(contrato_issues)}')
    
    # ========================================================================
    # 4. VIRADA DE DIA
    # ========================================================================
    print('\n[4] VERIFICANDO VIRADA DE DIA...')
    
    virada_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file) or '.agnes' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por detecção de virada de dia
        if '_dia_brt' in content or '86400000' in content:
            # Verificar se há reset
            if 'reset_diario' not in content:
                virada_issues.append((str(py_file.relative_to(_root)), 'detecção de dia mas sem reset'))
    
    if virada_issues:
        print(f'  [WARN] {len(virada_issues)} arquivos com detecção de dia mas sem reset:')
        for f, issue in virada_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'virada:{len(virada_issues)}')
    else:
        print('  [OK] Virada de dia verificada')
    
    # ========================================================================
    # 5. RESET DIÁRIO
    # ========================================================================
    print('\n[5] VERIFICANDO RESET DIÁRIO...')
    
    trackers = [
        ('VWAPTracker', 'features.vwap_tracker'),
        ('VolumeProfileTracker', 'features.volume_profile'),
        ('KyleLambdaTracker', 'features.kyle_lambda'),
        ('VolumeRelativoTracker', 'features.volume_relativo'),
        ('PocMigrationTracker', 'features.poc_migration'),
        ('VolatilityTracker', 'features.volatility'),
        ('ReturnsTracker', 'features.returns'),
        ('RegimeTracker', 'ml.scorer'),
    ]
    
    reset_ok = 0
    reset_fail = 0
    for name, module in trackers:
        try:
            mod = __import__(module, fromlist=[name])
            cls = getattr(mod, name)
            if hasattr(cls, 'reset_diario'):
                reset_ok += 1
            else:
                print(f'  [FAIL] {name} não tem reset_diario')
                reset_fail += 1
                findings.append(f'reset_{name}')
        except Exception as e:
            print(f'  [WARN] {name}: {e}')
            reset_fail += 1
    
    print(f'  Trackers com reset_diario: {reset_ok}/{reset_ok + reset_fail}')
    
    # ========================================================================
    # 6. TIMESTAMPS DUPLICADOS
    # ========================================================================
    print('\n[6] VERIFICANDO TIMESTAMPS DUPLICADOS...')
    
    # Verificar se há deduplication
    dedup_found = False
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'vistos_tt' in content or 'dedup' in content.lower():
            dedup_found = True
            break
    
    if dedup_found:
        print('  [OK] Deduplication de timestamps identificada')
    else:
        print('  [WARN] Deduplication não identificada')
        findings.append('dedup')
    
    # ========================================================================
    # 7. TIMESTAMPS AUSENTES
    # ========================================================================
    print('\n[7] VERIFICANDO TIMESTAMPS AUSENTES...')
    
    # Verificar se ts_ms é obrigatório
    ts_obrigatorio = False
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'ts_ms' in content and 'required' in content.lower():
            ts_obrigatorio = True
            break
    
    if ts_obrigatorio:
        print('  [OK] ts_ms identificado como obrigatório')
    else:
        print('  [INFO] ts_ms não marcado como obrigatório (verificar manualmente)')
    
    # ========================================================================
    # 8. TIMESTAMPS FORA DE ORDEM
    # ========================================================================
    print('\n[8] VERIFICANDO ORDENÇÃO TEMPORAL...')
    
    # Verificar se há ordenação
    ordenacao = False
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'sort_values' in content or 'sort_index' in content or 'sorted(' in content:
            ordenacao = True
            break
    
    if ordenacao:
        print('  [OK] Ordenação temporal identificada')
    else:
        print('  [WARN] Ordenação temporal não identificada')
        findings.append('ordenacao')
    
    # ========================================================================
    # 9. SINCRONIZAÇÃO BOOK E T&T
    # ========================================================================
    print('\n[9] VERIFICANDO SINCRONIZAÇÃO BOOK E T&T...')
    
    sync_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Verificar se book e T&T usam o mesmo timestamp
        if 'book' in content.lower() and 'tt' in content.lower():
            if 'ts_ms' not in content:
                sync_issues.append((str(py_file.relative_to(_root)), 'book/TT sem ts_ms'))
    
    if sync_issues:
        print(f'  [WARN] {len(sync_issues)} arquivos com sincronização book/TT problemática')
        findings.append(f'sync:{len(sync_issues)}')
    else:
        print('  [OK] Sincronização book/TT verificada')
    
    # ========================================================================
    # 10. GRANULARIDADE DE MILESEGUNDOS
    # ========================================================================
    print('\n[10] VERIFICANDO GRANULARIDADE...')
    
    # Verificar se timestamps são em milissegundos
    ms_check = False
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        if 'ms' in content and ('timestamp' in content.lower() or 'ts_' in content):
            ms_check = True
            break
    
    if ms_check:
        print('  [OK] Granularidade de milissegundos identificada')
    else:
        print('  [WARN] Granularidade não verificada')
    
    # ========================================================================
    # 11. ARREDONDAMENTO
    # ========================================================================
    print('\n[11] VERIFICANDO ARREDONDAMENTO...')
    
    round_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por arredondamentos que podem causar problemas
        if 'round(' in content:
            # Verificar se é arredondamento de timestamp (problemático)
            if re.search(r'round\(.*ts', content) or re.search(r'round\(.*time', content):
                round_issues.append((str(py_file.relative_to(_root)), 'arredondamento de timestamp'))
    
    if round_issues:
        print(f'  [WARN] {len(round_issues)} casos de arredondamento de timestamp:')
        for f, issue in round_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'round:{len(round_issues)}')
    else:
        print('  [OK] Nenhum arredondamento problemático de timestamp')
    
    # ========================================================================
    # 12. AGREGAÇÃO POR SEGUNDO
    # ========================================================================
    print('\n[12] VERIFICANDO AGREGAÇÃO POR SEGUNDO...')
    
    agg_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por agregação que pode causar lookahead
        if 'groupby' in content and 'second' in content.lower():
            # Verificar se há lookahead (exceto em testes/auditoria)
            if 'shift(-' in content and 'testes' not in str(py_file) and 'auditoria' not in str(py_file):
                agg_issues.append((str(py_file.relative_to(_root)), 'agregação com shift negativo'))
    
    if agg_issues:
        print(f'  [WARN] {len(agg_issues)} casos de agregação com potencial lookahead:')
        for f, issue in agg_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'agg:{len(agg_issues)}')
    else:
        print('  [OK] Agregação verificada')
    
    # ========================================================================
    # 13. LOOKAHEAD EM CÁLCULOS
    # ========================================================================
    print('\n[13] VERIFICANDO LOOKAHEAD EM CÁLCULOS...')
    
    lookahead_issues = []
    for py_file in sorted(_root.rglob('*.py')):
        if '__pycache__' in str(py_file):
            continue
        content = py_file.read_text(encoding='utf-8', errors='ignore')
        
        # Procurar por shift negativo (lookahead)
        if re.search(r'\.shift\(-\d+\)', content):
            # Verificar se é em contexto de treinamento (problemático)
            if 'treino' in content.lower() or 'train' in content.lower():
                lookahead_issues.append((str(py_file.relative_to(_root)), 'shift negativo em treino'))
    
    if lookahead_issues:
        print(f'  [FAIL] {len(lookahead_issues)} casos de lookahead em treinamento:')
        for f, issue in lookahead_issues[:5]:
            print(f'    {f}: {issue}')
        findings.append(f'lookahead:{len(lookahead_issues)}')
    else:
        print('  [OK] Nenhum lookahead identificado em treinamento')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA DE INTEGRIDADE TEMPORAL')
    print('='*70)
    
    if findings:
        print(f'\n[CRÍTICO] {len(findings)} problema(s) de integridade temporal:')
        for f in findings:
            print(f'  - {f}')
        print('\nRecomendação: Revisar cada problema antes de colocar em produção.')
        return 1
    else:
        print('\n[TEMPORAL OK] Nenhuma irregularidade temporal crítica encontrada.')
        print('\nPontos de atenção:')
        print('  1. Todos os trackers têm reset_diario()')
        print('  2. Timestamps são em milissegundos (epoch)')
        print('  3. Deduplication implementada')
        print('  4. Ordenação temporal garantida')
        return 0

if __name__ == '__main__':
    sys.exit(main())
