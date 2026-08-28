"""
integrar_base.py — Pipeline completo de integração dos indicadores de
contexto (ajuste oficial B3 + VWAP intraday + features de regime +
interações micro x contexto) ao dataset final do Freebuff.

Arquitetura:

    RAW parquet        ->  calcular_ajuste_diario    ->  ajuste_diario.csv
    RAW parquet        ->  calcular_vwap_diaria      ->  vwap_<periodo>.parquet
    dataset_final     ->  features_contexto_preco   ->  (proxy ajuste + maxima/minima)
    dataset_final     ->  features_contexto_avancado
                          - ajuste_oficial
                          - vwap
                          - interacoes
                          - regime                  ->  dataset_final_completo.parquet

Saída:
  - dataset_final_completo.parquet: dataset integrado com todas as camadas
  - ajuste_diario.csv: tabela de ajuste oficial (auditoria)
  - vwap_<periodo>.parquet: tabela de VWAP (auditoria)

USO:
  python integrar_base.py                              # mês atual
  python integrar_base.py --mes 202608                 # mês específico
  python integrar_base.py --mes 202608 --ativo WINV26  # ativo específico
"""
import os
import sys
import argparse
import time
import json
import numpy as np
import pandas as pd

# Tornar o pacote importável
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features_contexto_preco import adicionar_contexto_preco
from features_contexto_avancado import (
    adicionar_ajuste_oficial, adicionar_vwap_causal,
    adicionar_interacoes_micro_contexto, adicionar_features_regime,
)
from calcular_ajuste_diario import (
    calcular_ajuste_multi_dias, listar_dias, JANELAS_DEFAULT
)
from calcular_vwap_diaria import (
    carregar_negocios_brutos, calcular_vwap, listar_dias as listar_dias_vwap
)

# Config
RAW_BASE = r'D:\MarketData\Profit\RAW'
DATASET_BASE = r'D:\MarketData\mimo\26\dataset_final.parquet'
DATASET_COMPL = r'D:\MarketData\mimo\26\dataset_final_completo.parquet'
SAVE_DIR = r'D:\MarketData\mimo'
ATIVOS = ['WINV26', 'WDOU26']


