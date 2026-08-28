"""
features_contexto_avancado.py — Camada avançada de contexto de mercado.

COMPÕE-SE AO LADO de features_contexto_preco.py (que cuida do proxy
fechamento/ajuste). Este módulo adiciona:

  1) AJUSTE OFICIAL B3 (substitui o proxy quando a tabela oficial é fornecida)
     - dist_ajuste_oficial_pts / _norm
     - acima_ajuste_oficial / abaixo_ajuste_oficial
     - abertura_vs_ajuste_oficial_pts / _norm
     - retorno_em_relacao_ao_ajuste_oficial

  2) VWAP INTRADAY CAUSAL
     - dist_vwap_pts / _abs / _norm / _ticks (tick por contrato)
     - acima_vwap / abaixo_vwap
     - aproximando_vwap / afastando_vwap
     - cruzou_vwap (evento binário causal: lado mudou vs N trades atrás)

PRINCÍPIOS (zero look-ahead):
  - ajuste_anterior_oficial = ajuste de D-1 (calculado pós-fechamento)
  - VWAP só usa negócios <= t (cumsum dentro do dia, reset diário)
  - cruzou_vwap compara t com t-1 (sem olhar t+1)
  - ticks: WIN=5, WDO=0.5 (documentados; se houver novo contrato, ver
    contratos.TICK_POR_CONTRATO abaixo)
"""

import numpy as np
import pandas as pd

# ============================================================
#   TABELA DE TICKS (oficial, documentada)
# ============================================================
# WIN (mini-ibovespa): tick = 5 pontos
# WDO (mini-dólar):    tick = 0.5 pontos
# Fonte: home broker B3 / Profit. Se houver outro contrato, sobrescrever
# via parametro no adicionar_contexto_avancado.
TICK_POR_CONTRATO = {
    'WINV26': 5.0, 'WING26': 5.0, 'WINQ26': 5.0, 'WINM26': 5.0, 'WINJ26': 5.0,
    'WDOV26': 0.5, 'WDOG26': 0.5, 'WDOU26': 0.5, 'WDOQ26': 0.5, 'WDOM26': 0.5,
}

# EWMA alpha (consistente com features_contexto_preco)
_VOL_ALPHA = 0.005
_VOL_EPS = 1e-9
_RANGE_EPS = 1e-9

# janela (em linhas de 100ms) para "aproximando_vwap" / "afastando_vwap"
_VWAP_VEL_JANELA = 600  # ~60s


def _safe_div(num, den, eps=_VOL_EPS):
    num = pd.to_numeric(num, errors='coerce').astype('float64')
    den = pd.to_numeric(den, errors='coerce').astype('float64')
    res = num / den
    res = res.mask((den.abs() <= eps) & den.notna() & num.notna(), 0.0)
    res = res.mask(~(den.notna() & num.notna()), np.nan)
    return res


def _dia_brt(ts_ms):
    """Indice de dia (Brasil UTC-3) — mesmo criterio usado em features_contexto_preco
    e walk_forward_v914_limpo."""
    return (pd.to_numeric(ts_ms, errors='coerce').astype('int64') - 3 * 3600 * 1000) // 86_400_000


