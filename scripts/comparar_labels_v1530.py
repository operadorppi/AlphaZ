#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comparar_labels_v1530.py — A/B do labeler temporal (P0-A33 / v15.30).

Compara, sobre os MESMOS eventos do RAW Hive (Parquet particionado), as duas
semanticas de horizonte do triple barrier:

  ANTES (HEAD, pre-v15.30): horizonte por CONTAGEM DE LINHAS
      ahead_ticks = max_holding_ms // tick_ms   (30s / 100ms = 300 linhas)
      duracao_ms  = tick * tick_ms              (linhas * 100ms)
      -> em rajada, 300 linhas podem ser < 1s real; em silencio, 300 linhas
         podem ser varios minutos.

  DEPOIS (v15.30, working tree): horizonte por TIMESTAMP REAL
      horizonte = ts[i] + max_holding_ms        (30s reais)
      duracao_ms = delta real de timestamps
      -> identico ao ANTES em grid uniforme 100ms; correto em dados
         irregulares (RAW de eventos).

O codigo ANTES e extraido literalmente do git (HEAD:ml/labeler_vectorizado.py)
para nao haver divergencia de portabilidade — a comparacao e codigo vs codigo.

Uso:
  python scripts/comparar_labels_v1530.py --modo amostra [--tp 20 --sl 15 --holding 30]
  python scripts/comparar_labels_v1530.py --modo full

Saidas:
  - relatorio JSON + tabela por ativo (distribuicao ANTES x DEPOIS, matriz de
    transicao, mudanca de duracao)
  - labels regenerados (DEPOIS) em JSONL:  <out>/labels_<asset>_<date>.jsonl
  - labels ANTES em JSONL:                 <out>/legado/labels_<asset>_<date>.jsonl

Nota: o labeler puro Python custa ~1.5-2.5 ms/linha — dia cheio de WIN (2.5M)
leva ~1.5-2 h. O modo `amostra` usa janelas deterministicas por fase da sessao
(abertura, manha, almoco, tarde, fechamento) para a validacao A/B; o modo
`full` regenera o dia inteiro (recomendado na rotina pos-pregao / overnight).
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.labeler_vectorizado import label_vectorizado as label_depois  # noqa: E402

BRT = timezone(timedelta(hours=-3))

RAW_BASE = Path('D:/MarketData/mimo/RAW')
OUT_BASE = Path('D:/MarketData/mimo/26')
ATIVOS = ['WIN', 'WDO', 'IND', 'DOL']

# Janelas por fase da sessao (offset_s desde a abertura do ativo, duracao_s).
# Cobrem abertura / manha / almoco / tarde / fechamento de forma deterministica.
JANELAS_S = [(0, 600), (7200, 600), (14400, 600), (18000, 600), (21600, 600),
             (24000, 600)]
ROW_CAP_POR_JANELA = 25_000
# descarta o fim de cada janela (labels nao resolviveis sem futuro real)
TAIL_DROP_S = 40


