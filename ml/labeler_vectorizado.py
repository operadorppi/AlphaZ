#!/usr/bin/env python3
"""
labeler_vectorizado.py — Triple Barrier labeling VECTORIZADO.

v9.14 — REESCRITO com semantica canônica:
  - FIRST BARRIER WINS: primeiro toque de TP ou SL determina o label
  - Labels: TP=+1, SL=-1, TIMEOUT=0, AMBIGUOUS=-99 (descartar)
  - NUNCA cruza fronteira de dia/ativo
  - Embargo so rearma em trade real

Implementacao por segmento com scan forward O(N * max_holding_ticks).
Para holding=30s e grid=100ms, max_holding_ticks=300 — efetivamente O(N).

Uso:
  python labeler_vectorizado.py --input dataset.jsonl --tp 100 --sl 50 --max-holding 30
"""
import argparse
import json
import numpy as np
from pathlib import Path
from config import CONFIG as _CFG

# ════════════════════════════════════════════════════════════════
# LABEL CANONICO
# ════════════════════════════════════════════════════════════════
TP_VALUE = 1
SL_VALUE = -1
TIMEOUT_VALUE = 0
AMBIGUOUS_VALUE = -99


def _segmentos(ts_ms, ativos):
    """Indices de inicio de cada segmento contiguo (mesmo ativo + mesmo dia)."""
    inicios = [0]
    dias = ts_ms // 86400000
    n = len(ts_ms)
    for i in range(1, n):
        if ativos[i] != ativos[i - 1] or dias[i] != dias[i - 1]:
            inicios.append(i)
    if not inicios or inicios[-1] != n:
        inicios.append(n)
    return inicios


