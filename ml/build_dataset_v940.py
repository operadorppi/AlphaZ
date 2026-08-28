# -*- coding: utf-8 -*-
# build_dataset_v940.py — Reconstrói dataset COM features de contexto (v9.40)
# Gera arquivos auxiliares e roda dataset_builder com contexto completo
import sys, os, subprocess, json, time
from pathlib import Path

SAVE_DIR = Path('D:/MarketData/mimo/26')
RAW_DIR = SAVE_DIR

def run(cmd, label):
    print(f'\n>>> {label}')
    print(f'    {cmd}')
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd='C:/Freebuff')
    if r.stdout: print(r.stdout[-500:] if len(r.stdout) > 500 else r.stdout)
    if r.returncode != 0:
        print(f'    ERRO (exit {r.returncode}): {r.stderr[-300:] if r.stderr else "?"}')
    else:
        print(f'    OK ({time.time()-t0:.0f}s)')
    return r.returncode

def main():
    t_total = time.time()
    
    # 1. Gerar referência diária D-1
    ref_path = SAVE_DIR / 'ref_diaria_202608.json'
    if not ref_path.exists():
        print('\n=== 1. Gerando referência diária D-1 ===')
        # Usar features_contexto_preco para gerar ref
        code = '''
import sys; sys.path.insert(0, 'C:/Freebuff')
import pandas as pd, json
from pathlib import Path
from features_contexto_preco import calcular_referencia_diaria

SAVE = Path('D:/MarketData/mimo/26')
# Carregar dataset_100ms existente para extrair referências
feats = sorted(Path(SAVE).glob('dataset_100ms_*.jsonl'))
if not feats:
    print('Nenhum dataset_100ms encontrado, criando ref vazia')
    json.dump({}, open(SAVE / 'ref_diaria_202608.json', 'w'))
else:
    dfs = []
    for f in feats[:20]:
        try:
            df = pd.read_json(f, lines=True)
            if len(df) > 0: dfs.append(df)
        except: pass
    if dfs:
        df = pd.concat(dfs, ignore_index=True)
        ref = calcular_referencia_diaria(df)
        ref.to_json(SAVE / 'ref_diaria_202608.json', orient='records')
        print(f'Ref diária: {len(ref)} registros')
    else:
        print('Sem dados, criando ref vazia')
        json.dump({}, open(SAVE / 'ref_diaria_202608.json', 'w'))
'''
        with open('_tmp_ref.py', 'w') as f: f.write(code)
        run(f'python _tmp_ref.py', 'Gerando ref diária')
    else:
        print(f'\n=== 1. Ref diária já existe: {ref_path} ===')
    
    # 2. Gerar ajuste diário B3
    ajuste_path = SAVE_DIR / 'ajuste_diario_202608.csv'
    if not ajuste_path.exists():
        print('\n=== 2. Gerando ajuste diário B3 ===')
        # Tentar rodar calcular_ajuste_diario
        run(f'python ml/calcular_ajuste_diario.py --output {ajuste_path}', 'Ajuste B3')
        if not ajuste_path.exists():
            # Criar CSV mínimo se o script falhar
            print('    Criando CSV mínimo de ajuste')
            import csv
            with open(ajuste_path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['data_pregao','contrato','ajuste'])
                for d in range(4, 18):
                    w.writerow([f'2026-08-{d:02d}', 'WINV26', 0])
                    w.writerow([f'2026-08-{d:02d}', 'WDOU26', 0])
            print(f'    CSV mínimo criado: {ajuste_path}')
    else:
        print(f'\n=== 2. Ajuste já existe: {ajuste_path} ===')
    
    # 3. Gerar VWAP diária
    vwap_path = SAVE_DIR / 'vwap_diaria_202608.parquet'
    if not vwap_path.exists():
        print('\n=== 3. Gerando VWAP diária ===')
        run(f'python ml/calcular_vwap_diaria.py --output {vwap_path}', 'VWAP diária')
        if not vwap_path.exists():
            print('    VWAP não gerado, continuando sem VWAP')
    else:
        print(f'\n=== 3. VWAP já existe: {vwap_path} ===')
    
    # 4. Reconstruir dataset com contexto
    print('\n=== 4. Reconstruindo dataset com contexto ===')
    features_file = str(SAVE_DIR / 'dataset_100ms_WINV26_4-17.jsonl')
    labels_file = str(SAVE_DIR / 'labels_WINV26_4-17_v939.jsonl')
    output = str(SAVE_DIR / 'dataset_final_WINV26_v940.parquet')
    
    cmd = f'python ml/dataset_builder.py --features {features_file} --labels {labels_file} --output {output} --formato parquet'
    cmd += f' --contexto --ajuste-oficial {ajuste_path}'
    if vwap_path.exists():
        cmd += f' --vwap-por-negocio {vwap_path}'
    
    run(cmd, 'Dataset com contexto')
    
    # 5. Verificar resultado
    if os.path.exists(output):
        import pyarrow.parquet as pq
        t = pq.read_table(output)
        print(f'\n=== RESULTADO ===')
        print(f'Linhas: {t.num_rows:,}')
        print(f'Colunas: {t.num_columns}')
        print(f'Colunas: {t.column_names}')
        sz = os.path.getsize(output) / (1024*1024)
        print(f'Tamanho: {sz:.1f} MB')
    
    print(f'\nTempo total: {time.time()-t_total:.0f}s')
    os.remove('_tmp_ref.py') if os.path.exists('_tmp_ref.py') else None

if __name__ == '__main__':
    main()