# ============================================================
#   VWAP (CAUSAL) — wrapper sobre o df de features
# ============================================================
def adicionar_vwap_causal(df, vwap_por_negocio, preco_col='preco_ultimo',
                          ts_col='ts_ms', ativo_col='ativo',
                          vol_col='_vol_pts', tick_col='tick'):
    """Adiciona features de VWAP ao DataFrame de features (100ms).

    vwap_por_negocio: DataFrame por timestamp de negocio, colunas
        [ts_ms, contrato, vwap, volume_acumulado, pv_acumulado]
        (saida de calcular_vwap_diaria.reduzir_vwap_por_negocio).

    Logica causal:
      - Faz merge por (ts_ms, contrato) — para cada snapshot de 100ms,
        usa a VWAP do negocio MAIS RECENTE com ts <= ts_ms.
      - Calcula: dist_vwap_pts, _abs, _norm, _ticks, acima/abaixo,
        aproximando/afastando, cruzou_vwap.
    """
    if vwap_por_negocio is None or vwap_por_negocio.empty:
        return df

    df = df.copy()

    # Preparar vwap: deduplicar para o ultimo valor por (ts_ms, contrato)
    vwap = vwap_por_negocio[['ts_ms', 'contrato', 'vwap']].copy()
    vwap = vwap.dropna(subset=['vwap'])
    vwap = vwap.drop_duplicates(subset=['ts_ms', 'contrato'], keep='last')
    vwap = vwap.rename(columns={'contrato': ativo_col})

    # Padronizar dtype da chave (pode haver StringDtype vs object)
    if ativo_col in vwap.columns:
        vwap[ativo_col] = vwap[ativo_col].astype(str)
        df[ativo_col] = df[ativo_col].astype(str)

    # Ordenar ambos por ts para asof merge (causal: direction='backward')
    df = df.sort_values(ts_col).reset_index(drop=True)
    vwap = vwap.sort_values(ts_col).reset_index(drop=True)

    # asof por (ativo) — pega o último vwap <= ts
    if ativo_col in vwap.columns:
        merged = pd.merge_asof(
            df, vwap[[ts_col, ativo_col, 'vwap']].sort_values(ts_col),
            on=ts_col, by=ativo_col, direction='backward'
        )
    else:
        merged = df

    df['vwap'] = merged['vwap'].values

    # tick do contrato — tentar inferir do simbolo (WIN*/WDO*)
    def _tick(s):
        if not s:
            return np.nan
        s = str(s).upper()
        if s in TICK_POR_CONTRATO:
            return TICK_POR_CONTRATO[s]
        if s.startswith('WIN'):
            return 5.0
        if s.startswith('WDO'):
            return 0.5
        return 0.5  # default conservador

    if tick_col in df.columns:
        df['_tick'] = pd.to_numeric(df[tick_col], errors='coerce')
    else:
        df['_tick'] = df[ativo_col].apply(_tick)

    preco = pd.to_numeric(df[preco_col], errors='coerce')
    vwap = pd.to_numeric(df['vwap'], errors='coerce')
    tick = pd.to_numeric(df['_tick'], errors='coerce')

    df['dist_vwap_pts'] = preco - vwap
    df['dist_vwap_abs'] = (preco - vwap).abs()
    vol = pd.to_numeric(df[vol_col], errors='coerce') if vol_col in df.columns else pd.Series(0.0, index=df.index)
    df['dist_vwap_norm'] = _safe_div(preco - vwap, vol)
    df['dist_vwap_ticks'] = _safe_div(preco - vwap, tick)

    df['acima_vwap'] = (preco > vwap).astype('float64')
    df['abaixo_vwap'] = (preco < vwap).astype('float64')

    # aproximando / afastando: |dist| diminuindo vs N trades atrás
    dist_abs = (preco - vwap).abs()
    dist_abs_prev = dist_abs.groupby(df[ativo_col]).shift(_VWAP_VEL_JANELA)
    df['aproximando_vwap'] = (
        (dist_abs < dist_abs_prev).where(dist_abs_prev.notna(), np.nan))
    df['afastando_vwap'] = (
        (dist_abs > dist_abs_prev).where(dist_abs_prev.notna(), np.nan))

    # cruzou_vwap: lado (acima/abaixo) mudou vs snapshot anterior
    lado = (preco > vwap).astype('Int64')
    lado_prev = lado.groupby(df[ativo_col]).shift(1)
    df['cruzou_vwap'] = (
        (lado.notna() & lado_prev.notna() & (lado != lado_prev))
        .astype('float64'))

    df = df.drop(columns=['_tick'], errors='ignore')
    return df


