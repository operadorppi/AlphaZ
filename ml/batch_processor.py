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
    
    # Carregar negócios - buscar todos os arquivos do dia
    neg_files = sorted(Path(SAVE_DIR).glob(f'raw_negocios_ms_{data_str}*.jsonl'))
    if not neg_files:
        print(f'  Arquivos de negócios não encontrados para {data_str}')
        return []
    
    negocios = []
    for nf in neg_files:
        negocios.extend(carregar_negocios(nf))
    print(f'  Carregados {len(negocios)} negócios de {len(neg_files)} arquivos')
    
    # Carregar book - buscar todos os arquivos do dia
    book_files = sorted(Path(SAVE_DIR).glob(f'raw_book_ms_{data_str}*.jsonl'))
    book = []
    for bf in book_files:
        book.extend(carregar_book(bf))
    if book:
        print(f'  Carregados {len(book)} snapshots de book de {len(book_files)} arquivos')
    
    # Processar com GeradorJanelas
    gerador = GeradorJanelas(
        instrumentos=ativos_conhecidos,
        janela_ms=100, passo_ms=100
    )
    
    snapshots = []
    contagem = {}
    for neg in negocios:
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
    
    # Processar book separadamente
    for b in book:
        gerador.processar_book(b['ativo'], b['ts_ms'], b)
    
    resumo = ' | '.join(f'{a}: {c:,}' for a, c in sorted(contagem.items()))
    print(f'  Total: {len(snapshots):,} snapshots ({resumo})')
    return snapshots


def main():
    parser = argparse.ArgumentParser(description='Batch processor para raw_events')
    parser.add_argument('--ativo', default='WINV26', help='Ativo(s): WINV26 ou WINV26,INDV26,WDOU26,DOLU26')
    parser.add_argument('--ctx', default='WDOU26', help='Ativo contexto (legacy)')
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
