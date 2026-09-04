"""
calcular_ajuste_diario.py — Cálculo do ajuste diário B3 para WIN e WDO.

REGRAS DE CAUSALIDADE (CRÍTICO):
  - O ajuste é calculado APÓS o encerramento da janela oficial B3.
  - Só pode ser usado como `ajuste_anterior` em pregões D+1.
  - Nunca o ajuste de D pode aparecer em features de D.

METODOLOGIA (parametrizada por contrato, NÃO hardcoded):
  - WIN:  17:00:00 <= ts <= 17:15:00  -> ajuste = sum(preco*qtd) / sum(qtd)
  - WDO:  15:50:00 <= ts <= 16:00:00  -> ajuste = sum(preco*qtd) / sum(qtd)

DEDUPLICAÇÃO:
  - `event_id` é único por negócio. Usado como chave canônica.
  - Se `event_id` ausente, fallback em tupla (timestamp, preco, quantidade, simbolo).
  - Eventos com `quantidade <= 0` ou `preco <= 0` são descartados (registrados em stats).

ORIGEM DOS DADOS:
  - RAW_BASE/ano=YYYY/mes=MM/dia=DD/sym=<contrato>/tipo=TT/*.parquet
  - Particionado por hora; cada arquivo cobre HH:00:00.000 - HH:59:59.999.

PRECISÃO TEMPORAL:
  - `time_ms` = epoch ms (UTC).
  - `timestamp_brt` = datetime64[ns] (America/Sao_Paulo).
  - Janela aplicada em horário local BRT.

USO:
  df_ajuste = calcular_ajuste_multi_dias(RAW_BASE, [('WINV26', ('17:00','17:15')), ...])
  df_ajuste -> ['data_pregao', 'contrato', 'janela_inicio', 'janela_fim',
                 'ajuste', 'n_negocios', 'volume', 'valor_total',
                 'min_ts', 'max_ts', 'fonte', 'event_ids_dedup', 'volume_invalido']
"""

import os
import json
import glob
from datetime import datetime, time as dtime, timedelta
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


# ============================================================
#   CONFIG
# ============================================================
RAW_BASE_DEFAULT = r'D:\MarketData\mimo\RAW'

# Janelas default (parametrizaveis via argumento)
JANELAS_DEFAULT = {
    'WINV26':  ('17:00:00', '17:15:00'),
    'WING26':  ('17:00:00', '17:15:00'),
    'WINQ26':  ('17:00:00', '17:15:00'),
    'WDOV26':  ('15:50:00', '16:00:00'),
    'WDOG26':  ('15:50:00', '16:00:00'),
    'WDOU26':  ('15:50:00', '16:00:00'),
    'WDOQ26':  ('15:50:00', '16:00:00'),
}

COLS_TT = ['ts_ns', 'ativo', 'preco', 'quantidade']


# ============================================================
#   LEITURA PARTICIONADA
# ============================================================
def listar_dias(ano, mes, raw_base=RAW_BASE_DEFAULT):
    """Retorna lista ordenada de dias disponíveis para (ano, mes) na
    estrutura Hive nova (RAW/data_type=TT/date=YYYYMMDD/asset=X)."""
    base = os.path.join(raw_base, 'data_type=TT')
    if not os.path.isdir(base):
        return []
    prefixo = f'{ano:04d}{mes:02d}'
    dias = set()
    for d in sorted(os.listdir(base)):
        if d.startswith('date=') and len(d) == 13 and d[5:11] == prefixo:
            try:
                dias.add(int(d[11:13]))
            except ValueError:
                pass
    return sorted(dias)


