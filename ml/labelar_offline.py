# labelar_offline.py - labels triple barrier CANONICOS (labeler_vectorizado)
#
# v9.19: reescrito para usar labeler_vectorizado (semantica canonica v9.14,
# FIRST BARRIER WINS, SL real). Antes usava labeler.py (logica divergente
# com SL ignorado). Mantem o mesmo formato de saida 1:1 com o dataset e
# corrige a contaminacao cross-instrument (linhas de OUTRO ativo nunca
# recebem label do ativo alvo).
#
# Uso:
#   python labelar_offline.py --input dataset.jsonl --ativo WINV26 --tp 100 --sl 50
import json
import argparse
import numpy as np
from labeler_vectorizado import label_vectorizado


def generar_labels(input_path, output_path, ativo='WINV26', tp=100, sl=50,
                   max_holding=30, purge=0, min_vol=5):
    # Gera labels canonicos e escreve saida 1:1 com o dataset.
    # Retorna (n_total, n_pos, n_neg).
    # 1. Ler todos os snapshots (preserva ordem original p/ saida 1:1)
    snaps = []
    with open(input_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                snaps.append(json.loads(line))

    # 2. Filtrar ativo e ordenar cronologicamente
    alvo = [s for s in snaps if s.get('ativo') == ativo]
    alvo.sort(key=lambda s: s['ts_ms'])
    print(f'total: {len(snaps)} | {ativo}: {len(alvo)}')

    precos = np.array(
        [s.get('preco_ultimo', 0) or s.get('preco_fim', 0) for s in alvo],
        dtype=np.float64)
    ts = np.array([s.get('ts_ms', 0) for s in alvo], dtype=np.int64)
    ativos = np.array([s.get('ativo', '') for s in alvo])
    vols = np.array([s.get('vol_total', 0) for s in alvo], dtype=np.float64)

    res = label_vectorizado(
        precos, ts, ativos,
        tp_pts=tp, sl_pts=sl,
        max_holding_s=max_holding, purge_s=purge,
        min_vol=vols,
    )

    por_ts = {}
    for i in range(len(alvo)):
        por_ts[int(ts[i])] = {
            'label': int(res['label'][i]),
            'retorno_pts': float(res['retorno_pts'][i]),
            'duracao_ms': int(res['duracao_ms'][i]),
        }

    n1 = sum(1 for l in por_ts.values() if l['label'] == 1)
    nm1 = sum(1 for l in por_ts.values() if l['label'] == -1)
    print(f'positivos: {n1} | negativos: {nm1} | neutros: {len(alvo) - n1 - nm1}')
    print(f'taxa de labels: {100 * (n1 + nm1) / max(len(alvo), 1):.1f}%')

    with open(output_path, 'w', encoding='utf-8') as f:
        for s in snaps:  # preserva ordem 1:1 com o dataset
            if s.get('ativo') == ativo:
                info = por_ts.get(s.get('ts_ms', 0), {})
            else:
                info = {}  # v9.19: sem contaminacao cross-instrument
            f.write(json.dumps({
                'ts_ms': s.get('ts_ms', 0),
                'label': info.get('label', 0),
                'retorno_pts': info.get('retorno_pts', 0),
                'duracao_ms': info.get('duracao_ms', 0),
            }, ensure_ascii=False) + '\n')
    print(f'salvo: {output_path}')
    return len(snaps), n1, nm1


def main():
    ap = argparse.ArgumentParser(description='Labels triple barrier canonicos')
    ap.add_argument('--input', required=True)
    ap.add_argument('--ativo', default='WINV26')
    ap.add_argument('--tp', type=float, default=100)
    ap.add_argument('--sl', type=float, default=50)
    ap.add_argument('--max-holding', type=int, default=30)
    ap.add_argument('--purge', type=int, default=0)
    ap.add_argument('--min-vol', type=int, default=5)
    args = ap.parse_args()

    out = args.input.replace('.jsonl', '_labels.jsonl')
    generar_labels(args.input, out, ativo=args.ativo, tp=args.tp, sl=args.sl,
                   max_holding=args.max_holding, purge=args.purge,
                   min_vol=args.min_vol)


if __name__ == '__main__':
    main()