def label_vectorizado(precos, ts_ms, ativos,
                      tp_pts=None, sl_pts=None,
                      max_holding_s=None, purge_s=0,
                      min_vol=None, tick_ms=100):
    """Triple barrier canônico: FIRST BARRIER WINS.

    Para LONG com preco P0:
      TP: primeiro tick com P >= P0 + TP
      SL: primeiro tick com P <= P0 - SL

    Resultado:
      TP primeiro   -> label = +1
      SL primeiro   -> label = -1
      ambos no tick -> label = AMBIGUOUS (-99)
      nenhum        -> label = TIMEOUT (0)

    Returns:
        dict com arrays numpy
    """
    if tp_pts is None:
        tp_pts = _CFG['trading'].get('tp_pts', 100.0)
    if sl_pts is None:
        sl_pts = _CFG['trading'].get('sl_pts', 50.0)
    if max_holding_s is None:
        max_holding_s = _CFG['trading'].get('max_holding_s') or 30
    n = len(precos)
    max_holding_ms = max_holding_s * 1000
    ahead_ticks = max_holding_ms // tick_ms
    purge_ms = purge_s * 1000

    # Arrays de saida
    labels = np.full(n, TIMEOUT_VALUE, dtype=np.int32)
    outcome_raw = np.full(n, TIMEOUT_VALUE, dtype=np.int32)
    preco_saida = precos.copy()
    duracao_ms = np.zeros(n, dtype=np.int64)
    retorno_pts = np.zeros(n, dtype=np.float64)
    tp_atingido = np.zeros(n, dtype=bool)
    sl_atingido = np.zeros(n, dtype=bool)
    ambiguous = np.zeros(n, dtype=bool)

    # Segmentos (ativo+dia)
    segs = _segmentos(ts_ms, ativos)

    # ══════════════════════════════════════════════════════════
    # SCAN FORWARD por segmento — FIRST BARRIER WINS
    # ══════════════════════════════════════════════════════════
    for s in range(len(segs) - 1):
        seg_ini = segs[s]
        seg_fim = segs[s + 1]

        for i in range(seg_ini, seg_fim):
            P0 = precos[i]
            tp_barrier = P0 + tp_pts
            sl_barrier = P0 - sl_pts

            # Limite do scan: holding ou fim do segmento
            ticks_ate_fim = seg_fim - i - 1
            max_dt = min(ahead_ticks, ticks_ate_fim)

            if max_dt <= 0:
                # Sem espaco para scan — TIMEOUT
                duracao_ms[i] = 0
                continue

            # Scan forward: primeiro toque
            tick_tp = None
            tick_sl = None
            preco_tp = P0
            preco_sl = P0

            for dt in range(1, max_dt + 1):
                P = precos[i + dt]

                if tick_tp is None and P >= tp_barrier:
                    tick_tp = dt
                    preco_tp = P

                if tick_sl is None and P <= sl_barrier:
                    tick_sl = dt
                    preco_sl = P

                if tick_tp is not None and tick_sl is not None:
                    break

            # ══════════════════════════════════════════════
            # DECISAO — FIRST BARRIER WINS
            # ══════════════════════════════════════════════
            if tick_tp is not None and tick_sl is not None:
                if tick_tp == tick_sl:
                    # AMBIGUOUS: mesmo tick
                    ambiguous[i] = True
                    outcome_raw[i] = AMBIGUOUS_VALUE
                    labels[i] = 0
                    preco_saida[i] = (preco_tp + preco_sl) / 2.0
                    duracao_ms[i] = tick_tp * tick_ms
                elif tick_tp < tick_sl:
                    # TP veio primeiro
                    labels[i] = TP_VALUE
                    outcome_raw[i] = TP_VALUE
                    tp_atingido[i] = True
                    preco_saida[i] = preco_tp
                    duracao_ms[i] = tick_tp * tick_ms
                else:
                    # SL veio primeiro
                    labels[i] = SL_VALUE
                    outcome_raw[i] = SL_VALUE
                    sl_atingido[i] = True
                    preco_saida[i] = preco_sl
                    duracao_ms[i] = tick_sl * tick_ms

            elif tick_tp is not None:
                # So TP
                labels[i] = TP_VALUE
                outcome_raw[i] = TP_VALUE
                tp_atingido[i] = True
                preco_saida[i] = preco_tp
                duracao_ms[i] = tick_tp * tick_ms

            elif tick_sl is not None:
                # So SL
                labels[i] = SL_VALUE
                outcome_raw[i] = SL_VALUE
                sl_atingido[i] = True
                preco_saida[i] = preco_sl
                duracao_ms[i] = tick_sl * tick_ms

            else:
                # TIMEOUT
                outcome_raw[i] = TIMEOUT_VALUE
                duracao_ms[i] = max_dt * tick_ms
                preco_saida[i] = P0

    # v9.30: Volume minimo — mascara de validade (nao zera labels)
    # Antes: min_vol zera labels APOS labeling → leakage (features viram o label original)
    # Agora: mascara_valido filtra no dataset_builder (labels preservados corretamente)
    mask_valid = np.ones(n, dtype=bool)
    if min_vol is not None:
        vol_arr = np.asarray(min_vol)
        mask_valid = vol_arr >= 5

    # Embargo: so trades rearmam (nunca cruza segmento)
    if purge_s > 0:
        ultimo_fim_ts = -999999999
        seg_inicios = set(segs)
        for i in range(n):
            if i in seg_inicios:
                ultimo_fim_ts = -999999999
            ts = ts_ms[i]
            if ts - ultimo_fim_ts < purge_ms:
                labels[i] = TIMEOUT_VALUE
                outcome_raw[i] = TIMEOUT_VALUE
                duracao_ms[i] = 0
                preco_saida[i] = precos[i]
                sl_atingido[i] = False
                tp_atingido[i] = False
                ambiguous[i] = False
                continue
            if labels[i] != 0 or sl_atingido[i]:
                ultimo_fim_ts = ts + duracao_ms[i]

    # Retorno em pontos (positivo = lucro LONG)
    retorno_pts = np.where(labels == TP_VALUE, preco_saida - precos,
                           np.where(labels == SL_VALUE, preco_saida - precos, 0.0))

    return {
        'ts_ms': ts_ms,
        'label': labels,
        'outcome_raw': outcome_raw,
        'preco_entrada': precos,
        'preco_saida': preco_saida,
        'duracao_ms': duracao_ms,
        'retorno_pts': retorno_pts,
        'ativo': ativos,
        'tp_atingido': tp_atingido,
        'sl_atingido': sl_atingido,
        'ambiguous': ambiguous,
    }