def listar_arquivos_contrato(ano, mes, dia, contrato, raw_base=RAW_BASE_DEFAULT):
    """Lista arquivos parquet de T&T para um contrato/dia (estrutura Hive).
    As partições Hive usam o PREFIXO do ativo (asset=WIN, asset=WDO, ...) e
    não o contrato completo (WINV26). Prefere TT_LIMPO (deduplicado, sem
    reemissoes) quando existe; fallback RAW."""
    date_str = f'{ano:04d}{mes:02d}{dia:02d}'
    asset_part = contrato[:3].upper()  # 'WINV26' -> 'WIN'
    # 1) TT_LIMPO (limpo) em LIMPO/data_type=TT_LIMPO
    base_limpo = os.path.join(os.path.dirname(raw_base), 'LIMPO',
                              'data_type=TT_LIMPO', f'date={date_str}',
                              f'asset={asset_part}')
    if os.path.isdir(base_limpo):
        return sorted(glob.glob(os.path.join(base_limpo, '*.parquet')))
    # 2) Fallback RAW (asset=WIN e também WIN_RLP — pega só o prefixo exato)
    base_raw = os.path.join(raw_base, 'data_type=TT', f'date={date_str}',
                            f'asset={asset_part}')
    if os.path.isdir(base_raw):
        return sorted(glob.glob(os.path.join(base_raw, '*.parquet')))
    return []


