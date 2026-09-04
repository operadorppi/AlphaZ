#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_processor.py — Processa raw_events gravados pelo captura_eventos_ms
e gera dataset de features em janelas de 100ms.

Pipeline:
  raw_negocios_ms_*.jsonl ──┐
                             ├──▶ GeradorJanelas (100ms) ──▶ features_100ms.jsonl
  raw_book_ms_*.jsonl ──────┘                               │
                                                            ├──▶ asof_join (WIN×WDO)
                                                            │
                                                            └──▶ labeler (triple barrier)
                                                                  │
                                                                  └──▶ dataset.parquet

Uso:
  python batch_processor.py --dia 20 --ativo WINV26 --ctx WDOU26
  python batch_processor.py --periodo 15-20 --ativo WINV26 --ctx WDOU26
  python batch_processor.py --arquivo raw_negocios_ms_20260820_xxx.jsonl
"""
import sys, os, json, argparse, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root
from features_lib import GeradorJanelas, asof_join_linhas

SAVE_DIR = os.environ.get("SINAL_RT_DIR", r"D:\MarketData\mimo")

# Faixas de preço por ativo (para filtrar dados inválidos)
FAIXAS_PRECO = {
    'WIN': (150000, 250000),
    'WDO': (4000, 8000),
    'IND': (150000, 250000),
    'DOL': (4000, 8000),
}


def obter_faixa(ativo):
    """Retorna a faixa de preço para o ativo."""
    prefix = ativo[:3].upper()
    return FAIXAS_PRECO.get(prefix, (0, float('inf')))


def carregar_negocios(arquivo):
    """Carrega negócios do arquivo JSONL."""
    negocios = []
    with open(arquivo, 'r', encoding='utf-8') as f:
        for linha in f:
            if linha.strip():
                negocios.append(json.loads(linha))
    return negocios


def carregar_book(arquivo):
    """Carrega book do arquivo JSONL."""
    book = []
    with open(arquivo, 'r', encoding='utf-8') as f:
        for linha in f:
            if linha.strip():
                book.append(json.loads(linha))
    return book


def processar_dia(ativo, ctx, data_file, SAVE_DIR, ativos_conhecidos=None):
    """Processa um dia específico. Retorna snapshots de TODOS os ativos conhecidos."""
    data_str = data_file.strftime('%Y%m%d')
    if ativos_conhecidos is None:
        ativos_conhecidos = [ativo] + ([ctx] if ctx else [])
    
    # v14: Buscar dados na estrutura Hive Parquet OU JSONL legado
    # v15.34: preferir o Hive LIMPO (TT_LIMPO — reemissoes removidas, rajadas
    # preservadas por received_at_ns) quando existir para o dia; fallback para
    # o RAW original. O RAW de dias gravados com o motor antigo contem 76-98%
    # de reemissoes — features calculadas sobre ele ficariam infladas.
    from adapters.file_storage import find_hive_files
    
    tt_files = find_hive_files(SAVE_DIR, dia_str=data_str, data_type='TT_LIMPO',
                               base_subdir='LIMPO')
    if not tt_files:
        tt_files = find_hive_files(SAVE_DIR, dia_str=data_str, data_type='TT')
    book_files = find_hive_files(SAVE_DIR, dia_str=data_str, data_type='BOOK_LIMPO',
                                 base_subdir='LIMPO')
    if not book_files:
        book_files = find_hive_files(SAVE_DIR, dia_str=data_str, data_type='BOOK')
    
    negocios = []
    if tt_files:
        import pyarrow.parquet as pq
        for tf in tt_files:
            try:
                table = pq.read_table(tf)
                df = table.to_pandas()
                for _, row in df.iterrows():
                    # v14.1: schema usa ts_ns e quantidade
                    ts_ns = row.get('ts_ns', row.get('ts_ms', 0))
                    ts_ms = ts_ns // 1_000_000 if ts_ns > 1e15 else ts_ns
                    negocios.append({
                        'ativo': row['ativo'], 'ts_ms': int(ts_ms),
                        'preco': float(row['preco']), 'qtd': int(row.get('quantidade', row.get('qtd', 0))),
                        'agressor': row['agressor'], 'compradora': row.get('compradora', ''),
                        'vendedora': row.get('vendedora', ''),
                    })
            except Exception as e:
                print(f'  Erro ao ler {tf}: {e}')
        fonte = 'TT_LIMPO (limpo)' if 'LIMPO' in str(tt_files[0]) else 'TT (RAW)'
        print(f'  Carregados {len(negocios)} negócios de {len(tt_files)} Parquet hive [{fonte}]')
    else:
        # Fallback: JSONL legado
        neg_files = sorted(Path(SAVE_DIR).glob(f'raw_negocios_ms_{data_str}*.jsonl'))
        if not neg_files:
            print(f'  Arquivos de negócios não encontrados para {data_str}')
            return []
        for nf in neg_files:
            negocios.extend(carregar_negocios(nf))
        print(f'  Carregados {len(negocios)} negócios de {len(neg_files)} JSONL legado')
    
    book = []
    if book_files:
        import pyarrow.parquet as pq
        for bf in book_files:
            try:
                table = pq.read_table(bf)
                df = table.to_pandas()
                for _, row in df.iterrows():
                    # v14.1: schema usa ts_ns e bid_vol_total/ask_vol_total
                    ts_ns = row.get('ts_ns', row.get('ts_ms', 0))
                    ts_ms = ts_ns // 1_000_000 if ts_ns > 1e15 else ts_ns
                    book.append({
                        'ativo': row['ativo'], 'ts_ms': int(ts_ms),
                        'bid_vol': int(row.get('bid_vol_total', row.get('bid_vol', 0))),
                        'ask_vol': int(row.get('ask_vol_total', row.get('ask_vol', 0))),
                    })
            except Exception as e:
                print(f'  Erro ao ler {bf}: {e}')
        print(f'  Carregados {len(book)} book snapshots de {len(book_files)} Parquet hive')
    else:
        book_files_jsonl = sorted(Path(SAVE_DIR).glob(f'raw_book_ms_{data_str}*.jsonl'))
        for bf in book_files_jsonl:
            book.extend(carregar_book(bf))
        if book:
            print(f'  Carregados {len(book)} book snapshots de {len(book_files_jsonl)} JSONL legado')
    
    # Processar com GeradorJanelas — fluxo ÚNICO cronológico (trades + book
    # intercalados por ts_ms via heapq.merge, sem materializar a lista toda).
    #
    # v15.35: ANTES, os negócios eram alimentados agrupados por arquivo
    # (todo o DOL, depois IND, WDO, WIN). Como o relógio de cortes de 100ms
    # avança a cada evento, o primeiro ativo (DOL, ordem alfabética dos
    # arquivos) consumia o dia inteiro e os demais ativos chegavam com
    # timestamps "no passado" — nunca cruzavam um corte e ficavam com ~0
    # snapshots. O book também era processado SÓ DEPOIS de toda a emissão,
    # então snap['book'] nunca era preenchido.
    gerador = GeradorJanelas(
        instrumentos=ativos_conhecidos,
        janela_ms=100, passo_ms=100
    )

    import heapq
    negocios.sort(key=lambda n: n['ts_ms'])
    book.sort(key=lambda b: b['ts_ms'])
    fluxo = heapq.merge(
        ((n['ts_ms'], 0, n) for n in negocios),   # 0 = trade (negócio)
        ((b['ts_ms'], 1, b) for b in book),       # 1 = book (estado antes do trade no mesmo ms)
    )

    snapshots = []
    contagem = {}
    for ts_ms, kind, payload in fluxo:
        if kind == 1:
            gerador.processar_book(payload['ativo'], payload['ts_ms'], payload)
            continue
        neg = payload
        novos = gerador.processar_evento(
            neg['ativo'], neg['ts_ms'], neg['preco'], neg['qtd'],
            neg['agressor'], neg.get('compradora', ''), neg.get('vendedora', '')
        )
        for ativo_n, snap_n in novos:
            snap_n['ativo'] = ativo_n
            if 'ts_ms' not in snap_n:
                snap_n['ts_ms'] = snap_n.get('time_ms', 0)
            # Filtrar: ativo conhecido + preço na faixa correta
            preco = snap_n.get('preco_ultimo', 0)
            faixa = obter_faixa(ativo_n)
            if ativo_n in ativos_conhecidos and faixa[0] <= preco <= faixa[1]:
                snapshots.append(snap_n)
                contagem[ativo_n] = contagem.get(ativo_n, 0) + 1

    resumo = ' | '.join(f'{a}: {c:,}' for a, c in sorted(contagem.items()))
    print(f'  Total: {len(snapshots):,} snapshots ({resumo})')
    return snapshots


def main():
    parser = argparse.ArgumentParser(description='Batch processor para raw_events')
    parser.add_argument('--ativo', default='WINV26', help='Ativo(s): WINV26 ou WINV26,INDV26,WDOU26,DOLU26')
    parser.add_argument('--ctx', default='', help='Ativo contexto (legacy; vazio = não adicionar ao nome do arquivo)')
    # v15.35: default vazio — antes, 'WDOU26' era sempre anexado a `ativos` e
    # entrava no nome do dataset (dataset_100ms_..._WDOU26_...) enquanto o
    # pipeline_diario procurava o arquivo sem o sufixo → passo 3 abortava.
    parser.add_argument('--dia', type=int, help='Dia do mês (ex: 20)')
    parser.add_argument('--periodo', help='Range de dias (ex: 15-20)')
    parser.add_argument('--arquivo', help='Arquivo específico para processar')
    parser.add_argument('--output', help='Caminho de saída')
    args = parser.parse_args()
    
    # Parse ativos: aceita 'WINV26' ou 'WINV26,INDV26,WDOU26,DOLU26'
    ativos = [a.strip() for a in args.ativo.split(',') if a.strip()]
    if args.ctx and args.ctx not in ativos:
        ativos.append(args.ctx)
    print(f'Ativos: {ativos}')

    if args.arquivo:
        print(f'Processando arquivo único: {args.arquivo}')
        negocios = carregar_negocios(args.arquivo)
        print(f'  Carregados {len(negocios)} negócios')
        
        gerador = GeradorJanelas(
            instrumentos=ativos,
            janela_ms=100, passo_ms=100
        )
        snapshots = []
        for neg in negocios:
            novos = gerador.processar_evento(
                neg['ativo'], neg['ts_ms'], neg['preco'], neg['qtd'],
                neg['agressor'], neg.get('compradora', ''), neg.get('vendedora', '')
            )
            for ativo_n, snap_n in novos:
                snap_n['ativo'] = ativo_n
                if 'ts_ms' not in snap_n:
                    snap_n['ts_ms'] = snap_n.get('time_ms', 0)
                # Filtro: só ativos conhecidos com preço válido
                preco = snap_n.get('preco_ultimo', 0)
                faixa = obter_faixa(ativo_n)
                if ativo_n in ativos and faixa[0] <= preco <= faixa[1]:
                    snapshots.append(snap_n)
        
        out_path = Path(SAVE_DIR) / f'dataset_100ms_{'_'.join(ativos)}_{Path(args.arquivo).stem}.jsonl'
        with open(out_path, 'w', encoding='utf-8') as f:
            for s in snapshots:
                f.write(json.dumps(s, ensure_ascii=False, default=str) + '\n')
        print(f'  Salvo: {out_path}')
        return
    
    # Determinar dias para processar
    hoje = date.today()
    mes = hoje.month
    
    # Pipeline diário: processa apenas o dia especificado
    # Formato: --periodo 28-28 (dia-dia)
    if args.periodo:
        partes = args.periodo.split('-')
        if len(partes) == 2:
            try:
                inicio = int(partes[0])
                fim = int(partes[1])
                dias = range(inicio, fim + 1)
            except ValueError:
                # Se não conseguir converter, tentar como YYYYMMDD
                try:
                    dia = int(args.periodo)
                    dias = [dia]
                except ValueError:
                    print(f'Formato de período inválido: {args.periodo}')
                    return
        else:
            print(f'Formato de período inválido: {args.periodo} (esperado dia-dia)')
            return
    elif args.dia:
        dias = [args.dia]
    else:
        print('Especifique --dia ou --periodo')
        return
    
    todos_snapshots = []
    for dia in dias:
        data_file = hoje.replace(day=dia)
        print(f'\n=== Dia {data_file.strftime("%d/%m/%Y")}')
        snaps = processar_dia(ativos[0], None, data_file, SAVE_DIR, ativos_conhecidos=ativos)
        todos_snapshots.extend(snaps)
    
    # Salvar - usar formato YYYYMMDD para consistência com pipeline_diario
    ativo_str = '_'.join(ativos)
    if args.periodo and '-' in args.periodo:
        partes = args.periodo.split('-')
        if partes[0] == partes[1]:  # Single day like '28-28'
            periodo_str = data_file.strftime('%Y%m%d') if dias else partes[0]
        else:
            periodo_str = args.periodo
    else:
        periodo_str = data_file.strftime('%Y%m%d') if dias else str(args.dia)
    out_path = Path(SAVE_DIR) / f'dataset_100ms_{ativo_str}_{periodo_str}.jsonl'
    with open(out_path, 'w', encoding='utf-8') as f:
        for s in todos_snapshots:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + '\n')
    print(f'\n  Total salvo: {out_path} ({len(todos_snapshots)} linhas)')


if __name__ == '__main__':
    main()
