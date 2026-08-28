#!/usr/bin/env python3
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
from features_lib import GeradorJanelas, asof_join_linhas

SAVE_DIR = os.environ.get("SINAL_RT_DIR", r"D:\MarketData\mimo")


def carregar_negocios(arquivo):
    """Carrega negócios de um arquivo JSONL."""
    negocios = []
    with open(arquivo, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                neg = json.loads(line)
                negocios.append(neg)
            except json.JSONDecodeError:
                continue
    return negocios


def carregar_book(arquivo):
    """Carrega snapshots de book de um arquivo JSONL."""
    snapshots = []
    with open(arquivo, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
                snapshots.append(snap)
            except json.JSONDecodeError:
                continue
    return snapshots


def processar_dia(ativo, ctx, data_str, save_dir=SAVE_DIR):
    """Processa um dia completo: carrega events, roda Feature Engine,
    retorna lista de snapshots com features (a cada 100ms)."""
    base = Path(save_dir)
    
    # Encontrar arquivos do dia
    arquivos_neg = sorted(base.glob(f'raw_negocios_ms_*{data_str}*.jsonl'))
    arquivos_book = sorted(base.glob(f'raw_book_ms_*{data_str}*.jsonl'))
    
    if not arquivos_neg:
        print(f'  Nenhum arquivo de negócios encontrado para {data_str}')
        return []
    
    print(f'  Negócios: {len(arquivos_neg)} arquivo(s)')
    print(f'  Book: {len(arquivos_book)} arquivo(s)')
    
    # Carregar dados
    todos_neg = []
    for arq in arquivos_neg:
        todos_neg.extend(carregar_negocios(arq))
    todos_neg.sort(key=lambda x: x['ts_ms'])
    
    todos_book = []
    for arq in arquivos_book:
        todos_book.extend(carregar_book(arq))
    todos_book.sort(key=lambda x: x['ts_ms'])
    
    print(f'  Total negócios: {len(todos_neg)}')
    print(f'  Total book snapshots: {len(todos_book)}')
    
    if not todos_neg:
        return []
    
    # Criar GeradorJanelas
    instrumentos = list(set(n['ativo'] for n in todos_neg))
    if ctx and ctx not in instrumentos:
        instrumentos.append(ctx)
    
    gerador = GeradorJanelas(instrumentos, janela_ms=100, passo_ms=100)
    
    # Interlevar negócios e book snapshots por timestamp
    # Usar merge sort manual para manter ordenação
    neg_idx = 0
    book_idx = 0
    snapshots_100ms = []
    n_eventos = 0
    
    while neg_idx < len(todos_neg) or book_idx < len(todos_book):
        neg_ts = todos_neg[neg_idx]['ts_ms'] if neg_idx < len(todos_neg) else float('inf')
        book_ts = todos_book[book_idx]['ts_ms'] if book_idx < len(todos_book) else float('inf')
        
        if neg_ts <= book_ts:
            # Processar negócio
            neg = todos_neg[neg_idx]
            novos = gerador.processar_evento(
                neg['ativo'], neg['ts_ms'], neg['preco'], neg['qtd'],
                neg['agressor'], neg.get('compradora', ''), neg.get('vendedora', '')
            )
            for ativo_n, snap_n in novos:
                snap_n["ativo"] = ativo_n
                snapshots_100ms.append(snap_n)
            n_eventos += 1
            neg_idx += 1
        else:
            # Processar book snapshot
            book = todos_book[book_idx]
            # Se tem 'levels', passar como dict aninhado (BookLevelFeatures)
            if 'levels' in book:
                book_com_levels = {
                    'bid_vol': book['levels'].get('bid_vol', []),
                    'ask_vol': book['levels'].get('ask_vol', []),
                    'bid_preco': book['levels'].get('bid_preco', []),
                    'ask_preco': book['levels'].get('ask_preco', []),
                }
                gerador.processar_book(book['ativo'], book['ts_ms'], book_com_levels)
            else:
                gerador.processar_book(book['ativo'], book['ts_ms'], book)
            book_idx += 1
        
        if n_eventos % 10000 == 0 and n_eventos > 0:
            print(f'    {n_eventos}/{len(todos_neg)} eventos, {len(snapshots_100ms)} snapshots 100ms', end='\r')
    
    print(f'  Processados: {n_eventos} eventos -> {len(snapshots_100ms)} snapshots 100ms')
    
    return snapshots_100ms


def salvar_dataset(snapshots, output_path):
    """Salva snapshots como JSONL (compatível com pandas read_json)."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for snap in snapshots:
            f.write(json.dumps(snap, ensure_ascii=False, default=str) + '\n')
    print(f'  Dataset salvo: {output_path} ({len(snapshots)} linhas)')


def main():
    parser = argparse.ArgumentParser(description='Batch processor para raw_events')
    parser.add_argument('--ativo', default='WINV26', help='Ativo principal')
    parser.add_argument('--ctx', default='WDOU26', help='Ativo contexto')
    parser.add_argument('--dia', type=int, help='Dia do mês (ex: 20)')
    parser.add_argument('--periodo', help='Range de dias (ex: 15-20)')
    parser.add_argument('--arquivo', help='Arquivo específico para processar')
    parser.add_argument('--output', help='Caminho de saída')
    args = parser.parse_args()
    
    if args.arquivo:
        print(f'Processando arquivo único: {args.arquivo}')
        negocios = carregar_negocios(args.arquivo)
        print(f'  Carregados {len(negocios)} negócios')

        gerador = GeradorJanelas(
            instrumentos=[args.ativo, args.ctx] if args.ctx else [args.ativo],
            janela_ms=100, passo_ms=100
        )
        snapshots_100ms = []
        for i, neg in enumerate(negocios):
            novos = gerador.processar_evento(
                neg['ativo'], neg['ts_ms'], neg['preco'], neg['qtd'],
                neg['agressor'], neg.get('compradora', ''), neg.get('vendedora', '')
            )
            for ativo_n, snap_n in novos:
                snap_n['ativo'] = ativo_n
                snapshots_100ms.append(snap_n)
            if (i + 1) % 10000 == 0:
                print(f'    {i+1}/{len(negocios)} negócios, {len(snapshots_100ms)} snapshots', end='\r')

        print(f'\n  Total: {len(snapshots_100ms)} snapshots')

        out_path = Path(SAVE_DIR) / f'dataset_100ms_{args.ativo}_{Path(args.arquivo).stem}.jsonl'
        with open(out_path, 'w', encoding='utf-8') as f:
            for s in snapshots_100ms:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        print(f'  Salvo: {out_path}')
        return
    
    # Determinar dias para processar
    hoje = date.today()
    mes = hoje.month
    
    if args.periodo:
        inicio, fim = map(int, args.periodo.split('-'))
        dias = range(inicio, fim + 1)
    elif args.dia:
        dias = [args.dia]
    else:
        print('Especifique --dia ou --periodo')
        return
    
    todos_snapshots = []
    for dia in dias:
        data_str = f'{mes:02d}/{dia:02d}'
        data_file = f'{hoje.year}{mes:02d}{dia:02d}'
        print(f'\n=== Dia {data_str} ===')
        snaps = processar_dia(args.ativo, args.ctx, data_file)
        todos_snapshots.extend(snaps)
    
    if todos_snapshots:
        output = args.output or str(Path(SAVE_DIR) / f'dataset_100ms_{args.ativo}_{args.periodo or args.dia}.jsonl')
        salvar_dataset(todos_snapshots, output)
        
        # Resumo
        ativos = set(s.get('ativo', '?') for s in todos_snapshots)
        print(f'\n=== Resumo ===')
        print(f'  Snapshots: {len(todos_snapshots)}')
        print(f'  Ativos: {ativos}')
        print(f'  Duração: ~{len(todos_snapshots) * 0.1:.0f}s de dados')


if __name__ == '__main__':
    main()