def carregar_negocios(ano, mes, dia, contrato, raw_base=RAW_BASE_DEFAULT,
                      exclude_backup=True):
    """Carrega todos os negócios de um contrato em um dia, deduplicados.
    Retorna DataFrame vazio se não houver dados.
    Exclui arquivos em _backup_colapso/ (não confiáveis) por padrão.
    """
    arquivos = listar_arquivos_contrato(ano, mes, dia, contrato, raw_base)
    if exclude_backup:
        arquivos = [a for a in arquivos if '_backup_colapso' not in a]

    if not arquivos:
        return pd.DataFrame(columns=['time_ms', 'timestamp_brt', 'simbolo',
                                     'preco', 'quantidade'])

    parts = []
    for a in arquivos:
        t = pq.read_table(a, columns=COLS_TT)
        parts.append(t.to_pandas())
    df = pd.concat(parts, ignore_index=True)

    # v15.35: schema Hive novo — ts_ns (ns epoch) em vez de time_ms,
    # ativo em vez de simbolo, sem event_id.
    df['time_ms'] = (df['ts_ns'] // 1_000_000).astype('int64')
    df['timestamp_brt'] = (pd.to_datetime(df['ts_ns'], unit='ns', utc=True)
                           .dt.tz_convert('America/Sao_Paulo')
                           .dt.tz_localize(None))
    df['simbolo'] = df['ativo']

    # Dedup SÓ no fallback RAW (que tem reemissoes da janela T&T). O
    # TT_LIMPO já vem deduplicado na origem preservando rajadas legítimas
    # (mesmo ts/preco/qtd com received_at_ns distintos) — dedup adicional
    # aqui colapsaria rajadas reais.
    if arquivos and '_LIMPO' in str(arquivos[0]):
        pass  # fonte limpa — não deduplicar
    else:
        df = df.drop_duplicates(subset=['time_ms', 'preco', 'quantidade', 'simbolo'],
                                keep='first')

    # descartar inválidos
    df = df[(df['quantidade'] > 0) & (df['preco'] > 0)]
    df = df.sort_values('time_ms').reset_index(drop=True)
    return df[['time_ms', 'timestamp_brt', 'simbolo', 'preco', 'quantidade']]


# ============================================================
#   CÁLCULO DO AJUSTE
# ============================================================
def _hms_para_segundos(s):
    """'17:00:00' -> 61200."""
    h, m, sec = s.split(':')
    return int(h) * 3600 + int(m) * 60 + int(sec)


def calcular_ajuste_contrato_dia(ano, mes, dia, contrato,
                                 janela_inicio='17:00:00',
                                 janela_fim='17:15:00',
                                 raw_base=RAW_BASE_DEFAULT):
    """Calcula o ajuste de um contrato em um dia.
    Retorna dict com: data_pregao, contrato, ajuste, n_negocios, volume,
    valor_total, min_ts, max_ts, fonte.
    """
    df = carregar_negocios(ano, mes, dia, contrato, raw_base)
    if df.empty:
        return {
            'data_pregao': f'{ano:04d}-{mes:02d}-{dia:02d}',
            'contrato': contrato,
            'janela_inicio': janela_inicio,
            'janela_fim': janela_fim,
            'ajuste': np.nan,
            'n_negocios': 0,
            'volume': 0,
            'valor_total': 0.0,
            'min_ts': pd.NaT,
            'max_ts': pd.NaT,
            'fonte': f'RAW{ano}{mes:02d}{dia:02d}_{contrato}',
        }

    ts_ini = _hms_para_segundos(janela_inicio)
    ts_fim = _hms_para_segundos(janela_fim)
    tod = df['timestamp_brt'].dt.hour * 3600 + df['timestamp_brt'].dt.minute * 60 + df['timestamp_brt'].dt.second
    mask = (tod >= ts_ini) & (tod <= ts_fim)
    sub = df[mask].copy()
    if sub.empty:
        return {
            'data_pregao': f'{ano:04d}-{mes:02d}-{dia:02d}',
            'contrato': contrato,
            'janela_inicio': janela_inicio,
            'janela_fim': janela_fim,
            'ajuste': np.nan,
            'n_negocios': 0,
            'volume': 0,
            'valor_total': 0.0,
            'min_ts': pd.NaT,
            'max_ts': pd.NaT,
            'fonte': f'RAW{ano}{mes:02d}{dia:02d}_{contrato}',
        }

    volume = int(sub['quantidade'].sum())
    valor_total = float((sub['preco'] * sub['quantidade']).sum())
    ajuste = valor_total / volume if volume > 0 else np.nan

    return {
        'data_pregao': f'{ano:04d}-{mes:02d}-{dia:02d}',
        'contrato': contrato,
        'janela_inicio': janela_inicio,
        'janela_fim': janela_fim,
        'ajuste': ajuste,
        'n_negocios': int(len(sub)),
        'volume': volume,
        'valor_total': valor_total,
        'min_ts': sub['timestamp_brt'].min(),
        'max_ts': sub['timestamp_brt'].max(),
        'fonte': f'RAW{ano}{mes:02d}{dia:02d}_{contrato}',
    }


def calcular_ajuste_multi_dias(contratos, ano, mes, dias=None,
                                janelas=None, raw_base=RAW_BASE_DEFAULT,
                                incluir_backup=False):
    """Calcula ajuste para varios contratos/dias. Retorna DataFrame.
    contratos: list de strings (ex.: ['WINV26', 'WDOU26'])
    dias: list de ints (None = todos os dias disponíveis)
    janelas: dict {contrato: (inicio, fim)} (None = JANELAS_DEFAULT)
    """
    if janelas is None:
        janelas = JANELAS_DEFAULT
    if dias is None:
        dias = listar_dias(ano, mes, raw_base)
    if not dias:
        return pd.DataFrame()

    rows = []
    for contrato in contratos:
        ji, jf = janelas.get(contrato, ('17:00:00', '17:15:00'))
        for dia in dias:
            row = calcular_ajuste_contrato_dia(ano, mes, dia, contrato,
                                              ji, jf, raw_base)
            rows.append(row)
    return pd.DataFrame(rows)


# ============================================================
#   AUDITORIA DE LOOK-AHEAD (ajuste_anterior SEMPRE = D-1)
# ============================================================
def ajuste_anterior(df_ajuste, contrato):
    """Constrói a tabela de ajuste_anterior (D-1) respeitando causalidade.

    Para cada linha com data_pregao D, ajuste_anterior = ajuste de D-1 do mesmo
    contrato. Se D for o primeiro dia disponível, ajuste_anterior = NaN.
    """
    sub = df_ajuste[df_ajuste['contrato'] == contrato].copy()
    sub = sub.sort_values('data_pregao').reset_index(drop=True)
    sub['ajuste_anterior'] = sub['ajuste'].shift(1)
    sub['data_anterior'] = sub['data_pregao'].shift(1)
    return sub


if __name__ == '__main__':
    # Exemplo de uso
    df = calcular_ajuste_multi_dias(['WINV26', 'WDOU26'], 2026, 8)
    print(df.to_string())
    if not df.empty:
        out = 'ajuste_diario_202608.csv'
        df.to_csv(out, index=False)
        print(f'\nSalvo: {out}')
