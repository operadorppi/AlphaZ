#!/usr/bin/env python3
"""
run_labeler_completo.py — Roda o labeler em todos os dados brutos e gera labels corretos.
Uso: python ml/run_labeler_completo.py
"""
import sys, os, json, time
import numpy as np
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.labeler_vectorizado import label_vectorizado

BASE = Path('D:/MarketData/mimo')
RAW = BASE / 'raw_negocios_ms_*.jsonl'
OUT_LABELS = BASE / '26' / 'labels_win_completo.jsonl'
ATIVO = 'WINV26'
TP = 100
SL = 50
HOLDING = 300
PURGE = 5

def main():
    print('=' * 50)
    print('LABELER COMPLETO — WINV26')
    print('=' * 50)
    
    # Carregar todos os registros WINV26
    precos, ts, ativos_list = [], [], []
    files = sorted(BASE.glob('raw_negocios_ms_*.jsonl'))
    print(f'Arquivos: {len(files)}')
    
    t0 = time.time()
    for f in files:
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                d = json.loads(line)
                if d.get('ativo') == ATIVO and d.get('preco', 0) > 0:
                    precos.append(d['preco'])
                    ts.append(d['ts_ms'])
                    ativos_list.append(ATIVO)
    
    print(f'Registros {ATIVO}: {len(precos):,} ({time.time()-t0:.1f}s)')
    
    if len(precos) < 100:
        print('Dados insuficientes!')
        return
    
    # Rodar labeler
    precos_arr = np.array(precos, dtype=np.float64)
    ts_arr = np.array(ts, dtype=np.int64)
    ativos_arr = np.array(ativos_list)
    
    print(f'Rodando labeler (tp={TP}, sl={SL}, holding={HOLDING}s)...')
    t1 = time.time()
    result = label_vectorizado(precos_arr, ts_arr, ativos_arr,
                                tp_pts=TP, sl_pts=SL, max_holding_s=HOLDING, purge_s=PURGE)
    elapsed = time.time() - t1
    print(f'Labeler: {elapsed:.1f}s')
    
    # Estatisticas
    label = result['label']
    ret = result['retorno_pts']
    n1 = int(np.sum(label == 1))
    nm1 = int(np.sum(label == -1))
    n0 = int(np.sum(label == 0))
    total = len(label)
    
    print(f'\nResultados:')
    print(f'  +1 (TP): {n1} ({100*n1/total:.2f}%)')
    print(f'  -1 (SL): {nm1} ({100*nm1/total:.2f}%)')
    print(f'   0 (neutro): {n0} ({100*n0/total:.2f}%)')
    if n1 > 0:
        print(f'  Retorno TP medio: {np.mean(ret[label==1]):.1f} pts')
    if nm1 > 0:
        print(f'  Retorno SL medio: {np.mean(ret[label==-1]):.1f} pts')
    
    # Salvar labels
    print(f'\nSalvando: {OUT_LABELS}')
    OUT_LABELS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_LABELS, 'w', encoding='utf-8') as f:
        for i in range(total):
            f.write(json.dumps({
                'ts_ms': int(ts_arr[i]),
                'ativo': ATIVO,
                'label': int(label[i]),
                'preco_entrada': float(precos_arr[i]),
                'preco_saida': float(result['preco_saida'][i]),
                'retorno_pts': float(ret[i]),
                'duracao_ms': int(result['duracao_ms'][i]),
                'tp_atingido': bool(result['tp_atingido'][i]),
                'sl_atingido': bool(result['sl_atingido'][i]),
            }, ensure_ascii=False) + '\n')
    
    print(f'Concluido: {total} labels salvos')
    print(f'Tempo total: {time.time()-t0:.1f}s')

if __name__ == '__main__':
    main()
