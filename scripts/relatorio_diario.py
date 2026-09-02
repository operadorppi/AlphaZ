#!/usr/bin/env python3
"""
relatorio_diario.py — Qualidade dos dados capturados + resumo do dia (v9.10).

Para o modo "acumular dados": garante que o que está sendo capturado é
íntegro (gate de qualidade) e gera um relatório diário legível que
mostra quando há dados suficientes para retreinar com confiança.

Uso:
  python relatorio_diario.py                    # ontem (dia útil anterior)
  python relatorio_diario.py --dia 20260821     # dia específico
  python relatorio_diario.py --save-dir C:\\x    # outro diretório de captura

Gera relatorios_diarios/YYYYMMDD.md e retorna exit code:
  0 = dados OK | 1 = dados suspeitos (problemas encontrados)

A função validar_dia() também é usada como GATE no retreinar_sem_leak.py
(aborta o treino se os dados de algum dia estiverem com problema).
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from adapters.file_storage import find_hive_files

DEFAULT_SAVE_DIR = r'D:\MarketData\mimo'
MIN_TRADES_POR_DIA = 500
MAX_REJ_TS_ANTIGO_PCT = 0.5   # % dos negócios aceitos
MAX_REJ_DUP_PCT = 2.0
MIN_SPAN_HORAS = 4.0          # pregão mínimo esperado de cobertura


def dia_util_anterior(hoje=None):
    hoje = hoje or date.today()
    d = hoje - timedelta(days=1)
    while d.weekday() >= 5:  # pula fim de semana
        d -= timedelta(days=1)
    return d


def ultimos_dias_uteis(n=5, hoje=None):
    """Últimos n dias úteis como 'YYYYMMDD,YYYYMMDD,...' (para o gate)."""
    d = dia_util_anterior(hoje)
    dias = []
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d.strftime('%Y%m%d'))
        d -= timedelta(days=1)
    return ','.join(dias)


def _ler_negocios_hive(base, data_str):
    """Le negocios de Parquet Hive (RAW/data_type=TT/date=.../).
    Retorna (arquivos_neg, n_negocios, por_ativo, ts_min, ts_max, book_snapshots)."""
    from adapters.file_storage import find_hive_files
    tt_files = find_hive_files(str(base), dia_str=data_str, data_type='TT')
    book_files = find_hive_files(str(base), dia_str=data_str, data_type='BOOK')
    if not tt_files:
        return [], 0, {}, None, None, 0

    try:
        import pyarrow.parquet as pq
    except ImportError:
        return [], 0, {}, None, None, 0

    negocios = 0
    por_ativo = {}
    ts_min = None
    ts_max = None
    book_snaps = 0

    for tf in tt_files:
        try:
            t = pq.read_table(tf)
            rows = t.num_rows
            negocios += rows
            ativos = t.column('ativo').to_pylist()
            for a in ativos:
                por_ativo[a] = por_ativo.get(a, 0) + 1
            # ts_ns -> ts_ms
            ts_col = t.column('ts_ns').to_pylist()
            for ts_ns in ts_col:
                ts_ms = ts_ns // 1_000_000 if ts_ns and ts_ns > 1e15 else ts_ns
                if ts_ms:
                    ts_min = ts_ms if ts_min is None else min(ts_min, ts_ms)
                    ts_max = ts_ms if ts_max is None else max(ts_max, ts_ms)
        except Exception:
            continue

    for bf in book_files:
        try:
            book_snaps += pq.read_table(bf).num_rows
        except Exception:
            continue

    return tt_files, negocios, por_ativo, ts_min, ts_max, book_snaps


def validar_dia(save_dir, data_str):
    """Valida a captura de um dia (Hive Parquet ou JSONL legado).
    Retorna dict com metricas e lista de problemas."""
    base = Path(save_dir)
    problemas = []

    # v14: tentar Hive Parquet primeiro
    raw_tt = base / 'RAW' / 'data_type=TT'
    usa_hive = raw_tt.exists()

    if usa_hive:
        tt_files, negocios, por_ativo, ts_min, ts_max, book_snaps = \
            _ler_negocios_hive(base, data_str)
        neg_arquivos = tt_files
        book_arquivos = find_hive_files(str(base), dia_str=data_str, data_type='BOOK') if tt_files else []
        meta_arquivos = sorted(base.glob(f'raw_meta_*{data_str}*.json'))
    else:
        # Fallback: JSONL legado
        neg_arquivos = sorted(base.glob(f'raw_negocios_ms_*{data_str}*.jsonl'))
        book_arquivos = sorted(base.glob(f'raw_book_ms_*{data_str}*.jsonl'))
        meta_arquivos = sorted(base.glob(f'raw_meta_*{data_str}*.json'))
        negocios = 0
        por_ativo = {}
        ts_min = None
        ts_max = None
        book_snaps = 0

    info = {
        'data': data_str,
        'fonte': 'hive' if usa_hive else 'jsonl',
        'arquivos_negocios': len(neg_arquivos),
        'arquivos_book': len(book_arquivos),
        'arquivos_meta': len(meta_arquivos),
        'negocios': negocios,
        'book_snapshots': book_snaps,
        'por_ativo': por_ativo,
        'rejeitados': {'ts_futuro': 0, 'ts_antigo': 0, 'qtd': 0,
                       'preco': 0, 'dup': 0, 'overflow': 0},
        'ts_min': ts_min, 'ts_max': ts_max,
        'span_horas': None,
        'problemas': problemas,
    }

    if not neg_arquivos:
        problemas.append(f'sem arquivos de negocios para {data_str}')
        return info

    # Se JSONL, contar linha a linha (legado)
    if not usa_hive:
        for nf in neg_arquivos:
            with open(nf, encoding='utf-8') as fh:
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        r = json.loads(linha)
                    except Exception:
                        problemas.append(f'linha invalida em {nf.name}')
                        continue
                    negocios += 1
                    ativo = r.get('ativo', '?')
                    por_ativo[ativo] = por_ativo.get(ativo, 0) + 1
                    ts = r.get('ts_ms', 0)
                    if ts:
                        ts_min = ts if ts_min is None else min(ts_min, ts)
                        ts_max = ts if ts_max is None else max(ts_max, ts)
        info['negocios'] = negocios
        info['por_ativo'] = por_ativo
        info['ts_min'] = ts_min
        info['ts_max'] = ts_max

        for bf in book_arquivos:
            with open(bf, encoding='utf-8') as fh:
                info['book_snapshots'] += sum(1 for ln in fh if ln.strip())

    # Rejeitados vindos dos metadados das sessoes
    for mf in meta_arquivos:
        try:
            m = json.loads(mf.read_text(encoding='utf-8'))
            for k, v in m.get('rejeitados', {}).items():
                info['rejeitados'][k] = info['rejeitados'].get(k, 0) + int(v or 0)
        except Exception:
            problemas.append(f'meta ilegivel: {mf.name}')

    # ---- Regras de qualidade ----
    if info['negocios'] < MIN_TRADES_POR_DIA:
        problemas.append(f'poucos negocios: {info["negocios"]} < {MIN_TRADES_POR_DIA}')
    if info['rejeitados']['ts_antigo'] > max(50, info['negocios'] * MAX_REJ_TS_ANTIGO_PCT / 100):
        problemas.append(f'muitos rejeitados ts_antigo: {info["rejeitados"]["ts_antigo"]}')
    if info['rejeitados']['dup'] > max(100, info['negocios'] * MAX_REJ_DUP_PCT / 100):
        problemas.append(f'muitos duplicados: {info["rejeitados"]["dup"]}')
    if info['ts_min'] and info['ts_max']:
        span_h = (info['ts_max'] - info['ts_min']) / 3_600_000
        info['span_horas'] = round(span_h, 1)
        if span_h < MIN_SPAN_HORAS:
            problemas.append(f'span de tempo curto: {span_h:.1f}h < {MIN_SPAN_HORAS:.0f}h')

    return info


def gerar_relatorio(save_dir, data_str):
    """Valida o dia e grava relatorios_diarios/YYYYMMDD.md."""
    info = validar_dia(save_dir, data_str)
    rel_dir = Path(save_dir) / 'relatorios_diarios'
    rel_dir.mkdir(parents=True, exist_ok=True)

    linhas = [f'# Relatório de captura — {data_str}', '']
    linhas.append(f'- Negócios: **{info["negocios"]}** ({info["arquivos_negocios"]} arquivo(s))')
    linhas.append(f'- Book snapshots: {info["book_snapshots"]} ({info["arquivos_book"]} arquivo(s))')
    linhas.append(f'- Por ativo: ' + ', '.join(
        f'{k}={v}' for k, v in sorted(info['por_ativo'].items())) or 'n/d')
    linhas.append(f'- Span de tempo: {info["span_horas"] if info["span_horas"] is not None else "n/d"}h')
    linhas.append('- Rejeitados: ' + ', '.join(
        f'{k}={v}' for k, v in info['rejeitados'].items()))
    linhas.append('')
    linhas.append('## Problemas')
    if info['problemas']:
        for p in info['problemas']:
            linhas.append(f'- ⚠️ {p}')
    else:
        linhas.append('- ✅ Nenhum problema detectado')
    linhas.append('')

    out = rel_dir / f'{data_str}.md'
    out.write_text('\n'.join(linhas), encoding='utf-8')
    return info, out


def main():
    ap = argparse.ArgumentParser(description='Relatório/validação da captura')
    ap.add_argument('--dia', default=None, help='YYYYMMDD (default: ontem)')
    ap.add_argument('--save-dir', default=None)
    args = ap.parse_args()

    save_dir = args.save_dir or os.environ.get('SINAL_RT_DIR') or DEFAULT_SAVE_DIR
    dia = args.dia or dia_util_anterior().strftime('%Y%m%d')

    info, out = gerar_relatorio(save_dir, dia)
    print(f'Relatório: {out}')
    print(f'Negócios: {info["negocios"]} | Book: {info["book_snapshots"]} | '
          f'Span: {info["span_horas"]}h | Problemas: {len(info["problemas"])}')
    for p in info['problemas']:
        print(f'  - {p}')
    sys.exit(1 if info['problemas'] else 0)


if __name__ == '__main__':
    main()
