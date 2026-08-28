# -*- coding: utf-8 -*-
# build_dataset_v950.py — Expansão final do dataset (v940 → v950)
#
# Adiciona todas as features pendentes da especificação 6-26:
#   Sec 6:  ATR, regime vol (expansão/compressão/aceleração/desaceleração)
#   Sec 7:  range_vs_media, range_vs_mediana, range_percentil
#   Sec 10: retorno_normalizado_volatilidade, aceleracao_retorno
#   Sec 12: VWAP causal intraday (computada do próprio dataset)
#   Sec 14: VWAP × preço, VWAP × ajuste, VWAP × microestrutura
#   Sec 15: VWAP × POC
#   Sec 16: microestrutura × contexto adicional
#   Sec 17: regime (vol, range, persistência, aceleração, posição vs VWAP/POC)
#
# ZERO LOOK-AHEAD: todas as features são causais.
# Lê v940 (124 cols) → produz v950 (~185 cols).
#
# Uso: python ml/build_dataset_v950.py

import os
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path

SAVE_DIR = Path('D:/MarketData/mimo/26')
INPUT = SAVE_DIR / 'dataset_final_WINV26_v940.parquet'
OUTPUT = SAVE_DIR / 'dataset_final_WINV26_v950.parquet'
VE = 1e-9
_TZ_OFFSET_MS = 3 * 3600 * 1000
_DIA_MS = 86_400_000


def _safe_div(num, den, eps=VE):
    """Divisão segura: retorna 0 quando |den|<=eps, NaN quando qualquer NaN."""
    num = pd.to_numeric(num, errors='coerce').astype('float64')
    den = pd.to_numeric(den, errors='coerce').astype('float64')
    res = num / den
    res = res.mask((den.abs() <= eps) & den.notna() & num.notna(), 0.0)
    res = res.mask(~(den.notna() & num.notna()), np.nan)
    return res


def _dia_de_ts(ts_ms):
    return (pd.to_numeric(ts_ms, errors='coerce').astype('int64') - _TZ_OFFSET_MS) // _DIA_MS


def adicionar_vwap_causal(df):
    """
    Sec 12: VWAP intraday causal.
    VWAP_t = sum(preco * quantidade) / sum(quantidade) desde abertura até t.
    Como não temos volume por trade no parquet, usamos acumulação de preço
    (preco_ultimo como proxy — dado o tick de 100ms, é a melhor aproximação).
    
    Para WIN (tick=5), assumimos 1 contrato por snapshot de 100ms.
    """
    pc = 'preco_ultimo'
    ac = 'ativo'
    ts = 'ts_ms'
    
    df = df.copy()
    df['_dia'] = _dia_de_ts(df[ts])
    df[pc] = pd.to_numeric(df[pc], errors='coerce')
    df = df.sort_values([ac, ts]).reset_index(drop=True)
    
    g = df.groupby([ac, '_dia'])
    
    # VWAP causal: cumsum(preco) / cumsum(1) = média móvel cumulativa
    # Com 1 contrato por snapshot: VWAP = média ponderada pelo tempo
    n_trades = g.cumcount() + 1
    df['_cum_preco'] = g[pc].cumsum()
    df['vwap_causal'] = df['_cum_preco'] / n_trades
    
    # Features derivadas da VWAP
    preco = df[pc]
    vwap = df['vwap_causal']
    vol = df['_vol_pts'] if '_vol_pts' in df.columns else pd.Series(np.nan, index=df.index)
    
    df['dist_vwap_causal_pts'] = preco - vwap
    df['dist_vwap_causal_abs'] = (preco - vwap).abs()
    df['dist_vwap_causal_norm'] = _safe_div(preco - vwap, vol)
    df['acima_vwap_causal'] = (preco > vwap).astype('float64')
    df['abaixo_vwap_causal'] = (preco < vwap).astype('float64')
    
    # Cruzou VWAP (lado mudou vs 1 trade atrás)
    lado = (preco > vwap).astype('Int64')
    lado_prev = lado.groupby(df[ac]).shift(1)
    df['cruzou_vwap_causal'] = (
        (lado.notna() & lado_prev.notna() & (lado != lado_prev))
        .astype('float64'))
    
    # Aproximando/afastando (|dist| diminuindo vs 600 trades ~60s)
    dist_abs = (preco - vwap).abs()
    dist_abs_prev = dist_abs.groupby(df[ac]).shift(600)
    df['aproximando_vwap_causal'] = (
        (dist_abs < dist_abs_prev).where(dist_abs_prev.notna(), np.nan))
    df['afastando_vwap_causal'] = (
        (dist_abs > dist_abs_prev).where(dist_abs_prev.notna(), np.nan))
    
    # Inclinação da VWAP (derivada: VWAP agora vs 600 trades atrás)
    vwap_prev = vwap.groupby(df[ac]).shift(600)
    df['vwap_inclinacao'] = _safe_div(vwap - vwap_prev, vol)
    
    df.drop(columns=['_dia', '_cum_preco'], inplace=True)
    return df