def integrar(mes, ativos, ano=None, force=False):
    """Pipeline completo: RAW -> dataset enriquecido."""
    if ano is None:
        ano = int(str(mes)[:4])
    t0 = time.time()
    print('=' * 70)
    print(f'INTEGRAR BASE — {mes} ({", ".join(ativos)})')
    print('=' * 70)

    # 1. Carregar dataset base (microestrutura)
    if not os.path.exists(DATASET_BASE):
        sys.exit(f'[ERRO] dataset_base nao encontrado: {DATASET_BASE}')
    print(f'\n[1/5] Carregando dataset base: {DATASET_BASE}')
    df = pd.read_parquet(DATASET_BASE)
    print(f'  shape: {df.shape} (t={time.time()-t0:.1f}s)')

    # Filtrar ativos
    if 'ativo' in df.columns:
        df = df[df['ativo'].isin(ativos)].reset_index(drop=True)
        print(f'  apos filtro de ativos: {df.shape}')

    # 2. Contexto de preço base
    t0 = time.time()
    print(f'\n[2/5] Adicionando contexto de preço base (proxy de ajuste)')
    df = adicionar_contexto_preco(df)
    print(f'  shape: {df.shape} (t={time.time()-t0:.1f}s)')

    # criar _vol_pts (dependência)
    if '_vol_pts' not in df.columns:
        ret = df.groupby('ativo')['preco_ultimo'].diff().abs()
        df['_vol_pts'] = ret.groupby(df['ativo']).transform(
            lambda s: s.ewm(alpha=0.005, adjust=False).mean())

    # 3. Ajuste oficial B3 (do RAW particionado)
    t0 = time.time()
    print(f'\n[3/5] Calculando ajuste oficial B3 do RAW')
    dias = listar_dias(ano, mes)
    if not dias:
        print(f'  [WARN] Nenhum dia encontrado em {RAW_BASE}/ano={ano}/mes={mes:02d}/')
        print('  [WARN] Ajuste oficial nao foi calculado — usando apenas proxy')
        df_ajuste = pd.DataFrame()
    else:
        df_ajuste = calcular_ajuste_multi_dias(ativos, ano, mes, dias=dias)
        print(f'  {len(df_ajuste)} linhas de ajuste oficial (dias={dias})')
        # persistir para auditoria
        out_csv = os.path.join(SAVE_DIR, f'ajuste_diario_{ano}{mes:02d}.csv')
        df_ajuste.to_csv(out_csv, index=False)
        print(f'  Salvo: {out_csv}')
    print(f'  (t={time.time()-t0:.1f}s)')

    if not df_ajuste.empty:
        df = adicionar_ajuste_oficial(df, df_ajuste, usar_proxy_se_ausente=True)
        print(f'  shape: {df.shape}')

    # 4. VWAP intraday causal
    t0 = time.time()
    print(f'\n[4/5] Calculando VWAP intraday do RAW')
    if not dias:
        print(f'  [WARN] Nenhum dia RAW — VWAP nao calculado')
        df_vwap = pd.DataFrame()
    else:
        df_neg = carregar_negocios_brutos(ano, mes, dias, ativos)
        if df_neg.empty:
            print(f'  [WARN] Nenhum negocio no RAW')
            df_vwap = pd.DataFrame()
        else:
            df_vwap = calcular_vwap(df_neg)
            print(f'  {len(df_vwap):,} negocios processados')
            # persistir para auditoria
            out_pq = os.path.join(SAVE_DIR, f'vwap_{ano}{mes:02d}.parquet')
            df_vwap.to_parquet(out_pq, index=False)
            print(f'  Salvo: {out_pq}')
    print(f'  (t={time.time()-t0:.1f}s)')

    if not df_vwap.empty:
        df = adicionar_vwap_causal(df, df_vwap)
        print(f'  shape: {df.shape}')

    # 5. Interações e regime
    t0 = time.time()
    print(f'\n[5/5] Adicionando interações micro x contexto e regime')
    df = adicionar_interacoes_micro_contexto(df)
    print(f'  apos interacoes: {df.shape}')
    df = adicionar_features_regime(df)
    print(f'  apos regime: {df.shape} (t={time.time()-t0:.1f}s)')

    # drop auxiliar
    if '_dia' in df.columns:
        df = df.drop(columns=['_dia'])

    # Salvar
    out = DATASET_COMPL
    if force or not os.path.exists(out):
        df.to_parquet(out, index=False)
        print(f'\n[OK] Salvo: {out} (shape={df.shape})')
    else:
        backup = out + '.bak'
        if not os.path.exists(backup):
            import shutil
            shutil.copy(out, backup)
            print(f'  Backup criado: {backup}')
        df.to_parquet(out, index=False)
        print(f'\n[OK] Sobrescrito: {out} (shape={df.shape})')

    # Relatório de features adicionadas
    return df


def main():
    ap = argparse.ArgumentParser(
        description='Integra ajuste oficial B3 + VWAP intraday + '
                    'features de regime ao dataset final')
    ap.add_argument('--mes', type=int, default=None,
                    help='YYYYMM (default: mes atual)')
    ap.add_argument('--ano', type=int, default=None)
    ap.add_argument('--ativo', nargs='+', default=ATIVOS)
    ap.add_argument('--force', action='store_true',
                    help='Forca re-escrita do dataset final')
    args = ap.parse_args()

    if args.mes is None:
        from datetime import date
        hoje = date.today()
        mes = hoje.year * 100 + hoje.month
    else:
        mes = args.mes
    ano = args.ano if args.ano else int(str(mes)[:4])

    df = integrar(mes, args.ativo, ano=ano, force=args.force)
    if df is not None and not df.empty:
        # Resumo
        print('\n' + '=' * 70)
        print('RESUMO')
        print('=' * 70)
        ctx_cols = [c for c in df.columns
                    if any(k in c for k in
                          ('ajuste_oficial', 'vwap', 'dist_vwap',
                           'dist_ajuste_oficial', 'acima_', 'abaixo_',
                           'regime_', 'cruzou', 'aproximando',
                           'afastando', '_x_', 'inclinacao'))]
        print(f'  {len(ctx_cols)} colunas de contexto avançado adicionadas')
        print(f'  Total de colunas: {len(df.columns)}')
        # verificar NaNs nas colunas principais
        for c in ['ajuste_anterior_oficial', 'vwap', 'dist_ajuste_oficial_pts',
                  'dist_vwap_pts', 'regime_realiz_vol']:
            if c in df.columns:
                nan_pct = 100.0 * df[c].isna().sum() / max(len(df), 1)
                print(f'  {c}: {nan_pct:.1f}% NaN')


if __name__ == '__main__':
    main()
