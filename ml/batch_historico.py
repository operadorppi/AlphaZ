# batch_historico.py — dataset de 1s a partir do histórico Parquet (RAWHISTORICO)
import argparse
import json
import pandas as pd
from pathlib import Path
from features_lib import GeradorJanelas

RAIZ = Path(r'D:\MarketData\Profit\RAWHISTORICO')
SAIDA = Path(r'D:\MarketData\mimo')

def carregar_dia(pasta_ativo, mm, dd):
    p = RAIZ / pasta_ativo / mm / dd / 'negocios.parquet'
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=['timestamp', 'preco', 'qtd', 'tipo', 'compradora', 'vendedora'])
    df = df[df['tipo'].isin(['Comprador', 'Vendedor'])]   # corta Leilão
    df = df[(df['preco'] > 0) & (df['qtd'] > 0)]
    df = df.sort_values('timestamp', kind='mergesort')
    return df

def eventos_do_dia(df, tag):
    ts   = (df['timestamp'].astype('int64') // 10**6).tolist()
    pr   = df['preco'].tolist()
    qtd  = df['qtd'].tolist()
    agr  = df['tipo'].tolist()   # 'Comprador'/'Vendedor' = padrao do sistema (JanelaFeatures)
    comp = df['compradora'].astype(str).tolist()
    vend = df['vendedora'].astype(str).tolist()
    return list(zip([tag] * len(ts), ts, pr, qtd, agr, comp, vend))

def achatar(a, s):
    linha = {'ativo': a}
    for k, v in s.items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                linha[f'{k}_{kk}'] = vv
        elif v is None or isinstance(v, (int, float, str, bool)):
            linha[k] = v
    return linha

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ativo', default='WINV26')
    ap.add_argument('--ctx', default='WDOU26')
    ap.add_argument('--mm', default='08')
    ap.add_argument('--dias', default=None, help='ex: 04,05,06 (default: todos)')
    args = ap.parse_args()

    dias = args.dias.split(',') if args.dias else sorted(
        p.name for p in (RAIZ / args.ativo / args.mm).glob('*') if p.is_dir())

    out = SAIDA / f'dataset_1s_{args.ativo}_hist.jsonl'
    total = 0
    with open(out, 'w', encoding='utf-8') as fout:
        for dd in dias:
            df_a = carregar_dia(args.ativo, args.mm, dd)
            if df_a is None:
                print(f'[{dd}] sem dados, pulando'); continue
            df_c = carregar_dia(args.ctx, args.mm, dd) if args.ctx else None
            eventos = eventos_do_dia(df_a, args.ativo)
            if df_c is not None:
                eventos += eventos_do_dia(df_c, args.ctx)
            eventos.sort(key=lambda e: e[1])   # sort estável preserva ordem em ts iguais
            ger = GeradorJanelas(
                instrumentos=[args.ativo] + ([args.ctx] if args.ctx else []),
                janela_ms=5000, passo_ms=1000)   # janela de 5s, snapshot a cada 1s
            n = 0
            for tag, ts, pr, qtd, agr, comp, vend in eventos:
                for a, s in ger.processar_evento(tag, ts, pr, qtd, agr, comp, vend):
                    fout.write(json.dumps(achatar(a, s), ensure_ascii=False, default=str) + '\n')
                    n += 1
            total += n
            print(f'[{dd}] WIN {len(df_a)} trades | WDO {len(df_c) if df_c is not None else 0} | snapshots {n}')
    print(f'\nTotal: {total} snapshots -> {out}')

if __name__ == '__main__':
    main()