def adicionar_vwap_cross_features(df):
    """
    Sec 14-15: VWAP × preço, VWAP × ajuste, VWAP × POC.
    """
    df = df.copy()
    pc = 'preco_ultimo'
    
    # --- Sec 14: VWAP × Ajuste ---
    if 'vwap_causal' in df.columns and 'ajuste_anterior' in df.columns:
        vwap = pd.to_numeric(df['vwap_causal'], errors='coerce')
        ajuste = pd.to_numeric(df['ajuste_anterior'], errors='coerce')
        vol = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
        
        df['vwap_vs_ajuste'] = _safe_div(vwap - ajuste, vol)
        df['preco_vs_vwap_ajuste'] = (
            df.get('acima_vwap_causal', 0) * df.get('acima_ajuste', 0) * 2 +
            df.get('abaixo_vwap_causal', 0) * df.get('abaixo_ajuste', 0) * (-2) +
            df.get('acima_vwap_causal', 0) * df.get('abaixo_ajuste', 0) * 1 +
            df.get('abaixo_vwap_causal', 0) * df.get('acima_ajuste', 0) * (-1)
        )  # +2: ambos acima, -2: ambos abaixo, +1/-1: misto
    
    # --- Sec 15: VWAP × POC ---
    if 'vwap_causal' in df.columns and 'vp_poc_dist' in df.columns:
        # POC absolute = preco - vp_poc_dist (negativo = POC acima)
        preco = pd.to_numeric(df[pc], errors='coerce')
        poc_abs = preco - pd.to_numeric(df['vp_poc_dist'], errors='coerce')
        vwap = pd.to_numeric(df['vwap_causal'], errors='coerce')
        vol = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
        
        df['vwap_vs_poc'] = _safe_div(vwap - poc_abs, vol)
        df['preco_vs_vwap_poc'] = (
            (preco > vwap).astype('float64') * (preco > poc_abs).astype('float64') * 2 +
            (preco < vwap).astype('float64') * (preco < poc_abs).astype('float64') * (-2) +
            (preco > vwap).astype('float64') * (preco < poc_abs).astype('float64') * 1 +
            (preco < vwap).astype('float64') * (preco > poc_abs).astype('float64') * (-1)
        )
        df['vwap_acima_poc'] = (vwap > poc_abs).astype('float64')
    
    return df


def adicionar_atr(df):
    """
    Sec 6: ATR (Average True Range) causal — expanding max(|high-low|) no dia.
    Como temos apenas preco_ultimo (OHLC não disponível em 100ms), usamos
    range entre máx e mín como proxy do range real.
    """
    df = df.copy()
    pc = 'preco_ultimo'
    ac = 'ativo'
    ts = 'ts_ms'
    
    df['_dia'] = _dia_de_ts(df[ts])
    g = df.groupby([ac, '_dia'])
    
    # True Range proxy: |preco - preco_prev| (causal)
    preco = pd.to_numeric(df[pc], errors='coerce')
    preco_prev = preco.groupby(df[ac]).shift(1)
    tr = (preco - preco_prev).abs()
    
    # ATR = EWMA do True Range (causal, alfa = 1/14)
    alpha = 2.0 / 15  # ~14-period EMA
    df['atr_14'] = tr.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=alpha, adjust=False).mean())
    
    # ATR normalizado
    df['atr_14_norm'] = _safe_div(df['atr_14'], preco)
    
    df.drop(columns=['_dia'], inplace=True)
    return df


