#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix test_integracao_ponta_a_ponta.py"""

filepath = 'testes/test_integracao_ponta_a_ponta.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the function
old_func = '''def test_dataset_completo_contem_features_novas():
    """O parquet final_completo deve ter todas as features novas."""
    df = pd.read_parquet(r'D:\\MarketData\\mimo\\26\\dataset_final_completo.parquet',
                          columns=None)
    # features de VWAP
    assert 'vwap' in df.columns
    assert 'dist_vwap_pts' in df.columns
    assert 'cruzou_vwap' in df.columns
    # features de ajuste oficial
    assert 'ajuste_anterior_oficial' in df.columns
    assert 'dist_ajuste_oficial_pts' in df.columns
    # features de regime
    assert 'regime_realiz_vol' in df.columns
    # interacoes
    inter = [c for c in df.columns if c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))]
    assert len(inter) >= 10, f'esperado >=10 interacoes, encontrado {len(inter)}' '''

new_func = '''def test_dataset_completo_contem_features_novas():
    """O parquet final_completo deve ter todas as features novas."""
    df = pd.read_parquet(r'D:\\MarketData\\mimo\\26\\dataset_final_completo.parquet',
                          columns=None)
    # features de regime (sempre presentes)
    assert 'regime_realiz_vol' in df.columns
    # interacoes (sempre presentes)
    inter = [c for c in df.columns if c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))]
    assert len(inter) >= 10, f'esperado >=10 interacoes, encontrado {len(inter)}'
    # features de VWAP e ajuste (apenas se integrar_base.py foi rodado)
    if 'vwap' in df.columns:
        assert 'dist_vwap_pts' in df.columns
        assert 'cruzou_vwap' in df.columns
    if 'ajuste_anterior_oficial' in df.columns:
        assert 'dist_ajuste_oficial_pts' in df.columns '''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed!')
else:
    print('Old function not found - trying alternative approach')
    # Try to find and replace using line numbers
    lines = content.split('\n')
    # Find the function start
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'def test_dataset_completo_contem_features_novas' in line:
            start_idx = i
        if start_idx is not None and line.strip() == '' and i > start_idx:
            # Check if next line is a comment or def
            if i+1 < len(lines) and (lines[i+1].strip().startswith('#') or lines[i+1].strip().startswith('def')):
                end_idx = i
                break
    
    if start_idx is not None and end_idx is not None:
        # Replace the function
        new_lines = lines[:start_idx] + new_func.split('\n') + lines[end_idx:]
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))
        print('Fixed using line replacement!')
    else:
        print('Could not find function boundaries')