# ============================================================
#   AJUSTE OFICIAL (CAUSAL) — wrapper
# ============================================================
def adicionar_ajuste_oficial(df, ajuste_diario_df, ativo_col='ativo',
                              ts_col='ts_ms', preco_col='preco_ultimo',
                              vol_col='_vol_pts', abertura_col='abertura',
                              usar_proxy_se_ausente=True):
    """Adiciona features de ajuste oficial B3.

    ajuste_diario_df: DataFrame com colunas
        [data_pregao, contrato, ajuste, ...]
    (saida de calcular_ajuste_diario.calcular_ajuste_multi_dias).

    Adiciona:
      - ajuste_anterior_oficial: ajuste de D-1
      - abertura_anterior: primeiro preco de D-1 (calculado se nao existir)
      - dist_ajuste_oficial_pts / _norm
      - acima_ajuste_oficial / abaixo_ajuste_oficial
      - abertura_vs_ajuste_oficial_pts / _norm
      - retorno_em_relacao_ao_ajuste_oficial
    """
    if ajuste_diario_df is None or ajuste_diario_df.empty:
        return df

    df = df.copy()

    # calcular _dia e a chave
    df['_dia'] = _dia_brt(df[ts_col])

    # preparar a tabela: contrato -> dataframe com ajuste + abertura
    # construir referencia: para cada (contrato, dia) -> ajuste do DIA ANTERIOR
    # equivalente ao calcular_referencia_diaria mas com fonte = tabela oficial
    partes = []
    for contrato, sub in ajuste_diario_df.groupby('contrato'):
        sub = sub.sort_values('data_pregao').reset_index(drop=True)
        # o "anterior" para o dia D é o ajuste da linha D-1
        sub['ajuste_anterior'] = sub['ajuste'].shift(1)
        sub['abertura_anterior'] = sub['abertura'].shift(1) if 'abertura' in sub.columns else np.nan
        sub['contrato'] = contrato
        partes.append(sub)
    if not partes:
        return df
    ref = pd.concat(partes, ignore_index=True)

    # mapear (contrato, _dia) -> ajuste_anterior
    # _dia aqui é int (epoch days); data_pregao é str 'YYYY-MM-DD'
    # converter: epoch days -> date
    epoch_days = pd.to_numeric(df['_dia'], errors='coerce').astype('Int64')
    # epoch day 0 = 1970-01-01; usar Timestamp
    df['_data'] = pd.to_datetime(epoch_days, unit='D', origin='unix').dt.strftime('%Y-%m-%d')

    # para mapear "data D -> ajuste de D-1", basta usar 'data_pregao' = D-1 do df
    # isto é: para uma linha com data_pregao = D, o ajuste_anterior_oficial é o
    # valor de 'ajuste' onde ref.data_pregao = D-1
    # = ref['ajuste'] da linha cuja data_pregao == D-1 do df.
    # ==> construimos um dict data -> ajuste_anterior e outro data -> abertura_anterior
    # e usamos data_pregao do df (D) - 1 dia
    mapa_ajuste = {}
    mapa_abertura = {}
    for _, r in ref.iterrows():
        mapa_ajuste[r['data_pregao']] = r['ajuste_anterior']
        mapa_abertura[r['data_pregao']] = r.get('abertura_anterior', np.nan)

    df['_data_ant'] = (pd.to_datetime(df['_data']) - pd.Timedelta(days=1)).dt.strftime('%Y-%m-%d')
    df['ajuste_anterior_oficial'] = df['_data_ant'].map(mapa_ajuste)
    df['abertura_anterior_oficial'] = df['_data_ant'].map(mapa_abertura)

    # se nao houver tabela para o contrato (proxy ausente)
    if not usar_proxy_se_ausente:
        df = df.drop(columns=['_data', '_data_ant'])
        return df

    # se ajuste_oficial for NaN, cair pro proxy que vem do features_contexto_preco
    if 'ajuste_anterior' in df.columns:
        df['ajuste_anterior_oficial'] = df['ajuste_anterior_oficial'].fillna(df['ajuste_anterior'])

    # features de distancia
    preco = pd.to_numeric(df[preco_col], errors='coerce')
    ajuste = pd.to_numeric(df['ajuste_anterior_oficial'], errors='coerce')
    vol = pd.to_numeric(df[vol_col], errors='coerce') if vol_col in df.columns else pd.Series(0.0, index=df.index)

    df['dist_ajuste_oficial_pts'] = preco - ajuste
    df['dist_ajuste_oficial_abs'] = (preco - ajuste).abs()
    df['dist_ajuste_oficial_norm'] = _safe_div(preco - ajuste, vol)
    df['acima_ajuste_oficial'] = (preco > ajuste).astype('float64')
    df['abaixo_ajuste_oficial'] = (preco < ajuste).astype('float64')
    df['retorno_em_relacao_ao_ajuste_oficial'] = _safe_div(preco - ajuste, ajuste)

    # abertura do dia vs ajuste do dia
    if abertura_col in df.columns:
        abertura = pd.to_numeric(df[abertura_col], errors='coerce')
    else:
        # se nao houver abertura calculada, usar primeiro preco do dia
        abertura = df.groupby(['_dia'])[preco_col].transform('first')

    df['abertura_vs_ajuste_oficial_pts'] = abertura - ajuste
    df['abertura_vs_ajuste_oficial_norm'] = _safe_div(abertura - ajuste, vol)
    df['acima_ajuste_oficial_abertura'] = (abertura > ajuste).astype('float64')
    df['abaixo_ajuste_oficial_abertura'] = (abertura < ajuste).astype('float64')

    df = df.drop(columns=['_data', '_data_ant'], errors='ignore')
    return df