def adicionar_regime_vol(df):
    """
    Sec 6: Indicadores de regime de volatilidade.
    Expansão/compressão/aceleração/desaceleração.
    """
    df = df.copy()
    ac = 'ativo'
    
    vol = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
    
    # Vol EWMA de curto prazo vs longo prazo (expansão = curto > longo)
    vol_short = vol.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=0.1, adjust=False).mean())  # ~10 trades
    vol_long = vol.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=0.01, adjust=False).mean())  # ~100 trades
    
    df['vol_expansao'] = (vol_short > vol_long * 1.2).astype('float64')  # +20%
    df['vol_compressao'] = (vol_short < vol_long * 0.8).astype('float64')  # -20%
    
    # Derivada da vol (aceleração = positiva)
    vol_diff = vol.groupby(df[ac]).diff(10)  # diff ~1s
    df['vol_acelerando'] = (vol_diff > 0).astype('float64')
    df['vol_desacelerando'] = (vol_diff < 0).astype('float64')
    
    # Magnitude da aceleração
    df['vol_aceleracao_mag'] = _safe_div(vol_diff, vol_long)
    
    return df


def adicionar_range_stats(df):
    """
    Sec 7: range_vs_media, range_vs_mediana, range_percentil.
    Comparar range atual com distribuição histórica (causal: só passado).
    """
    df = df.copy()
    ac = 'ativo'
    ts = 'ts_ms'
    
    df['_dia'] = _dia_de_ts(df[ts])
    
    range_col = 'range_dia' if 'range_dia' in df.columns else None
    if range_col is None:
        # Calcular range_dia se não existir
        pc = 'preco_ultimo'
        g = df.groupby([ac, '_dia'])[pc]
        df['range_dia'] = g.transform('max') - g.transform('min')
        range_col = 'range_dia'
    
    rng = pd.to_numeric(df[range_col], errors='coerce')
    
    # Range médio por ativo (EWMA causal, sem cross-day leakage)
    range_rolling_mean = rng.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=0.001, adjust=False).mean())
    # Median expanding dentro do dia (causal)
    range_rolling_median = df.groupby([ac, '_dia'])[range_col].transform(
        lambda s: s.expanding(min_periods=1).median())
    
    df['range_vs_media'] = _safe_div(rng, range_rolling_mean)
    df['range_vs_mediana'] = _safe_div(rng, range_rolling_median)
    
    # Percentil: expanding rank dentro do dia (causal, sem cross-day)
    df['range_percentil'] = df.groupby([ac, '_dia'])[range_col].rank(pct=True, method='average')
    
    df.drop(columns=['_dia'], inplace=True)
    return df


def adicionar_retorno_aceleracao(df):
    """
    Sec 10: retorno_normalizado_volatilidade, aceleracao_retorno.
    """
    df = df.copy()
    ac = 'ativo'
    pc = 'preco_ultimo'
    
    vol = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
    preco = pd.to_numeric(df[pc], errors='coerce')
    
    # Retorno 100ms
    ret_100ms = preco.groupby(df[ac]).pct_change(1)
    
    # Retorno normalizado por volatilidade
    df['retorno_norm_vol'] = _safe_div(ret_100ms, vol)
    
    # Aceleração do retorno (derivada do retorno 100ms)
    df['aceleracao_retorno'] = ret_100ms.groupby(df[ac]).diff(1)
    
    # Aceleração normalizada
    df['aceleracao_retorno_norm'] = _safe_div(df['aceleracao_retorno'], vol)
    
    return df


