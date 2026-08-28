#!/usr/bin/env python3
"""
importar_historico.py — Importa Times & Trades históricos (CSV/Parquet) para
o formato do pipeline (raw_negocios_ms_YYYYMMDD_<sufixo>.jsonl).

Os dados de entrada (últimos 10 dias de WIN e WDO) têm a MESMA resolução
que o motor RTD (segundo) — por isso podem alimentar o pipeline inteiro:
batch_processor → labeler → dataset_builder → retreino / walk-forward.

Entrada esperada (por negócio):
  timestamp (horário de Brasília), preco, qtd, comprador, vendedor, agressor
  + identificador do ativo (WIN / WDO / WINV26 / ...).

Uso:
  python importar_historico.py --entrada C:\\dados\\negocios.parquet
  python importar_historico.py --entrada dados.csv --saida D:\\MarketData\\mimo
  python importar_historico.py --entrada dados.parquet --ativo-alvo WINV26

Também grava raw_meta_<data>_<sufixo>.json por dia, no formato do gate de
qualidade (relatorio_diario.validar_dia enxerga os dias importados).
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd  # usado no main() para agrupar por data

SAVE_DIR_DEFAULT = r'D:\MarketData\mimo'
SUFIXO_DEFAULT = 'HIST'
BATCH_SIZE = 500_000

# Sinônimos de coluna (normalizados: minúsculas, sem acento, _ no lugar de espaço)
COLUNAS = {
    'ts': ['timestamp', 'tempo', 'hora', 'data', 'time', 'datetime', 'ts',
           'hora_negocio', 'date_time', 'horario'],
    'ativo': ['ativo', 'sym', 'simbolo', 'symbol', 'instrument', 'codigo',
              'ticket', 'ativo_id'],
    'preco': ['preco', 'price', 'preco_negocio'],
    'qtd': ['qtd', 'quantidade', 'volume', 'qty', 'size', 'quant'],
    'agressor': ['agressor', 'lado', 'lado_agressor', 'side', 'tipo',
                 'agressor_side', 'compra_venda'],
    'comprador': ['comprador', 'compradora', 'buyer', 'compra'],
    'vendedor': ['vendedor', 'vendedora', 'seller', 'venda'],
}

FUSO_BR = None
try:
    from zoneinfo import ZoneInfo
    FUSO_BR = ZoneInfo('America/Sao_Paulo')
except Exception:
    FUSO_BR = None  # fallback: fuso local da máquina (Brasília)


def _norm(nome):
    """Normaliza nome de coluna para matching."""
    n = nome.lower().strip().replace(' ', '_').replace('-', '_')
    n = n.replace('ã', 'a').replace('á', 'a').replace('â', 'a').replace('à', 'a')
    n = n.replace('ç', 'c').replace('é', 'e').replace('ê', 'e').replace('í', 'i')
    n = n.replace('ó', 'o').replace('ô', 'o').replace('õ', 'o').replace('ú', 'u')
    return n


def resolver_colunas(colunas):
    """Mapeia as colunas reais para os nomes canônicos do pipeline."""
    mapa = {}
    for canonico, alternativas in COLUNAS.items():
        for alt in alternativas:
            if _norm(alt) in colunas:
                mapa[canonico] = colunas[_norm(alt)]
                break
    return mapa


def normalizar_lado(v):
    """'C'/'Compra'/1 → 'Comprador'; 'V'/'Venda'/0/-1 → 'Vendedor'.
    Valores desconhecidos (RLP, Leilão, Direto...) → '' (preserva o
    negócio no histórico sem contaminar o CVD)."""
    if v is None:
        return ''
    s = str(v).strip().lower()
    if s in ('c', 'compra', 'comprador', 'buy', 'buyer', '1'):
        return 'Comprador'
    if s in ('v', 'venda', 'vendedor', 'sell', 'seller', '-1', '0'):
        return 'Vendedor'
    return ''


def ts_para_epoch_ms(df, coluna):
    """Converte coluna de timestamp → epoch ms (assume Brasília se naive)."""
    import pandas as pd
    dt = pd.to_datetime(df[coluna], errors='coerce')
    # Força datetime64[ns] (pandas pode usar µs ou s dependendo da versão)
    if str(dt.dtype).startswith('datetime64') and not str(dt.dtype).startswith('datetime64[ns'):
        dt = dt.astype('datetime64[ns]')
    if dt.dt.tz is None:
        if FUSO_BR is not None:
            dt = dt.dt.tz_localize(FUSO_BR, ambiguous='infer', nonexistent='NaT')
        else:
            # fallback: UTC se não houver fuso — dados com data completa
            dt = dt.dt.tz_localize('UTC')
    else:
        if FUSO_BR is not None:
            dt = dt.dt.tz_convert(FUSO_BR)
    # epoch ms: ns → ms
    return (dt.astype('int64') // 1_000_000).astype('Int64')


def ler_parquet_chunks(caminho):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(caminho)
    n = pf.metadata.num_rows
    print(f'Parquet: {n:,} linhas | {len(pf.schema.names)} colunas')
    print(f'  Colunas: {pf.schema.names}')
    for batch in pf.iter_batches(batch_size=BATCH_SIZE):
        yield batch.to_pandas()


def ler_csv_chunks(caminho):
    import pandas as pd
    for chunk in pd.read_csv(caminho, chunksize=BATCH_SIZE, low_memory=False):
        yield chunk


def importar_arquivo(entrada, saida, sufixo, ativo_alvo, mapa_ativos):
    """Processa UM arquivo (parquet/csv) e grava raw_negocios_ms_<data>_<sufixo>.jsonl
    + raw_meta por dia. Retorna total de negócios."""
    ext = entrada.suffix.lower()
    if ext == '.parquet':
        chunks = ler_parquet_chunks(entrada)
    elif ext in ('.csv', '.txt'):
        chunks = ler_csv_chunks(entrada)
    else:
        print(f'[ERRO] Extensão não suportada: {ext} (use .parquet ou .csv)')
        return 0

    # buffer por data (YYYYMMDD) — append incremental preserva ordem (assumida)
    fps = {}          # data → arquivo aberto
    totais = {}       # data → {'n': int, 'por_ativo': {}}
    n_total = 0
    mapa = None

    try:
        for df in chunks:
            if df.empty:
                continue
            if mapa is None:
                mapa = resolver_colunas({_norm(c): c for c in df.columns})
                faltando = [k for k in ('ts', 'ativo', 'preco', 'qtd', 'agressor')
                            if k not in mapa]
                if faltando:
                    print(f'[ERRO] Colunas obrigatórias ausentes: {faltando}')
                    print(f'  Colunas disponíveis: {list(df.columns)}')
                    sys.exit(2)
                print(f'  Mapeamento: {mapa}')

            # Timestamp → epoch ms (Brasília)
            df = df.copy()
            df['_epoch_ms'] = ts_para_epoch_ms(df, mapa['ts'])
            df = df[df['_epoch_ms'].notna()]
            df = df[df[mapa['preco']].notna() & df[mapa['qtd']].notna()]
            if df.empty:
                continue

            # Símbolo: aplica mapa de ativos (WINFUT → WINV26 etc.).
            # Ativos fora do mapa (DI, IND...) são descartados.
            ativo_serie = df[mapa['ativo']].astype(str).str.strip()
            if mapa_ativos:
                ativo_serie = ativo_serie.map(lambda s: mapa_ativos.get(s, s))
                sel = ativo_serie.isin(set(mapa_ativos.values()))
                df = df[sel]
                if df.empty:
                    continue
                ativo_serie = ativo_serie[sel]
            if ativo_alvo:
                sel = ativo_serie == ativo_alvo
                df = df[sel]
                if df.empty:
                    continue
                ativo_serie = ativo_serie[sel]

            # Data local (Brasília) do negócio
            if FUSO_BR is not None:
                df['_data'] = (pd.to_datetime(df['_epoch_ms'].astype('int64'), unit='ms')
                               .dt.tz_localize('UTC').dt.tz_convert(FUSO_BR)
                               .dt.strftime('%Y%m%d'))
            else:
                # fallback: fuso local da máquina (assumido Brasília)
                df['_data'] = (pd.to_datetime(df['_epoch_ms'].astype('int64'), unit='ms')
                               .dt.strftime('%Y%m%d'))

            tem_comprador = 'comprador' in mapa
            tem_vendedor = 'vendedor' in mapa

            for data, grupo in df.groupby('_data', sort=False):
                if data not in fps:
                    fp = open(saida / f'raw_negocios_ms_{data}_{sufixo}.jsonl',
                              'a', encoding='utf-8')
                    fps[data] = fp
                    totais[data] = {'n': 0, 'por_ativo': {}}
                totais[data]['n'] += len(grupo)

                ts_l = grupo['_epoch_ms'].tolist()
                ativo_l = [str(x).strip() for x in ativo_serie.loc[grupo.index].tolist()]
                preco_l = [float(x) for x in grupo[mapa['preco']].tolist()]
                qtd_l = [float(x) for x in grupo[mapa['qtd']].tolist()]
                agr_l = [normalizar_lado(x) for x in grupo[mapa['agressor']].tolist()]
                comp_l = [str(x) for x in grupo[mapa['comprador']].tolist()] if tem_comprador else None
                vend_l = [str(x) for x in grupo[mapa['vendedor']].tolist()] if tem_vendedor else None

                for j in range(len(ts_l)):
                    ativo = ativo_l[j]
                    totais[data]['por_ativo'][ativo] = totais[data]['por_ativo'].get(ativo, 0) + 1
                    linha = {
                        'ts_ms': int(ts_l[j]),
                        'ativo': ativo,
                        'preco': preco_l[j],
                        'qtd': qtd_l[j],
                        'agressor': agr_l[j],
                    }
                    if comp_l is not None:
                        linha['compradora'] = comp_l[j]
                    if vend_l is not None:
                        linha['vendedora'] = vend_l[j]
                    fps[data].write(json.dumps(linha, ensure_ascii=False) + '\n')
            n_total += len(df)

        # Fecha arquivos e grava metadados por dia
        for data, fp in fps.items():
            fp.flush()
            os.fsync(fp.fileno())
            fp.close()
            t = totais[data]
            meta = {
                'session': f'{data}_{sufixo}',
                'origem': 'importacao_historica',
                'arquivo_origem': str(entrada),
                'inicio_epoch_ms': None, 'fim_epoch_ms': None,
                'negocios': t['n'],
                'negocios_por_ativo': t['por_ativo'],
                'book_snapshots': 0,
                'rejeitados': {'ts_futuro': 0, 'ts_antigo': 0, 'qtd': 0,
                               'preco': 0, 'dup': 0, 'overflow': 0},
            }
            (saida / f'raw_meta_{data}_{sufixo}.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{data}: {t["n"]:,} negócios | {t["por_ativo"]}')
        return n_total
    finally:
        for fp in fps.values():
            try:
                fp.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description='Importa T&T histórico para o pipeline')
    ap.add_argument('--entrada', required=True,
                    help='Caminho do parquet/csv OU diretório (processa todos)')
    ap.add_argument('--saida', default=None, help='Save dir (default D:\\MarketData\\mimo)')
    ap.add_argument('--sufixo', default=SUFIXO_DEFAULT, help='Sufixo dos arquivos')
    ap.add_argument('--ativo-alvo', default=None,
                    help='Se informado, mantém só negócios desse ativo (ex.: WINV26)')
    ap.add_argument('--mapa-ativos', default='WINFUT:WINV26,WDOFUT:WDOU26,DOLFUT:WDOU26,DOLU26:WDOU26',
                    help='Reescreve símbolos de mercado → símbolos do pipeline. '
                         'Vazio desativa a reescrita E o filtro de ativos.')
    args = ap.parse_args()

    # Mapa de símbolos (códigos de mercado da B3 → contrato com vencimento).
    # Se definido, ativos que não aparecem no mapa são descartados (ex.: DI, IND)
    mapa_ativos = {}
    if args.mapa_ativos:
        for par in args.mapa_ativos.split(','):
            if ':' in par:
                origem, destino = par.split(':', 1)
                mapa_ativos[origem.strip()] = destino.strip()

    entrada = Path(args.entrada)
    if not entrada.exists():
        print(f'[ERRO] Caminho não encontrado: {entrada}')
        sys.exit(1)
    saida = Path(args.saida or os.environ.get('SINAL_RT_DIR') or SAVE_DIR_DEFAULT)
    saida.mkdir(parents=True, exist_ok=True)

    if entrada.is_dir():
        arquivos = sorted(p for p in entrada.rglob('*')
                          if p.suffix.lower() in ('.parquet', '.csv')
                          and 'F_0_Trade' not in p.name)  # exportação quebrada do ProfitChart
        print(f'{len(arquivos)} arquivo(s) encontrados em {entrada}')
        if not arquivos:
            print('[ERRO] Nenhum .parquet/.csv no diretório')
            sys.exit(1)
        gt = 0
        for arq in arquivos:
            print(f'\n>>> {arq}')
            gt += importar_arquivo(arq, saida, args.sufixo, args.ativo_alvo, mapa_ativos)
        print(f'\n{"="*60}')
        print(f'Total importado: {gt:,} negócios')
        print(f'Destino: {saida}')
        print(f'Arquivos: raw_negocios_ms_<data>_{args.sufixo}.jsonl '
              f'(+ raw_meta correspondente por dia)')
        print('Próximo passo: pipeline_diario.py --skip-batch | '
              'walk_forward.py --dataset <dataset_final.parquet>')
    else:
        n = importar_arquivo(entrada, saida, args.sufixo, args.ativo_alvo, mapa_ativos)
        print(f'\nTotal importado: {n:,} negócios')
        print(f'Destino: {saida}')


if __name__ == '__main__':
    main()