# ============================================================
#   INTERAÇÕES MICRO × CONTEXTO (item 14, 16)
# ============================================================
def adicionar_interacoes_micro_contexto(df):
    """Adiciona produtos economicamente motivados entre microestrutura
    e contexto (VWAP, ajuste, range). Cria apenas as features cujas
    entradas existem no df (robusto: nunca quebra o pipeline).

    Lista das interações (escolhidas com base no item 16 do pedido
    do usuário — não exaustiva; evita "milhares de combinações
    indiscriminadamente"):

      aggr_imb × dist_vwap_pts
      aggr_imb × dist_ajuste_oficial_pts
      aggr_imb × acima_vwap
      aggr_imb × acima_ajuste_oficial
      aggr_imb × posicao_range_dia  (se existir do features_contexto_preco)

      cvd_total × dist_vwap_pts
      cvd_total × dist_ajuste_oficial_pts
      cvd_total × acima_vwap
      cvd_total × acima_ajuste_oficial

      imb_L5 × dist_vwap_pts
      imb_L5 × dist_ajuste_oficial_pts

      realiz_vol_pts × acima_vwap
      realiz_vol_pts × acima_ajuste_oficial
    """
    df = df.copy()
    g_id = df['ativo'] if 'ativo' in df.columns else pd.Series('', index=df.index)

    aggr = df['aggr_imb'] if 'aggr_imb' in df.columns else None
    cvd = df['cvd_total'] if 'cvd_total' in df.columns else None
    imb = df['imb_L5'] if 'imb_L5' in df.columns else None
    vol = (df['_vol_pts'] if '_vol_pts' in df.columns
           else (df['vol_realizada_pts'] if 'vol_realizada_pts' in df.columns else None))

    vwap_d = df['dist_vwap_pts'] if 'dist_vwap_pts' in df.columns else None
    aju_d = df['dist_ajuste_oficial_pts'] if 'dist_ajuste_oficial_pts' in df.columns else None
    acima_vwap = df['acima_vwap'] if 'acima_vwap' in df.columns else None
    acima_ajuste = df['acima_ajuste_oficial'] if 'acima_ajuste_oficial' in df.columns else None
    pos_range = df['posicao_range_dia'] if 'posicao_range_dia' in df.columns else None

    # aggr_imb (pressão agressora) × contexto
    if aggr is not None and vwap_d is not None:
        df['aggr_x_dist_vwap'] = aggr * vwap_d
    if aggr is not None and aju_d is not None:
        df['aggr_x_dist_ajuste_oficial'] = aggr * aju_d
    if aggr is not None and acima_vwap is not None:
        df['aggr_x_acima_vwap'] = aggr * acima_vwap
    if aggr is not None and acima_ajuste is not None:
        df['aggr_x_acima_ajuste_oficial'] = aggr * acima_ajuste
    if aggr is not None and pos_range is not None:
        df['aggr_x_posicao_range_dia'] = aggr * pos_range

    # cvd_total (fluxo acumulado) × contexto
    if cvd is not None and vwap_d is not None:
        df['cvd_x_dist_vwap'] = cvd * vwap_d
    if cvd is not None and aju_d is not None:
        df['cvd_x_dist_ajuste_oficial'] = cvd * aju_d
    if cvd is not None and acima_vwap is not None:
        df['cvd_x_acima_vwap'] = cvd * acima_vwap
    if cvd is not None and acima_ajuste is not None:
        df['cvd_x_acima_ajuste_oficial'] = cvd * acima_ajuste

    # imbalance_book × contexto
    if imb is not None and vwap_d is not None:
        df['imb_x_dist_vwap'] = imb * vwap_d
    if imb is not None and aju_d is not None:
        df['imb_x_dist_ajuste_oficial'] = imb * aju_d

    # volatilidade realizada × contexto (item 14: absorcao × volatilidade)
    if vol is not None and acima_vwap is not None:
        df['vol_x_acima_vwap'] = vol * acima_vwap
    if vol is not None and acima_ajuste is not None:
        df['vol_x_acima_ajuste_oficial'] = vol * acima_ajuste

    return df