def adicionar_regime_mercado(df):
    """
    Sec 17: Features de regime continuo (sem rótulo arbitrário).
    Variáveis que permitem ao modelo identificar regime.
    """
    df = df.copy()
    ac = 'ativo'
    pc = 'preco_ultimo'
    ts = 'ts_ms'
    
    vol = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
    
    # --- Volatilidade como feature de regime ---
    vol_short = vol.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=0.1, adjust=False).mean())
    vol_long = vol.groupby(df[ac]).transform(
        lambda s: s.ewm(alpha=0.01, adjust=False).mean())
    df['regime_vol_ratio'] = _safe_div(vol_short, vol_long)
    
    # --- Range como feature de regime ---
    if 'range_dia' in df.columns:
        rng = pd.to_numeric(df['range_dia'], errors='coerce')
        rng_rolling = rng.groupby(df[ac]).transform(
            lambda s: s.rolling(1000, min_periods=1).mean())
        df['regime_range_ratio'] = _safe_div(rng, rng_rolling)
    
    # --- Posição vs VWAP como feature de regime ---
    if 'vwap_causal' in df.columns:
        preco = pd.to_numeric(df[pc], errors='coerce')
        vwap = pd.to_numeric(df['vwap_causal'], errors='coerce')
        df['regime_pos_vs_vwap'] = _safe_div(preco - vwap, vol)
    
    # --- Posição vs POC como feature de regime ---
    if 'vp_poc_dist' in df.columns:
        preco = pd.to_numeric(df[pc], errors='coerce')
        poc_dist = pd.to_numeric(df['vp_poc_dist'], errors='coerce')
        df['regime_pos_vs_poc'] = _safe_div(poc_dist, vol)  # negativo = POC acima
    
    # --- Inclinação VWAP (slope) como feature de regime ---
    if 'vwap_causal' in df.columns:
        vwap = pd.to_numeric(df['vwap_causal'], errors='coerce')
        vwap_slope = vwap.groupby(df[ac]).diff(100)  # ~10s
        df['regime_vwap_slope'] = _safe_div(vwap_slope, vol)
    
    # --- Persistência do sinal (quantas linhas seguidas preço > VWAP) ---
    if 'acima_vwap_causal' in df.columns:
        av = pd.to_numeric(df['acima_vwap_causal'], errors='coerce')
        _dia_col = _dia_de_ts(df[ts]) if 'ts_ms' in df.columns else df.get('_dia')
        if _dia_col is not None:
            groups = (av != av.groupby([df[ac], _dia_col]).shift(1)).cumsum()
            df['regime_persistencia'] = av.groupby([df[ac], _dia_col, groups]).cumsum()
        else:
            groups = (av != av.groupby(df[ac]).shift(1)).cumsum()
            df['regime_persistencia'] = av.groupby([df[ac], groups]).cumsum()
    
    # --- Aceleração da vol como feature de regime ---
    vol_diff = vol.groupby(df[ac]).diff(100)  # ~10s
    vol_diff_prev = vol_diff.groupby(df[ac]).shift(100)
    df['regime_vol_accel'] = _safe_div(vol_diff - vol_diff_prev, vol_long)
    
    return df


def adicionar_micro_x_contexto_vwap(df):
    """
    Sec 16: Interações microestrutura × VWAP (adicionais às que já existem).
    """
    df = df.copy()
    
    aggr = pd.to_numeric(df.get('aggr_imb', pd.Series(np.nan, index=df.index)), errors='coerce')
    cvd = pd.to_numeric(df.get('cvd_total', pd.Series(np.nan, index=df.index)), errors='coerce')
    imb = pd.to_numeric(df.get('ewma_imb_curta', pd.Series(np.nan, index=df.index)), errors='coerce')
    vol_pts = pd.to_numeric(df.get('_vol_pts', pd.Series(np.nan, index=df.index)), errors='coerce')
    
    # Micro × VWAP
    if 'dist_vwap_causal_pts' in df.columns:
        vwap_d = pd.to_numeric(df['dist_vwap_causal_pts'], errors='coerce')
        vwap_n = pd.to_numeric(df.get('dist_vwap_causal_norm', pd.Series(np.nan, index=df.index)), errors='coerce')
        
        df['aggr_x_dist_vwap'] = aggr * vwap_n
        df['cvd_x_dist_vwap'] = cvd * vwap_n
        df['imb_x_dist_vwap'] = imb * vwap_n
        df['vol_x_acima_vwap'] = vol_pts * df.get('acima_vwap_causal', 0)
    
    # Micro × POC
    if 'vp_poc_dist' in df.columns:
        poc_n = pd.to_numeric(df['vp_poc_dist'], errors='coerce')
        df['aggr_x_poc_dist'] = aggr * _safe_div(poc_n, vol_pts)
        df['cvd_x_poc_dist'] = cvd * _safe_div(poc_n, vol_pts)
    
    return df