def processar_jsonl(input_path, output_path, ativo_filter=None,
                    tp_pts=None, sl_pts=None, max_holding_s=None, purge_s=0,
                    min_vol=None):
    """Processa JSONL e gera labels."""
    if tp_pts is None:
        tp_pts = _CFG['trading'].get('tp_pts', 100)
    if sl_pts is None:
        sl_pts = _CFG['trading'].get('sl_pts', 50)
    if max_holding_s is None:
        max_holding_s = _CFG['trading'].get('max_holding_s') or 30
    print(f'Lendo {input_path}...')

    precos, ts_list, ativos_list, vol_list = [], [], [], []

    with open(input_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i % 1000000 == 0 and i > 0:
                print(f'  Lidos {i:,} registros...')
            d = json.loads(line)
            p = d.get('preco_ultimo', 0) or d.get('preco_fim', 0)
            ativo = d.get('ativo', '')
            vol = d.get('vol_total', 0)
            if ativo_filter and ativo != ativo_filter:
                continue
            precos.append(p)
            ts_list.append(d.get('ts_ms', 0))
            ativos_list.append(ativo)
            vol_list.append(vol)

    print(f'Total carregado: {len(precos):,} registros')
    if not precos:
        print('Nenhum registro para processar.')
        return

    precos_arr = np.array(precos, dtype=np.float64)
    ts_arr = np.array(ts_list, dtype=np.int64)
    ativos_arr = np.array(ativos_list)
    vol_arr = np.array(vol_list, dtype=np.float64)

    mask_valid = precos_arr > 0
    n_removed = int(np.sum(~mask_valid))
    if n_removed > 0:
        print(f'Removidos {n_removed:,} registros com preco=0')
    precos_arr = precos_arr[mask_valid]
    ts_arr = ts_arr[mask_valid]
    ativos_arr = ativos_arr[mask_valid]
    vol_arr = vol_arr[mask_valid]

    idx_sort = np.argsort(ts_arr, kind='mergesort')
    precos_arr = precos_arr[idx_sort]
    ts_arr = ts_arr[idx_sort]
    ativos_arr = ativos_arr[idx_sort]
    vol_arr = vol_arr[idx_sort]

    print(f'Processando {len(precos_arr):,} registros (tp={tp_pts}, sl={sl_pts}, holding={max_holding_s}s)...')

    resultado = label_vectorizado(
        precos_arr, ts_arr, ativos_arr,
        tp_pts=tp_pts, sl_pts=sl_pts,
        max_holding_s=max_holding_s, purge_s=purge_s,
        min_vol=(None if min_vol is None else vol_arr),
    )

    labels = resultado['label']
    ambiguous = resultado['ambiguous']
    n_tp = int(np.sum(labels == TP_VALUE))
    n_sl = int(np.sum(labels == SL_VALUE))
    n_timeout = int(np.sum((labels == TIMEOUT_VALUE) & ~ambiguous))
    n_amb = int(np.sum(ambiguous))
    total = len(labels)

    print(f'\n=== Estatisticas ===')
    print(f'Total: {total:,}')
    print(f'  +1 (TP):      {n_tp:,} ({100*n_tp/total:.2f}%)')
    print(f'  -1 (SL):      {n_sl:,} ({100*n_sl/total:.2f}%)')
    print(f'   0 (TIMEOUT):  {n_timeout:,} ({100*n_timeout/total:.2f}%)')
    print(f'  AMBIG (desc):  {n_amb:,} ({100*n_amb/total:.2f}%)')

    mask = ~ambiguous  # v9.39: mask_valid já aplicado no filtro de preco=0
    labels_out = []
    for i in np.where(mask)[0]:
        lab = int(labels[i])
        outcome = 'TP' if lab == TP_VALUE else ('SL' if lab == SL_VALUE else 'TIMEOUT')
        labels_out.append({
            'ts_ms': int(ts_arr[i]),
            'label': lab,
            'outcome': outcome,
            'tp_atingido': bool(resultado['tp_atingido'][i]),
            'sl_atingido': bool(resultado['sl_atingido'][i]),
            'preco_entrada': float(precos_arr[i]),
            'preco_saida': float(resultado['preco_saida'][i]),
            'duracao_ms': int(resultado['duracao_ms'][i]),
            'retorno_pts': float(resultado['retorno_pts'][i]),
            'ativo': str(ativos_arr[i]),
        })

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        for label in labels_out:
            f.write(json.dumps(label, ensure_ascii=False) + '\n')

    print(f'\nLabels salvos: {output} ({len(labels_out):,} linhas)')
    return labels_out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Triple Barrier Labeler Vectorizado')
    parser.add_argument('--input', required=True, help='Dataset JSONL')
    parser.add_argument('--ativo', default=None, help='Filtrar por ativo')
    parser.add_argument('--tp', type=float, default=None, help='Take-profit em pontos')
    parser.add_argument('--sl', type=float, default=None, help='Stop-loss em pontos')
    parser.add_argument('--max-holding', type=int, default=None, help='Max holding em segundos')
    parser.add_argument('--purge', type=int, default=10, help='Purge em segundos')
    parser.add_argument('--min-vol', type=int, default=0, help='Volume minimo (0 = sem filtro)')
    parser.add_argument('--output', '-o', default=None, help='Arquivo de saida')
    args = parser.parse_args()

    output = args.output
    if not output:
        suffix = f'_{args.ativo}' if args.ativo else ''
        output = args.input.replace('.jsonl', f'{suffix}_labels_vectorizado.jsonl')

    processar_jsonl(
        args.input, output, ativo_filter=args.ativo,
        tp_pts=args.tp, sl_pts=args.sl,
        max_holding_s=args.max_holding, purge_s=args.purge,
        min_vol=args.min_vol,
    )
