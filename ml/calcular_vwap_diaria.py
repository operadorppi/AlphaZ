"""
calcular_vwap_diaria.py — VWAP intraday causal para WIN e WDO.

DEFINIÇÃO CAUSAL (CRÍTICO):
  VWAP_t = sum(preco_i * quantidade_i) / sum(quantidade_i)
  somente para i tal que timestamp_i <= timestamp_t

  O VWAP em t NUNCA usa negócios após t. NUNCA.

CÁLCULO VETORIZADO:
  Para cada contrato/dia:
    pv = (preco * quantidade).cumsum()  por (contrato, dia)
    vol = quantidade.cumsum()            por (contrato, dia)
    vwap = pv / vol                      com piso contra /0
  Reset diário via groupby.

ORIGEM DOS DADOS:
  RAW_BASE/ano=YYYY/mes=MM/dia=DD/sym=<contrato>/tipo=TT/*.parquet

DEDUPLICAÇÃO:
  - `event_id` único (chave canônica)
  - fallback em (time_ms, preco, quantidade, simbolo)
  - quantidade <= 0 ou preco <= 0 -> descartados

SAÍDA:
  DataFrame com colunas: ts_ms, timestamp_brt, contrato, preco, quantidade,
  pv_acumulado, volume_acumulado, vwap, dist_vwap_pts
"""

import os
import glob
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from calcular_ajuste_diario import (
    RAW_BASE_DEFAULT, COLS_TT,
    listar_arquivos_contrato, listar_dias,
)


def carregar_negocios_brutos(ano, mes, dias, contratos,
                             raw_base=RAW_BASE_DEFAULT, exclude_backup=True):
    """Carrega todos os negocios brutos de varios dias/contratos.
    Retorna DataFrame unificado.
    """
    if not isinstance(contratos, (list, tuple)):
        contratos = [contratos]
    parts = []
    for contrato in contratos:
        for dia in dias:
            arquivos = listar_arquivos_contrato(ano, mes, dia, contrato, raw_base)
            if exclude_backup:
                arquivos = [a for a in arquivos if '_backup_colapso' not in a]
            for a in arquivos:
                t = pq.read_table(a, columns=COLS_TT)
                parts.append(t.to_pandas())
    if not parts:
        return pd.DataFrame(columns=COLS_TT)
    df = pd.concat(parts, ignore_index=True)
    # dedup
    if df['event_id'].notna().all():
        df = df.drop_duplicates(subset=['event_id'], keep='first')
    else:
        df = df.drop_duplicates(subset=['time_ms', 'preco', 'quantidade', 'simbolo'],
                                keep='first')
    # invalidos
    df = df[(df['quantidade'] > 0) & (df['preco'] > 0)]
    df = df.sort_values(['simbolo', 'time_ms']).reset_index(drop=True)
    return df


def calcular_vwap(df_negocios, contratos=None):
    """Calcula VWAP causal intraday por (contrato, dia).

    df_negocios: saida de carregar_negocios_brutos (com timestamp_brt).
    contratos: list (None = usar todos do df).
    """
    if df_negocios.empty:
        return pd.DataFrame()
    if contratos is not None:
        df_negocios = df_negocios[df_negocios['simbolo'].isin(contratos)]

    # chave de dia em horario BRT
    df = df_negocios.copy()
    df['_dia'] = df['timestamp_brt'].dt.normalize()  # Timestamp midnight
    df['_pv'] = df['preco'] * df['quantidade']

    # cumsum por (contrato, dia) — causal dentro do dia
    df['pv_acumulado'] = (df.groupby(['simbolo', '_dia'])['_pv']
                            .cumsum())
    df['volume_acumulado'] = (df.groupby(['simbolo', '_dia'])['quantidade']
                                .cumsum())

    # vwap com protecao (vol==0 -> NaN; first row depois de qty>0)
    vol = df['volume_acumulado'].replace(0, np.nan)
    df['vwap'] = (df['pv_acumulado'] / vol).astype('float64')
    df = df.drop(columns=['_pv'])

    # distancia em pontos (positivo = preco acima da VWAP)
    df['dist_vwap_pts'] = df['preco'] - df['vwap']

    # renomear para a interface de features_contexto_avancado:
    #   time_ms -> ts_ms
    #   simbolo -> contrato
    df = df.rename(columns={'time_ms': 'ts_ms', 'simbolo': 'contrato'})

    return df


# ============================================================
#   ATALHOS DE FEATURES DERIVADAS (para o features_contexto)
# ============================================================
def features_vwap_por_negocio(df_com_vwap, vol_pts_ewm=None):
    """Adiciona features derivadas a um df que já tem vwap.
    Produz DataFrame por timestamp, uma linha por negocio.
    """
    if df_com_vwap.empty:
        return df_com_vwap
    df = df_com_vwap.copy()
    df['acima_vwap'] = (df['preco'] > df['vwap']).astype('float64')
    df['abaixo_vwap'] = (df['preco'] < df['vwap']).astype('float64')
    df['dist_vwap_abs'] = df['dist_vwap_pts'].abs()
    # cruzamento: mudou de lado vs negocio anterior
    df['_lado'] = (df['dist_vwap_pts'] > 0).astype('Int64')
    df['_lado_prev'] = df.groupby('simbolo')['_lado'].shift(1)
    df['cruzou_vwap'] = ((df['_lado'].notna()) & (df['_lado_prev'].notna()) &
                         (df['_lado'] != df['_lado_prev'])).astype('float64')
    df = df.drop(columns=['_lado', '_lado_prev'])
    return df


def reduzir_vwap_para_ts(df_com_vwap, freq='1s'):
    """Agrega a VWAP por timestamp (freq='1s' ou '100ms') para casar com features.
    Retorna DataFrame com uma linha por (timestamp, contrato).
    """
    if df_com_vwap.empty:
        return pd.DataFrame()
    df = df_com_vwap.copy()
    df['ts_bucket'] = df['timestamp_brt'].dt.floor(freq)
    g = df.groupby(['simbolo', 'ts_bucket'])
    out = g.agg(
        vwap=('vwap', 'last'),
        volume_acumulado=('volume_acumulado', 'last'),
        pv_acumulado=('pv_acumulado', 'last'),
    ).reset_index().rename(columns={'ts_bucket': 'timestamp_brt',
                                     'simbolo': 'contrato'})
    return out


if __name__ == '__main__':
    dias = listar_dias(2026, 8)
    print('dias:', dias)
    df_neg = carregar_negocios_brutos(2026, 8, dias, ['WINV26', 'WDOU26'])
    print('negocios:', len(df_neg))
    if not df_neg.empty:
        t0 = __import__('time').time()
        df_vwap = calcular_vwap(df_neg)
        print(f'vwap em {__import__("time").time()-t0:.2f}s; linhas:', len(df_vwap))
        print(df_vwap[['timestamp_brt', 'simbolo', 'preco', 'quantidade',
                         'vwap', 'dist_vwap_pts']].head(8))
        print('...')
        print('ultimo do WINV26 no dia 14:')
        sub = df_vwap[(df_vwap['simbolo']=='WINV26') & (df_vwap['_dia']==pd.Timestamp('2026-08-14').date())]
        print(sub[['timestamp_brt','preco','vwap','dist_vwap_pts']].tail(3))
        df_vwap.to_parquet('vwap_diaria_202608.parquet', index=False)
        print('salvo vwap_diaria_202608.parquet')