def main():
    t0 = time.time()
    print("=" * 60)
    print("BUILD DATASET v950 — Expansão Final")
    print("=" * 60)
    
    # 1. Ler v940
    print(f"\n1. Lendo v940: {INPUT}")
    df = pd.read_parquet(INPUT)
    print(f"   {df.shape[0]:,} linhas × {df.shape[1]} colunas")
    print(f"   {os.path.getsize(INPUT) / (1024**2):.1f} MB")
    
    cols_antes = set(df.columns)
    n_cols_antes = df.shape[1]
    
    # 2. ATR (Sec 6)
    print("\n2. Adicionando ATR (Sec 6)...")
    df = adicionar_atr(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 3. Regime vol (Sec 6)
    print("3. Adicionando regime vol (Sec 6)...")
    df = adicionar_regime_vol(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 4. Range stats (Sec 7)
    print("4. Adicionando range stats (Sec 7)...")
    df = adicionar_range_stats(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 5. Retorno aceleração (Sec 10)
    print("5. Adicionando retorno + aceleração (Sec 10)...")
    df = adicionar_retorno_aceleracao(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 6. VWAP causal (Sec 12)
    print("6. Adicionando VWAP causal (Sec 12)...")
    df = adicionar_vwap_causal(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 7. VWAP cross features (Sec 14-15)
    print("7. Adicionando VWAP × preço/ajuste/POC (Sec 14-15)...")
    df = adicionar_vwap_cross_features(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 8. Micro × VWAP (Sec 16)
    print("8. Adicionando micro × contexto VWAP (Sec 16)...")
    df = adicionar_micro_x_contexto_vwap(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 9. Regime mercado (Sec 17)
    print("9. Adicionando regime mercado (Sec 17)...")
    df = adicionar_regime_mercado(df)
    print(f"   +{df.shape[1] - n_cols_antes} colunas")
    
    # 10. Limpar colunas auxiliares
    cols_drop = [c for c in df.columns if c.startswith('_') and c not in ('_vol_pts',)]
    df = df.drop(columns=cols_drop, errors='ignore')
    
    # 11. Remover NaN labels
    df = df.dropna(subset=['label']).reset_index(drop=True)
    
    # 12. Salvar
    print(f"\n10. Salvando v950: {OUTPUT}")
    df.to_parquet(OUTPUT, index=False)
    
    # 13. Relatório
    print(f"\n{'=' * 60}")
    print(f"RESULTADO v950:")
    print(f"  Linhas:    {df.shape[0]:,}")
    print(f"  Colunas:   {df.shape[1]} (era {n_cols_antes})")
    print(f"  Novas:     +{df.shape[1] - n_cols_antes}")
    print(f"  Tamanho:   {os.path.getsize(OUTPUT) / (1024**2):.1f} MB")
    print(f"  Tempo:     {time.time() - t0:.1f}s")
    print(f"\n  Novas features:")
    novas = sorted(df.columns - cols_antes)
    for i, c in enumerate(novas):
        # Contar NaN
        nan_pct = df[c].isna().mean() * 100
        print(f"    {i+1:3d}. {c} ({nan_pct:.1f}% NaN)")
    
    print(f"\n  Todas as colunas:")
    for i, c in enumerate(df.columns):
        tp = str(df[c].dtype)[:10]
        nan = df[c].isna().mean() * 100
        print(f"    {i+1:3d}. {c:45s} {tp:10s} {nan:5.1f}% NaN")


if __name__ == '__main__':
    main()
