# features_expansao.py — Expansao do contexto de mercado (v9.37)
# Features novas: vol multi-TF, retornos multi-horizonte, POC migracao,
# volume relativo, tempo de sessao, niveis semanais.
# ZERO LOOK-AHEAD: tudo causal, so passado.
import numpy as np
import pandas as pd

_TZ_OFF = 3 * 3600 * 1000
_DIA_MS = 86_400_1000
_SEMANA_MS = 7 * 86_400_000
_VE = 1e-9


def _dia_de_ts(ts_ms):
    return (pd.to_numeric(ts_ms, errors="coerce").astype("int64") - _TZ_OFF) // 86_400_000


def _safe_div(num, den, eps=_VE):
    num = pd.to_numeric(num, errors="coerce").astype("float64")
    den = pd.to_numeric(den, errors="coerce").astype("float64")
    res = num / den
    res = res.mask((den.abs() <= eps) & den.notna() & num.notna(), 0.0)
    res = res.mask(~(den.notna() & num.notna()), np.nan)
    return res


def adicionar_expansao(df, preco_col=None, ativo_col="ativo", ts_col="ts_ms"):
    """Adiciona features de expansao ao DataFrame. Nao remove nada."""
    df = df.copy()
    # Detectar coluna de preco
    pc = preco_col
    if pc is None:
        for c in ("preco_ultimo", "preco", "mid", "preco_fim"):
            if c in df.columns:
                pc = c; break
    if pc is None:
        raise KeyError("features_expansao: nenhuma coluna de preco encontrada")
    df[pc] = pd.to_numeric(df[pc], errors="coerce")
    df["_dia"] = _dia_de_ts(df[ts_col])
    df = df.sort_values([ativo_col, ts_col]).reset_index(drop=True)
    g = df.groupby([ativo_col, "_dia"])[pc]

    # --- 1. Retornos multi-horizonte (causais: shift) ---
    rets = [1, 5, 10, 50, 100, 150, 300, 500]  # em linhas (100ms cada)
    for n in rets:
        df[f"retorno_{n}x100ms"] = g.transform(lambda s: s.pct_change(n))

    # --- 2. Volatilidade multi-TF (EWMA de |ret| em janelas) ---
    # P0-A21 (v15.15): n = LINHAS do grid de 100ms = TEMPO REAL. Os valores
    # antigos estavam errados para os nomes (100 linhas = 10s nao 15s;
    # 300 = 30s nao 1min; 1500 = 150s nao 5min). O dataset_100ms tem 1 linha
    # por corte de 100ms do master clock (forward-filled), entao pct_change(n)
    # e ewm(alpha=2/(n+1)) medem janelas temporais reais.
    for n, nome in [(1, "100ms"), (5, "500ms"), (10, "1s"), (50, "5s"),
                    (150, "15s"), (600, "1min"), (3000, "5min")]:
        ret_n = g.transform(lambda s: s.pct_change(n).abs())
        alpha = 2.0 / (n + 1)
        df[f"vol_{nome}"] = ret_n.groupby(df[ativo_col]).transform(
            lambda s: s.ewm(alpha=alpha, adjust=False).mean())

    # --- 3. Range dia normalizado ---
    mx = g.transform("max")
    mn = g.transform("min")
    rng = mx - mn
    vol_ref = df["vol_1s"] if "vol_1s" in df.columns else g.transform(lambda s: s.diff().abs().ewm(alpha=0.1, adjust=False).mean())
    df["range_dia"] = rng
    df["range_dia_norm"] = _safe_div(rng, vol_ref)

    # --- 4. POC migracao (delta, velocity, direction) ---
    if "vp_poc_dist" in df.columns:
        poc = -df["vp_poc_dist"] + df[pc]  # reconstruir POC abs
        df["poc_delta"] = poc.groupby(df[ativo_col]).diff()
        df["poc_velocity"] = poc.groupby(df[ativo_col]).transform(
            lambda s: s.diff().ewm(alpha=0.1, adjust=False).mean())
        df["poc_direction"] = np.sign(df["poc_delta"])

    # --- 5. Volume relativo ---
    if "vol_total" in df.columns:
        vol_acum = g.transform(lambda s: s.cumsum())
        df["volume_acumulado_dia"] = vol_acum
        # volume por minuto (aprox: 600 linhas = 60s)
        df["volume_por_minuto"] = vol_acum.groupby(df[ativo_col]).transform(
            lambda s: s.diff(600).fillna(s))
        # volume relativo: ratio vs media historica do mesmo horario
        # (aproximar por media movel longa)
        df["volume_relativo"] = vol_acum.groupby(df["ativo"]).transform(
            lambda s: _safe_div(s, s.ewm(alpha=0.001, adjust=False).mean()))

    # --- 6. Tempo de sessao ---
    # P0-A22 (v15.16): TOD de BRASILIA (epoch UTC - 3h), mesma regra das
    # funcoes temporais oficiais (core.temporal). ANTES usava `% 86400000`
    # cru (TOD UTC): 14h BRT virava 17h -> segundos_desde_abertura,
    # minutos_ate_fechamento e sin/cos_horario deslocados +3h no dataset.
    tod_ms = (df[ts_col].astype("int64") - _TZ_OFF) % 86_400_000
    abertura_ms = 9 * 3600 * 1000  # 09:00 BRT
    fechamento_ms = 17 * 3600 * 1000 + 45 * 60 * 1000  # 17:45 BRT
    df["segundos_desde_abertura"] = ((tod_ms - abertura_ms) / 1000).clip(lower=0)
    df["minutos_ate_fechamento"] = ((fechamento_ms - tod_ms) / 60000).clip(lower=0)
    # sin/cos do horario (ciclico, sem descontinuidade)
    hora_frac = (tod_ms / 86_400_000.0) * 2 * np.pi
    df["sin_horario"] = np.sin(hora_frac)
    df["cos_horario"] = np.cos(hora_frac)

    # --- 7. Niveis semanais (D-1 da semana) ---
    # Calcular por semana: max/min/fechamento da semana anterior
    _semana = (df[ts_col].astype("int64") - _TZ_OFF) // (7 * 86_400_000)
    df["_semana"] = _semana
    wk_g = df.groupby([ativo_col, "_semana"])[pc]
    wk_stats = wk_g.agg(wk_max="max", wk_min="min", wk_close="last").reset_index()
    wk_stats["_semana"] += 1  # shift para semana anterior
    df = df.merge(wk_stats, on=[ativo_col, "_semana"], how="left")
    df["maxima_semana_anterior"] = df["wk_max"]
    df["minima_semana_anterior"] = df["wk_min"]
    df["fechamento_semana_anterior"] = df["wk_close"]
    df["dist_max_semana_pts"] = df[pc] - df["maxima_semana_anterior"]
    df["dist_min_semana_pts"] = df[pc] - df["minima_semana_anterior"]
    df["dist_fech_semana_pts"] = df[pc] - df["fechamento_semana_anterior"]
    df.drop(columns=["_semana", "wk_max", "wk_min", "wk_close"], inplace=True)

    # Limpar auxiliares
    if "_dia" in df.columns:
        df.drop(columns=["_dia"], inplace=True)

    return df


def features_disponiveis():
    """Lista todas as features que este modulo pode gerar."""
    return [
        "retorno_1x100ms", "retorno_5x100ms", "retorno_10x100ms", "retorno_50x100ms",
        "retorno_100x100ms", "retorno_150x100ms", "retorno_300x100ms", "retorno_500x100ms",
        "vol_100ms", "vol_500ms", "vol_1s", "vol_5s", "vol_15s", "vol_1min", "vol_5min",
        "range_dia", "range_dia_norm",
        "poc_delta", "poc_velocity", "poc_direction",
        "volume_acumulado_dia", "volume_por_minuto", "volume_relativo",
        "segundos_desde_abertura", "minutos_ate_fechamento",
        "sin_horario", "cos_horario",
        "maxima_semana_anterior", "minima_semana_anterior", "fechamento_semana_anterior",
        "dist_max_semana_pts", "dist_min_semana_pts", "dist_fech_semana_pts",
    ]
