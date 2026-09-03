# -*- coding: utf-8 -*-
"""
scripts/limpar_raw_v1534.py — Limpeza do RAW T&T por identidade ESTAVEL
(ts+preco+qtd) preservando rajadas genuinas por received_at_ns.

Replica OFFLINE a semantica do dedup v15.34 do ProfitRTDAdapter:

  - Chave de identidade: (ts_ns, preco, quantidade) — trio DAT/PRE/QUL.
    compradora/vendedora/agressor NAO entram na chave (oscilam entre
    reemissoes da MESMA linha — medido no RAW 2026-09-03).
  - Rajada GENUINA: N linhas identicas (mesmo ts+preco+qtd) chegando no
    MESMO ciclo de RefreshData (received_at_ns proximos do 1o recebimento)
    sao N trades distintos -> N linhas preservadas.
  - Reemissao persistente: a mesma linha reentregue em ciclos posteriores
    (received_at_ns muito depois do 1o) -> descartada.

Politica de janela: dentro de cada grupo (ts_ns, preco, qtd), mantemos as
linhas cujo received_at_ns cai dentro de `janela_rajada_ms` do primeiro
recebimento do grupo. As linhas de ciclos seguintes (reentrega da janela
T&T persistente) sao removidas.

Uso:
    python scripts/limpar_raw_v1534.py [--data YYYYMMDD] [--ativo WIN,WDO,IND,DOL]
        [--janela-rajada-ms 1000] [--saida DIR] [--profit "IND:9127,WIN:0"]

Saida:
    <saida>/data_type=TT_LIMPO/date=YYYYMMDD/asset=WIN/part-000.parquet
        (particionamento Hive, schema identico ao RAW)
    <saida>/relatorio_limpeza_YYYYMMDD.json
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAW_BASE = r'D:/MarketData/mimo/RAW/data_type=TT'
SAIDA_DEFAULT = r'D:/MarketData/mimo/LIMPO'

COLUNAS_RAW = [
    'ts_ns', 'received_at_ns', 'sequence_id', 'ativo', 'asset_partition',
    'janela_id', 'window_name', 'is_rlp', 'preco', 'quantidade',
    'agressor', 'compradora', 'vendedora',
]
IDENTIDADE = ['ts_ns', 'preco', 'quantidade']
ASSINATURA = ['agressor', 'compradora', 'vendedora']


def achar_ultima_data() -> str:
    """Retorna a data YYYYMMDD mais recente disponivel no RAW."""
    datas = []
    for d in glob.glob(os.path.join(RAW_BASE, 'date=*')):
        nome = os.path.basename(d).replace('date=', '')
        if nome.isdigit() and len(nome) == 8:
            datas.append(nome)
    if not datas:
        raise SystemExit(f'Nenhum RAW encontrado em {RAW_BASE}')
    return max(datas)


def listar_ativos(data: str):
    ativos = []
    for d in glob.glob(os.path.join(RAW_BASE, f'date={data}', 'asset=*')):
        ativos.append(os.path.basename(d).replace('asset=', ''))
    return sorted(ativos)


def carregar_ativo(data: str, ativo: str) -> pd.DataFrame:
    padrao = os.path.join(RAW_BASE, f'date={data}', f'asset={ativo}', '*.parquet')
    arquivos = sorted(glob.glob(padrao))
    if not arquivos:
        raise FileNotFoundError(f'Sem arquivos para {ativo} em {padrao}')
    df = pd.read_parquet(arquivos)
    # normaliza colunas (caso algum fragmento tenha schema ligeiramente diferente)
    for c in COLUNAS_RAW:
        if c not in df.columns:
            df[c] = ''
    df = df[COLUNAS_RAW]
    return df, arquivos


def limpar_ativo(df: pd.DataFrame, janela_rajada_ms: int) -> dict:
    """Aplica a politica v15.34 e retorna (df_limpo, estatisticas)."""
    t0 = time.time()
    n_bruto = len(df)
    df = df[df['preco'].notna() & (df['preco'] > 0) & (df['quantidade'] > 0)].copy()
    n_valido = len(df)

    if n_valido == 0:
        return df, {
            'linhas_brutas': n_bruto, 'linhas_validas': 0, 'unicos': 0,
            'reemissoes_removidas': 0, 'rajadas_preservadas_extra': 0,
            'tempo_s': round(time.time() - t0, 2), 'grupos_identidade': 0,
        }

    # 1. grupo por identidade estavel (ts+preco+qtd)
    primeiro = df.groupby(IDENTIDADE, observed=True)['received_at_ns'].transform('min')
    janela_ns = janela_rajada_ms * 1_000_000
    # 2. mantem apenas o 1o cluster de recebimento (rajada do MESMO ciclo)
    mascara_1o_cluster = (df['received_at_ns'] - primeiro) <= janela_ns
    df_primeiro = df[mascara_1o_cluster]
    # 3. dentro do 1o cluster: dedup por assinatura completa (mesma linha
    #    entregue 2x no mesmo ciclo colapsa; contrapartes distintas = rajada)
    df_limpo = df_primeiro.drop_duplicates(
        subset=IDENTIDADE + ASSINATURA, keep='first').sort_values(
        ['received_at_ns', 'sequence_id']).reset_index(drop=True)

    n_reemissao = n_valido - len(df_primeiro)
    n_grupos = df[IDENTIDADE].drop_duplicates().shape[0]
    # linhas preservadas alem da 1a de cada grupo de identidade (rajada genuina
    # no 1o cluster: contrapartes diferentes no mesmo ms/refresh)
    n_rajada_extra = max(0, len(df_limpo) - n_grupos)

    return df_limpo, {
        'linhas_brutas': n_bruto,
        'linhas_validas': n_valido,
        'reemissoes_removidas': int(n_reemissao),
        'unicos': int(len(df_limpo)),
        'grupos_identidade': int(n_grupos),
        'rajadas_preservadas_extra': int(n_rajada_extra),
        'tempo_s': round(time.time() - t0, 2),
    }


def salvar_hive(df: pd.DataFrame, saida: str, data: str, ativo: str) -> str:
    dir_dest = os.path.join(saida, 'data_type=TT_LIMPO', f'date={data}', f'asset={ativo}')
    os.makedirs(dir_dest, exist_ok=True)
    destino = os.path.join(dir_dest, 'part-000.parquet')
    # escreve atomico: temporario + rename
    tmp = destino + f'.tmp{os.getpid()}'
    df.to_parquet(tmp, index=False)
    if os.path.exists(destino):
        os.remove(destino)
    os.replace(tmp, destino)
    return destino


def main():
    ap = argparse.ArgumentParser(description='Limpeza do RAW T&T (v15.34)')
    ap.add_argument('--data', default=None, help='YYYYMMDD (default: ultima disponivel)')
    ap.add_argument('--ativo', default=None, help='WIN,WDO,IND,DOL (default: todos)')
    ap.add_argument('--janela-rajada-ms', type=int, default=1000,
                    help='Janela (ms) do 1o cluster de recebimento (default 1000)')
    ap.add_argument('--saida', default=SAIDA_DEFAULT)
    ap.add_argument('--profit', default=None,
                    help='Contadores do Profit p/ comparacao: "IND:9127,WIN:6138000"')
    args = ap.parse_args()

    data = args.data or achar_ultima_data()
    ativos = args.ativo.split(',') if args.ativo else listar_ativos(data)
    profit = {}
    if args.profit:
        for par in args.profit.split(','):
            if ':' in par:
                k, v = par.split(':', 1)
                profit[k.strip()] = int(v)

    print(f'=== Limpeza RAW T&T v15.34 | data={data} | janela_rajada={args.janela_rajada_ms}ms ===')
    relatorio = {'data': data, 'janela_rajada_ms': args.janela_rajada_ms,
                 'ativos': {}, 'resumo': {}}
    for ativo in ativos:
        print(f'\n--- {ativo} ---')
        try:
            df, arquivos = carregar_ativo(data, ativo)
        except FileNotFoundError as e:
            print(f'  SKIP: {e}')
            continue
        df_limpo, stats = limpar_ativo(df, args.janela_rajada_ms)
        destino = salvar_hive(df_limpo, args.saida, data, ativo)
        stats['arquivos_entrada'] = len(arquivos)
        stats['destino'] = destino
        if ativo in profit:
            stats['profit'] = profit[ativo]
            stats['pct_vs_profit'] = round(stats['unicos'] / profit[ativo] * 100, 1) \
                if profit[ativo] else None
        if len(df_limpo):
            stats['ts_min'] = int(df_limpo['ts_ns'].min())
            stats['ts_max'] = int(df_limpo['ts_ns'].max())
        relatorio['ativos'][ativo] = stats
        print(f"  brutas={stats['linhas_brutas']:,} -> unicos={stats['unicos']:,} "
              f"(reemissoes removidas: {stats['reemissoes_removidas']:,}, "
              f"rajada extra preservada: {stats['rajadas_preservadas_extra']:,})")
        if 'pct_vs_profit' in stats:
            print(f"  vs Profit: {stats['profit']:,} -> {stats['pct_vs_profit']}%")
        print(f'  salvo: {destino}')

    # resumo
    total_bruto = sum(a['linhas_brutas'] for a in relatorio['ativos'].values())
    total_limpo = sum(a['unicos'] for a in relatorio['ativos'].values())
    relatorio['resumo'] = {
        'linhas_brutas_total': int(total_bruto),
        'unicos_total': int(total_limpo),
        'compressao': round(total_bruto / total_limpo, 2) if total_limpo else None,
    }
    caminho_rel = os.path.join(args.saida, f'relatorio_limpeza_{data}.json')
    os.makedirs(args.saida, exist_ok=True)
    with open(caminho_rel, 'w', encoding='utf-8') as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)
    print(f'\n=== RESUMO: {total_bruto:,} brutas -> {total_limpo:,} unicos '
          f'({relatorio["resumo"]["compressao"]}x) | relatorio: {caminho_rel} ===')


if __name__ == '__main__':
    main()