def carregar_legado():
    """Extrai o labeler ANTES (HEAD) para um modulo temporario e o importa."""
    try:
        src = subprocess.run(
            ['git', 'show', 'HEAD:ml/labeler_vectorizado.py'],
            capture_output=True, text=True, encoding='utf-8', check=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        ).stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            'Nao foi possivel extrair HEAD:ml/labeler_vectorizado.py do git '
            f'({e}). Rode com --modo full? O A/B ANTES x DEPOIS exige o git.'
        )
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False,
                                     encoding='utf-8') as f:
        f.write(src)
        path = f.name
    spec = importlib.util.spec_from_file_location('labeler_legado', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.label_vectorizado


def carregar_ativo(asset, date_str):
    """Le o fluxo TT canonico (nao-RLP) do ativo no dia."""
    d = f'{RAW_BASE}/data_type=TT/date={date_str}/asset={asset}'
    import pyarrow.dataset as ds
    ds_dir = ds.dataset(d, format='parquet', partitioning='hive')
    tb = ds_dir.to_table(columns=['ts_ns', 'preco', 'quantidade'])
    px = tb.column('preco').to_numpy(zero_copy_only=False).astype(np.float64)
    qtd = tb.column('quantidade').to_numpy(zero_copy_only=False).astype(np.int64)
    ts = (tb.column('ts_ns').to_numpy() // 1_000_000).astype(np.int64)
    m = px > 0
    px, ts, qtd = px[m], ts[m], qtd[m]
    at = np.array([asset] * len(px))
    return px, ts, qtd, at


def janelas_do_ativo(ts, qtd, mode):
    """Retorna lista de (nome, mascara) com os eventos de cada janela."""
    if mode == 'full':
        return [('DIA_TODO', np.ones(len(ts), dtype=bool))]
    t0 = int(ts.min())
    t1 = int(ts.max())
    out = []
    for off, dur in JANELAS_S:
        a = t0 + off * 1000
        b = a + dur * 1000
        if a >= t1:
            continue
        b = min(b, t1)
        m = (ts >= a) & (ts < b)
        if m.sum() < 100:
            continue
        # recorte determinístico: primeiras ROW_CAP linhas da janela
        idx = np.where(m)[0]
        cap = min(len(idx), ROW_CAP_POR_JANELA)
        idx = idx[:cap]
        # descarta os ultimos TAIL_DROP_S da janela recortada (futuro truncado)
        m2 = np.zeros(len(ts), dtype=bool)
        m2[idx] = True
        corte = ts[idx][-1] - TAIL_DROP_S * 1000
        m2 &= ts >= ts[idx][0]
        m2 &= ts <= corte
        nome = f'{off//60:02d}min+{dur//60}min'
        out.append((nome, m2))
    return out


def rodar_semantica(fn, px, ts, at, tp, sl, holding, purge, tick_ms):
    r = fn(px, ts, at, tp_pts=tp, sl_pts=sl, max_holding_s=holding,
           purge_s=purge, tick_ms=tick_ms)
    return r


def resumir(r):
    lab = r['label']
    amb = r['ambiguous']
    n = len(lab)
    tp = int(np.sum((lab == 1) & ~amb))
    sl = int(np.sum((lab == -1) & ~amb))
    to = int(np.sum((lab == 0) & ~amb))
    namb = int(np.sum(amb))
    dur = r['duracao_ms']
    ret = r['retorno_pts']
    s = {
        'total': n,
        'TP': tp, 'SL': sl, 'TIMEOUT': to, 'AMBIGUO': namb,
        'pct_TP': round(100 * tp / n, 2),
        'pct_SL': round(100 * sl / n, 2),
        'pct_TIMEOUT': round(100 * to / n, 2),
        'dur_media_ms_TP': round(float(dur[(lab == 1) & ~amb].mean()), 1) if tp else 0,
        'dur_media_ms_SL': round(float(dur[(lab == -1) & ~amb].mean()), 1) if sl else 0,
        'dur_media_ms_TIMEOUT': round(float(dur[(lab == 0) & ~amb].mean()), 1) if to else 0,
        'ret_medio_TP': round(float(ret[(lab == 1) & ~amb].mean()), 3) if tp else 0,
        'ret_medio_SL': round(float(ret[(lab == -1) & ~amb].mean()), 3) if sl else 0,
        'ret_medio_TIMEOUT': round(float(ret[(lab == 0) & ~amb].mean()), 3) if to else 0,
    }
    return s


def salvar_labels(r, path):
    lab = r['label']
    amb = r['ambiguous']
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(lab)
    with open(path, 'w', encoding='utf-8') as f:
        for i in range(n):
            if amb[i]:
                continue
            l = int(lab[i])
            f.write(json.dumps({
                'ts_ms': int(r['ts_ms'][i]),
                'ativo': str(r['ativo'][i]),
                'label': l,
                'outcome': 'TP' if l == 1 else ('SL' if l == -1 else 'TIMEOUT'),
                'preco_entrada': float(r['preco_entrada'][i]),
                'preco_saida': float(r['preco_saida'][i]),
                'retorno_pts': float(r['retorno_pts'][i]),
                'duracao_ms': int(r['duracao_ms'][i]),
                'tp_atingido': bool(r['tp_atingido'][i]),
                'sl_atingido': bool(r['sl_atingido'][i]),
            }, ensure_ascii=False) + '\n')
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--modo', choices=['amostra', 'full'], default='amostra')
    ap.add_argument('--semantica', choices=['ambas', 'depois', 'antes'],
                    default='ambas',
                    help='ambas (A/B, padrao); depois = so v15.30 (full day rapido);'
                         ' antes = so HEAD (legado)')
    ap.add_argument('--data', default='20260903')
    ap.add_argument('--tp', type=float, default=20.0)
    ap.add_argument('--sl', type=float, default=15.0)
    ap.add_argument('--holding', type=int, default=30)
    ap.add_argument('--purge', type=int, default=0,
                    help='embargo apos trade concluido; 0 = A/B puro de semantica A33')
    ap.add_argument('--tick', type=int, default=100, help='tick_ms do ANTES (legado)')
    ap.add_argument('--ativo', default=None, help='so um ativo (WIN/WDO/IND/DOL)')
    args = ap.parse_args()

    print(f'[v15.30-A/B] modo={args.modo} semantica={args.semantica} data={args.data} '
          f'tp={args.tp} sl={args.sl} holding={args.holding}s purge={args.purge}s')
    label_antes = carregar_legado() if args.semantica in ('ambas', 'antes') else None
    if label_antes is not None:
        print('[v15.30-A/B] labeler ANTES extraido do HEAD (semantica por linhas).')

    ativos = [args.ativo] if args.ativo else ATIVOS
    relatorio = {'data': args.data, 'params': vars(args), 'ativos': {}}

    for asset in ativos:
        px, ts, qtd, at = carregar_ativo(asset, args.data)
        if len(px) == 0:
            print(f'{asset}: sem dados'); continue
        print(f'\n{"=" * 72}\n{asset}: {len(px):,} eventos | '
              f'{datetime.fromtimestamp(ts.min()/1000, BRT):%H:%M:%S} -> '
              f'{datetime.fromtimestamp(ts.max()/1000, BRT):%H:%M:%S}')
        rel_asset = {'janelas': {}}
        janelas = janelas_do_ativo(ts, qtd, args.modo)
        for nome, m in janelas:
            p2, t2, a2 = px[m], ts[m], at[m]
            if args.semantica != 'depois':
                t0 = time.time()
                ra = rodar_semantica(label_antes, p2, t2, a2, args.tp, args.sl,
                                     args.holding, args.purge, args.tick)
                dt_antes = time.time() - t0
            else:
                ra = None
                dt_antes = 0.0
            t0 = time.time()
            rd = rodar_semantica(label_depois, p2, t2, a2, args.tp, args.sl,
                                 args.holding, args.purge, args.tick)
            dt_depois = time.time() - t0
            sa = resumir(ra) if ra is not None else None
            sd = resumir(rd)

            # matriz de transicao ANTES(linha) x DEPOIS(coluna)
            mapa = {1: 'TP', -1: 'SL', 0: 'TIMEOUT'}
            trans = {}
            mudou = 0
            shift_dur = None
            if ra is not None:
                for la in (1, -1, 0):
                    for ld in (1, -1, 0):
                        c = int(np.sum((ra['label'] == la) & (rd['label'] == ld)
                                       & ~ra['ambiguous'] & ~rd['ambiguous']))
                        if c:
                            trans[f'{mapa[la]}->{mapa[ld]}'] = c
                mudou = int(np.sum((ra['label'] != rd['label'])
                                   & ~ra['ambiguous'] & ~rd['ambiguous']))
                # shift de duracao entre os que NAO mudaram de outcome
                iguais = (ra['label'] == rd['label']) & ~ra['ambiguous'] & ~rd['ambiguous']
            else:
                iguais = None
            if iguais is not None and iguais.sum() > 0:
                shift_dur = {
                    'media_ms': round(float((rd['duracao_ms'][iguais]
                                             - ra['duracao_ms'][iguais]).mean()), 1),
                    'p50_ms': int(np.median(rd['duracao_ms'][iguais]
                                            - ra['duracao_ms'][iguais])),
                    'p90_ms': int(np.percentile(rd['duracao_ms'][iguais]
                                                - ra['duracao_ms'][iguais], 90)),
                }
            rel_asset['janelas'][nome] = {
                'n_eventos': int(len(p2)),
                'antes': sa, 'depois': sd,
                'mudaram_outcome': mudou if ra is not None else None,
                'pct_mudaram': (round(100 * mudou / len(p2), 2)
                                if ra is not None else None),
                'transicao': trans if ra is not None else None,
                'shift_duracao_iguais': shift_dur if ra is not None else None,
                'seg_antes': round(dt_antes, 1),
                'seg_depois': round(dt_depois, 1),
            }

            print(f'\n--- janela {nome} ({len(p2):,} eventos) '
                  f'[antes {dt_antes:.1f}s | depois {dt_depois:.1f}s] ---')
            if sa is not None:
                print(f'  ANTES : TP {sa["pct_TP"]:6.2f}%  SL {sa["pct_SL"]:6.2f}%  '
                      f'TIMEOUT {sa["pct_TIMEOUT"]:6.2f}%  | dur TP {sa["dur_media_ms_TP"]:8.0f}ms  '
                      f'dur SL {sa["dur_media_ms_SL"]:8.0f}ms  dur TO {sa["dur_media_ms_TIMEOUT"]:8.0f}ms')
            print(f'  DEPOIS: TP {sd["pct_TP"]:6.2f}%  SL {sd["pct_SL"]:6.2f}%  '
                  f'TIMEOUT {sd["pct_TIMEOUT"]:6.2f}%  | dur TP {sd["dur_media_ms_TP"]:8.0f}ms  '
                  f'dur SL {sd["dur_media_ms_SL"]:8.0f}ms  dur TO {sd["dur_media_ms_TIMEOUT"]:8.0f}ms')
            if trans:
                print(f'  transicoes: {json.dumps(trans)}')
            if shift_dur:
                print(f'  duracao (mesmo outcome): media {shift_dur["media_ms"]}ms '
                      f'p50 {shift_dur["p50_ms"]}ms p90 {shift_dur["p90_ms"]}ms')

            # persiste labels da janela (concatenado por ativo no fim)
            out_ant = OUT_BASE / 'labels_v1530_legado' / f'labels_{asset}_{args.data}.jsonl'
            out_dep = OUT_BASE / 'labels_v1530' / f'labels_{asset}_{args.data}.jsonl'
            if args.modo == 'full':
                if ra is not None:
                    salvar_labels(ra, out_ant)
                salvar_labels(rd, out_dep)
                print(f'  labels salvos: {out_dep}')

        # agrega por ativo (amostra: soma das janelas; full: janela unica)
        if True:
            tot = {k: 0 for k in rel_asset['janelas']}
            agg_antes = {k: 0 for k in ('TP', 'SL', 'TIMEOUT', 'AMBIGUO')}
            agg_depois = dict(agg_antes)
            ntot = 0
            for nome, j in rel_asset['janelas'].items():
                ntot += j['n_eventos']
                for k in agg_antes:
                    if j['antes'] is not None:
                        agg_antes[k] += j['antes'][k]
                    agg_depois[k] += j['depois'][k]
            if ntot:
                rel_asset['agregado'] = {
                    'n_eventos': ntot,
                    'antes': ({k: {'n': v, 'pct': round(100 * v / ntot, 2)}
                               for k, v in agg_antes.items()}
                              if agg_antes.get('TP', 0) or agg_antes.get('SL', 0)
                              or agg_antes.get('TIMEOUT', 0) else None),
                    'depois': {k: {'n': v, 'pct': round(100 * v / ntot, 2)}
                               for k, v in agg_depois.items()},
                }
                print(f'\n=== AGREGADO {asset} ({ntot:,} eventos) ===')
                if agg_antes.get('TP', 0) or agg_antes.get('SL', 0) \
                        or agg_antes.get('TIMEOUT', 0):
                    print(f'  ANTES : TP {100*agg_antes["TP"]/ntot:6.2f}%  SL '
                          f'{100*agg_antes["SL"]/ntot:6.2f}%  TIMEOUT '
                          f'{100*agg_antes["TIMEOUT"]/ntot:6.2f}%')
                print(f'  DEPOIS: TP {100*agg_depois["TP"]/ntot:6.2f}%  SL '
                      f'{100*agg_depois["SL"]/ntot:6.2f}%  TIMEOUT '
                      f'{100*agg_depois["TIMEOUT"]/ntot:6.2f}%')
        relatorio['ativos'][asset] = rel_asset

    out_json = OUT_BASE / 'labels_v1530' / f'relatorio_ab_{args.data}_{args.modo}_purge{args.purge}.json'
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)
    print(f'\n[v15.30-A/B] relatorio salvo: {out_json}')


if __name__ == '__main__':
    main()