# ============================================================
#   FEATURES DE REGIME CONTÍNUO (item 17)
# ============================================================
def adicionar_features_regime(df, vol_alpha=0.005, persist_janela=600,
                                aceler_janela=300):
    """Adiciona features contínuas de regime (item 17).

    Fornece ao modelo as variáveis para identificar o regime sem rótulo
    arbitrário (item 17 do pedido). Cálculo vetorizado causal.

    Features:
      - vwap_inclinacao_1m: (vwap[t] - vwap[t-1min]) / vwap[t-1min] (causal)
      - vwap_inclinacao_5m: idem para 5 min
      - realiz_vol_bps: vol EWMA normalizada em bps
      - realiz_vol_zscore: z-score da vol vs EWMA média (vol de vol)
      - aggr_imb_persistencia: EWMA de aggr_imb (smoothed aggressor pressure)
      - cvd_aceleracao: (cvd[t] - cvd[t-30s]) / 30 (delta de delta)
      - range_dia_norm: (maxima_dia - minima_dia) / vol (faixa em unidades de vol)
      - posicao_vs_ajuste_norm: dist_ajuste / vol
    """
    df = df.copy()

    # 1. Inclinação da VWAP (item 17: "inclinacao_VWAP")
    if 'vwap' in df.columns:
        vwap = pd.to_numeric(df['vwap'], errors='coerce')
        # lag em ticks: 1 min = 600 ticks a 100ms, 5 min = 3000 ticks
        # o shift precisa ser dentro do grupo de ativo
        if 'ativo' in df.columns:
            vwap_lag1m = vwap.groupby(df['ativo']).shift(600)
            vwap_lag5m = vwap.groupby(df['ativo']).shift(3000)
        else:
            vwap_lag1m = vwap.shift(600)
            vwap_lag5m = vwap.shift(3000)
        # (vwap[t] - vwap[t-lag]) / vwap[t-lag] — pode dar NaN se vwap[t-lag]==0
        # mas VWAP sempre positivo (preco medio), entao divide-se normalmente
        df['vwap_inclinacao_1m'] = (vwap - vwap_lag1m) / vwap_lag1m
        df['vwap_inclinacao_5m'] = (vwap - vwap_lag5m) / vwap_lag5m

    # 2. Volatilidade realizada (item 17: "volatilidade")
    # EWMA de |retorno| (magnitude) — sempre >= 0
    if 'preco_ultimo' in df.columns:
        preco = pd.to_numeric(df['preco_ultimo'], errors='coerce')
        if 'ativo' in df.columns:
            ret_raw = preco.groupby(df['ativo']).diff()
            ret = ret_raw.abs().groupby(df['ativo']).transform(
                lambda s: s.ewm(alpha=vol_alpha, adjust=False).mean())
        else:
            ret = preco.diff().abs().ewm(alpha=vol_alpha, adjust=False).mean()
        df['regime_realiz_vol'] = ret
        df['regime_realiz_vol_bps'] = ret * 10000
        # z-score da vol: vol de vol (EWMA da vol EWMA)
        if 'ativo' in df.columns:
            vol_ewm = ret.groupby(df['ativo']).transform(
                lambda s: s.ewm(alpha=0.01, adjust=False).mean())
        else:
            vol_ewm = ret.ewm(alpha=0.01, adjust=False).mean()
        df['regime_vol_zscore'] = (ret - vol_ewm) / vol_ewm.replace(0, np.nan)

    # 3. Persistência da agressão (item 17: "persistencia")
    if 'aggr_imb' in df.columns:
        aggr = pd.to_numeric(df['aggr_imb'], errors='coerce')
        if 'ativo' in df.columns:
            df['regime_aggr_persistencia'] = aggr.groupby(df['ativo']).transform(
                lambda s: s.ewm(alpha=0.05, adjust=False).mean())
        else:
            df['regime_aggr_persistencia'] = aggr.ewm(alpha=0.05, adjust=False).mean()

    # 4. Aceleração do CVD (item 17: "aceleracao")
    # shift em ticks; cada tick = 100ms = 0.1s; lag em segundos = aceler_janela*0.1
    if 'cvd_total' in df.columns and 'ativo' in df.columns:
        cvd = pd.to_numeric(df['cvd_total'], errors='coerce')
        cvd_lag = cvd.groupby(df['ativo']).shift(aceler_janela)
        lag_s = aceler_janela * 0.1  # 100ms por tick
        df['regime_cvd_aceleracao'] = (cvd - cvd_lag) / lag_s  # pts/s

    # 5. Range do dia normalizado (item 17: "range" e "posicao_vs_VWAP")
    if 'maxima_dia' in df.columns and 'minima_dia' in df.columns:
        maxima = pd.to_numeric(df['maxima_dia'], errors='coerce')
        minima = pd.to_numeric(df['minima_dia'], errors='coerce')
        rng = maxima - minima
        # usa _vol_pts se existir, senao 0 (retorna NaN/0)
        vol_ref = df['_vol_pts'] if '_vol_pts' in df.columns else None
        if vol_ref is not None:
            df['regime_range_dia_norm'] = _safe_div(rng, vol_ref)

    # 6. Posição vs VWAP (item 17: "posicao_vs_VWAP" — contínuo)
    if 'dist_vwap_norm' in df.columns:
        df['regime_pos_vs_vwap'] = pd.to_numeric(df['dist_vwap_norm'], errors='coerce')

    # 7. Posição vs ajuste (item 17: "posicao_vs_ajuste" — contínuo)
    if 'dist_ajuste_oficial_norm' in df.columns:
        df['regime_pos_vs_ajuste'] = pd.to_numeric(
            df['dist_ajuste_oficial_norm'], errors='coerce')

    return df


# ============================================================
#   AUDITORIA DE LEAKAGE
# ============================================================
def auditoria_leakage_avancado():
    rows = [
        ("ajuste_anterior_oficial", "ajuste de D-1 (calculado pos-fechamento)", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_ajuste_oficial_pts", "preco - ajuste D-1", "t", "ponto D-1", "NÃO"),
        ("dist_ajuste_oficial_abs", "|preco - ajuste D-1|", "t", "ponto D-1", "NÃO"),
        ("dist_ajuste_oficial_norm", "dist / vol", "t", "ponto D-1 + EWMA", "NÃO"),
        ("acima_ajuste_oficial", "preco > ajuste D-1", "t", "ponto D-1", "NÃO"),
        ("abaixo_ajuste_oficial", "preco < ajuste D-1", "t", "ponto D-1", "NÃO"),
        ("retorno_em_relacao_ao_ajuste_oficial", "(preco-ajuste)/ajuste", "t", "ponto D-1", "NÃO"),
        ("abertura_vs_ajuste_oficial_pts", "abertura D - ajuste D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("abertura_vs_ajuste_oficial_norm", "dist / vol", "abertura de D", "ponto D-1+EWMA", "NÃO"),
        ("vwap", "sum(pv)/sum(vol) intraday", "t", "cumsum ate t", "NÃO"),
        ("dist_vwap_pts", "preco - vwap", "t", "cumsum ate t", "NÃO"),
        ("dist_vwap_abs", "|preco - vwap|", "t", "cumsum ate t", "NÃO"),
        ("dist_vwap_norm", "dist / vol", "t", "cumsum+EWMA", "NÃO"),
        ("dist_vwap_ticks", "dist / tick", "t", "cumsum+constante", "NÃO"),
        ("acima_vwap", "preco > vwap", "t", "cumsum ate t", "NÃO"),
        ("abaixo_vwap", "preco < vwap", "t", "cumsum ate t", "NÃO"),
        ("aproximando_vwap", "|dist| diminuindo vs ~60s atras", "t", "shift causal", "NÃO"),
        ("afastando_vwap", "|dist| aumentando vs ~60s atras", "t", "shift causal", "NÃO"),
        ("cruzou_vwap", "lado mudou vs snapshot anterior", "t", "shift causal", "NÃO"),
        # interações micro × contexto
        ("aggr_x_dist_vwap", "aggr_imb × dist_vwap_pts", "t", "ambas em t", "NÃO"),
        ("aggr_x_dist_ajuste_oficial", "aggr_imb × dist_ajuste_oficial_pts", "t", "ambas em t", "NÃO"),
        ("aggr_x_acima_vwap", "aggr_imb × acima_vwap", "t", "ambas em t", "NÃO"),
        ("aggr_x_acima_ajuste_oficial", "aggr_imb × acima_ajuste_oficial", "t", "ambas em t", "NÃO"),
        ("aggr_x_posicao_range_dia", "aggr_imb × posicao_range_dia", "t", "ambas em t", "NÃO"),
        ("cvd_x_dist_vwap", "cvd_total × dist_vwap_pts", "t", "ambas em t", "NÃO"),
        ("cvd_x_dist_ajuste_oficial", "cvd_total × dist_ajuste_oficial_pts", "t", "ambas em t", "NÃO"),
        ("cvd_x_acima_vwap", "cvd_total × acima_vwap", "t", "ambas em t", "NÃO"),
        ("cvd_x_acima_ajuste_oficial", "cvd_total × acima_ajuste_oficial", "t", "ambas em t", "NÃO"),
        ("imb_x_dist_vwap", "imb_L5 × dist_vwap_pts", "t", "ambas em t", "NÃO"),
        ("imb_x_dist_ajuste_oficial", "imb_L5 × dist_ajuste_oficial_pts", "t", "ambas em t", "NÃO"),
        ("vol_x_acima_vwap", "vol_realizada × acima_vwap", "t", "ambas em t", "NÃO"),
        ("vol_x_acima_ajuste_oficial", "vol_realizada × acima_ajuste_oficial", "t", "ambas em t", "NÃO"),
        # regime contínuo
        ("vwap_inclinacao_1m", "(vwap[t] - vwap[t-1m]) / vwap[t-1m]", "t", "shift causal", "NÃO"),
        ("vwap_inclinacao_5m", "(vwap[t] - vwap[t-5m]) / vwap[t-5m]", "t", "shift causal", "NÃO"),
        ("regime_realiz_vol", "EWMA de retorno intraday (causal)", "t", "EWMA", "NÃO"),
        ("regime_realiz_vol_bps", "vol em bps", "t", "EWMA", "NÃO"),
        ("regime_vol_zscore", "z-score da vol vs vol media (vol de vol)", "t", "EWMA dupla", "NÃO"),
        ("regime_aggr_persistencia", "EWMA suave de aggr_imb", "t", "EWMA", "NÃO"),
        ("regime_cvd_aceleracao", "(cvd[t] - cvd[t-30s]) / 30s", "t", "shift causal", "NÃO"),
        ("regime_range_dia_norm", "(maxima_dia - minima_dia) / vol", "t", "expanding+EWMA", "NÃO"),
        ("regime_pos_vs_vwap", "= dist_vwap_norm", "t", "idem", "NÃO"),
        ("regime_pos_vs_ajuste", "= dist_ajuste_oficial_norm", "t", "idem", "NÃO"),
    ]
    return [{"feature": f, "fonte": s, "disponivel_em": d, "janela": j, "olha_futuro": o}
            for (f, s, d, j, o) in rows]


if __name__ == '__main__':
    import json
    print(json.dumps(auditoria_leakage_avancado(), indent=2, ensure_ascii=False))
