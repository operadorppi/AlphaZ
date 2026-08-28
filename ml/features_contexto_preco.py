"""
features_contexto_preco.py — Contexto de preço diário/sessão anterior para
o sistema Quant, COMPOSTO AO LADO das features de microestrutura existentes
(em features_lib.py / GeradorJanelas). NÃO substitui nada.

PRINCÍPIO FUNDAMENTAL: ZERO LOOK-AHEAD.
  - maxima_dia / minima_dia = máximo/mínimo OBSERVADO até o timestamp (expanding
    causal DENTRO do dia, reiniciado a cada pregão).
  - "anterior" (fechamento/ajuste/máxima/mínima/abertura do D-1) = informação
    conhecida ANTES da abertura de D, portanto utilizável durante todo D.
  - volatilidade = EWMA causal de |Δpreço| (só passado).
  - Nenhuma estatística usa o restante do pregão nem média/desvio futuro.

INTEGRAÇÃO (item 14):
  - Única implementação: tanto batch (dataset_builder) quanto um scorer ao vivo
    devem chamar `adicionar_contexto_preco`. Não espalhar a conta.
  - O pipeline fornece o preço (coluna `preco_ultimo` do GeradorJanelas) e,
    opcionalmente, uma tabela de referência diária (`ref_diario`) com o D-1.
    Se não fornecida, é derivada do próprio dataset (calcular_referencia_diaria).

AJUSTE: na ausência de feed de ajuste da B3, o "ajuste" é o preço de fechamento
  do dia (proxy documentada). Quem tiver o ajuste real deve passar `ref_diario`
  explícito. O comportamento é o mesmo: só se usa o ajuste de D-1.

VER item 16: `auditoria_leakage()` documenta, para cada feature, fonte,
timestamp de disponibilidade e se olha o futuro (sempre NÃO).
"""

import numpy as np
import pandas as pd

# Deslocamento Brasil (UTC-3 fixo em agosto, sem DST) — MESMO critério do
# walk_forward_v914_limpo.py, para que o "dia" casado com o split temporal.
_TZ_OFFSET_MS = 3 * 3600 * 1000
_DIA_MS = 86_400_000
_VOL_ALPHA = 0.005          # EWMA da vol realizada (|Δpreço|)
_VOL_EPS = 1e-9             # piso anti-divisão-por-zero
_RANGE_EPS = 1e-9
# Janela (em nº de linhas a 100ms) para "retornando à abertura" (~60s).
_REDUZ_JANELA_LINHAS = 600


# ============================================================
#   FUNÇÕES AUXILIARES
# ============================================================
def _safe_div(num, den, eps=_VOL_EPS):
    """Divisão segura:
      - ambos finitos e |den| > eps  -> num/den
      - ambos finitos e |den| <= eps -> 0.0   (sem informação de vol/range)
      - qualquer NaN                   -> NaN   (dado ausente: NÃO inventar)
    """
    num = pd.to_numeric(num, errors='coerce').astype('float64')
    den = pd.to_numeric(den, errors='coerce').astype('float64')
    res = num / den
    res = res.mask((den.abs() <= eps) & den.notna() & num.notna(), 0.0)
    res = res.mask(~(den.notna() & num.notna()), np.nan)
    return res


def _dia_de_ts(ts_ms):
    """Índice de dia (Brasília UTC-3) — usado para reset diário e join D-1.
    Consistente com walk_forward (ts - 3h) // 86400000."""
    return (pd.to_numeric(ts_ms, errors='coerce').astype('int64') - _TZ_OFFSET_MS) // _DIA_MS


def _detectar_preco(df, preco_col):
    if preco_col is not None and preco_col in df.columns:
        return preco_col
    for c in ('preco_ultimo', 'preco', 'mid', 'book_mid', 'preco_fim'):
        if c in df.columns:
            return c
    raise KeyError("features_contexto_preco: nenhuma coluna de preço encontrada "
                   "(esperado 'preco_ultimo'/'preco'/'mid'). Use preco_col=.")


# ============================================================
#   REFERÊNCIA DIÁRIA (D-1)
# ============================================================
def calcular_referencia_diaria(df, preco_col=None, ativo_col='ativo', ts_col='ts_ms'):
    """Deriva, do próprio dataset, a estatística DIÁRIA de cada pregão e
    desloca em 1 dia DENTRO de cada ativo para obter o contexto "anterior".

    Retorna DataFrame indexado por (ativo, _dia) com colunas:
      abertura_anterior, fechamento_anterior, ajuste_anterior,
      maxima_anterior, minima_anterior, faixa_anterior.

    'ajuste' = fechamento do dia (proxy documentada; substituir por
    ref_diario explícito se houver ajuste real da B3).

    Causalidade: o "anterior" de D usa SÓ dados de D-1 (fechados antes de
    D abrir). Para o primeiro dia disponível, vira NaN (tratado em seção 13).
    """
    pc = _detectar_preco(df, preco_col)
    tmp = df[[ativo_col, ts_col, pc]].copy()
    tmp['_dia'] = _dia_de_ts(tmp[ts_col])
    tmp[pc] = pd.to_numeric(tmp[pc], errors='coerce')

    daily = (tmp.groupby([ativo_col, '_dia'])[pc]
             .agg(abertura_dia='first', maxima_dia_ref='max',
                  minima_dia_ref='min', fechamento_dia='last')
             .reset_index())
    daily['ajuste_dia'] = daily['fechamento_dia']  # proxy

    # Desloca em 1 dia DENTRO de cada ativo (mantém ativo/_dia como colunas)
    _src = ['abertura_dia', 'maxima_dia_ref', 'minima_dia_ref',
            'fechamento_dia', 'ajuste_dia']
    _dst = ['abertura_anterior', 'maxima_anterior', 'minima_anterior',
            'fechamento_anterior', 'ajuste_anterior']
    daily[_src] = daily.sort_values([ativo_col, '_dia']).groupby(ativo_col)[_src].shift(1)
    daily = daily.rename(columns=dict(zip(_src, _dst)))
    daily['faixa_anterior'] = daily['maxima_anterior'] - daily['minima_anterior']

    ref = daily.set_index([ativo_col, '_dia'])[
        ['abertura_anterior', 'fechamento_anterior', 'ajuste_anterior',
         'maxima_anterior', 'minima_anterior', 'faixa_anterior']]
    return ref


# ============================================================
#   ADICIONAR CONTEXTO (função principal)
# ============================================================
def adicionar_contexto_preco(df, ref_diario=None, preco_col=None,
                             ativo_col='ativo', ts_col='ts_ms',
                             vol_alpha=_VOL_ALPHA):
    """Adiciona todas as features de contexto de preço a `df` e DEVOLVE o
    mesmo df enriquecido. Não altera nenhuma coluna existente.

    Requer: ts_ms, ativo, e uma coluna de preço (preco_ultimo por padrão).
    Opcional: ref_diario (tabela de D-1, ver calcular_referencia_diaria).
    """
    df = df.copy()
    pc = _detectar_preco(df, preco_col)
    df[pc] = pd.to_numeric(df[pc], errors='coerce')
    df['_dia'] = _dia_de_ts(df[ts_col])
    df = df.sort_values([ativo_col, ts_col]).reset_index(drop=True)

    g = df.groupby([ativo_col, '_dia'])[pc]

    # --- Intraday causal (expanding DENTRO do dia = só passado) ---
    df['maxima_dia'] = g.transform(lambda s: s.expanding().max())
    df['minima_dia'] = g.transform(lambda s: s.expanding().min())
    df['abertura'] = g.transform('first')

    # --- Volatilidade realizada (EWMA de |Δpreço|, causal, DENTRO do dia) ---
    # diff dentro do dia: o gap de abertura (preço de ontem) NÃO contamina a
    # vol do primeiro bar (consistência com features_lib: "realized_vol espúrio
    # polui a abertura").
    ret = df.groupby([ativo_col, '_dia'])[pc].diff().abs()
    df['_vol_pts'] = ret.groupby(df[ativo_col]).transform(
        lambda s: s.ewm(alpha=vol_alpha, adjust=False).mean())
    vol = df['_vol_pts']

    # --- Referência D-1 ---
    if ref_diario is None:
        ref_diario = calcular_referencia_diaria(df, preco_col=pc,
                                                ativo_col=ativo_col, ts_col=ts_col)
    ref = ref_diario.reset_index()
    df = df.merge(ref, on=[ativo_col, '_dia'], how='left')

    preco = df[pc]
    maxima = df['maxima_dia']
    minima = df['minima_dia']
    ab = df['abertura']
    f_ant = df['fechamento_anterior']
    a_ant = df['ajuste_anterior']
    mx_ant = df['maxima_anterior']
    mn_ant = df['minima_anterior']
    faixa_ant = df['faixa_anterior']

    # ---------- 3. Distâncias (pontos e normalizadas por vol) ----------
    df['dist_fechamento_anterior_pts'] = preco - f_ant
    df['dist_ajuste_pts'] = preco - a_ant
    df['dist_abertura_pts'] = preco - ab
    df['dist_maxima_dia_pts'] = preco - maxima
    df['dist_minima_dia_pts'] = preco - minima
    df['dist_maxima_anterior_pts'] = preco - mx_ant
    df['dist_minima_anterior_pts'] = preco - mn_ant

    df['dist_fechamento_anterior_norm'] = _safe_div(preco - f_ant, vol)
    df['dist_ajuste_norm'] = _safe_div(preco - a_ant, vol)
    df['dist_abertura_norm'] = _safe_div(preco - ab, vol)
    df['dist_maxima_dia_norm'] = _safe_div(preco - maxima, vol)
    df['dist_minima_dia_norm'] = _safe_div(preco - minima, vol)
    df['dist_maxima_anterior_norm'] = _safe_div(preco - mx_ant, vol)
    df['dist_minima_anterior_norm'] = _safe_div(preco - mn_ant, vol)

    # ---------- 4. Posição dentro da faixa ----------
    range_dia = maxima - minima
    df['posicao_range_dia'] = _safe_div(preco - minima, range_dia)
    df['range_dia_valido'] = (range_dia > _RANGE_EPS).astype('float64')
    # máxima==mínima (sem range ainda): NaN + flag 0 (sem informação falsa)
    df['posicao_range_dia'] = df['posicao_range_dia'].mask(range_dia <= _RANGE_EPS, np.nan)

    range_ant = mx_ant - mn_ant
    df['posicao_range_anterior'] = _safe_div(preco - mn_ant, range_ant)
    df['range_anterior_valido'] = (range_ant > _RANGE_EPS).astype('float64')
    df['posicao_range_anterior'] = df['posicao_range_anterior'].mask(
        range_ant <= _RANGE_EPS, np.nan)

    # ---------- 5. Gap de abertura ----------
    df['gap_abertura_fechamento_anterior'] = _safe_div(ab - f_ant, vol)
    df['gap_abertura_ajuste'] = _safe_div(ab - a_ant, vol)
    df['gap_abertura_fechamento_anterior_pts'] = ab - f_ant
    df['gap_abertura_ajuste_pts'] = ab - a_ant

    # ---------- 6. Contexto em relação ao ajuste ----------
    df['acima_ajuste'] = (preco > a_ant).where(a_ant.notna(), np.nan)
    df['abaixo_ajuste'] = (preco < a_ant).where(a_ant.notna(), np.nan)
    df['dist_ajuste_pts'] = df['dist_ajuste_pts']
    df['dist_ajuste_norm'] = df['dist_ajuste_norm']
    df['dist_ajuste_abs'] = (preco - a_ant).abs()
    # retorno em relação ao ajuste (%, não vol-normalizado)
    df['retorno_em_relacao_ao_ajuste'] = _safe_div(preco - a_ant, a_ant)

    # ---------- 7. Contexto da abertura ----------
    df['acima_abertura'] = (preco > ab).where(ab.notna(), np.nan)
    df['abaixo_abertura'] = (preco < ab).where(ab.notna(), np.nan)
    # retornando para a abertura? |dist| atual <= |dist| ~60s atrás (causal)
    dist_abs = (preco - ab).abs()
    dist_abs_prev = dist_abs.groupby(df[ativo_col]).shift(_REDUZ_JANELA_LINHAS)
    df['dist_abertura_reduzindo'] = (
        (dist_abs <= dist_abs_prev).where(dist_abs_prev.notna(), np.nan))
    # abertura vs ajuste (proxy = fechamento D-1)
    df['abertura_vs_ajuste_pts'] = ab - a_ant
    df['abertura_vs_ajuste_norm'] = _safe_div(ab - a_ant, vol)

    # ---------- 8. Contexto de máxima/mínima (intraday) ----------
    # thresholds robustos em função da vol (não pontos fixos)
    K = 1.0
    df['perto_maxima'] = ((maxima - preco) <= (K * vol)).where(
        (maxima - preco).notna() & vol.notna(), np.nan)
    df['perto_minima'] = ((preco - minima) <= (K * vol)).where(
        (preco - minima).notna() & vol.notna(), np.nan)
    # rompimento = NOVO topo/fundo (causal: maxima_dia só cresce; flag quando
    # o preço IGUAL a maxima_dia E a maxima subiu vs a anterior). NÃO é
    # "romper a máxima final do dia" (isso seria look-ahead — proibido).
    mx_prev = maxima.groupby(df[ativo_col]).shift(1)
    mn_prev = minima.groupby(df[ativo_col]).shift(1)
    df['rompimento_maxima'] = ((preco >= maxima - 1e-9) & (maxima > mx_prev)).astype('float64')
    df['rompimento_minima'] = ((preco <= minima + 1e-9) & (minima < mn_prev)).astype('float64')
    # rejeição = afastou-se do extremo do dia por > K*vol (puxão de volta)
    df['rejeicao_maxima'] = ((maxima - preco) > (K * vol)).where(vol.notna(), np.nan)
    df['rejeicao_minima'] = ((preco - minima) > (K * vol)).where(vol.notna(), np.nan)

    # ---------- 9. Contexto do dia anterior ----------
    df['range_anterior_pts'] = faixa_ant
    df['posicao_vs_range_anterior'] = df['posicao_range_anterior']
    df['dist_maxima_anterior'] = df['dist_maxima_anterior_pts']
    df['dist_minima_anterior'] = df['dist_minima_anterior_pts']
    df['preco_acima_maxima_anterior'] = (preco > mx_ant).where(mx_ant.notna(), np.nan)
    df['preco_abaixo_minima_anterior'] = (preco < mn_ant).where(mn_ant.notna(), np.nan)
    df['rompimento_maxima_anterior'] = df['preco_acima_maxima_anterior']
    df['rompimento_minima_anterior'] = df['preco_abaixo_minima_anterior']

    # ---------- 10. Interações contexto × microestrutura ----------
    # Só cria se a feature de microestrutura existir no df (robusto).
    cvd = df['cvd_total'] if 'cvd_total' in df.columns else None
    if cvd is not None and 'vol_total' in df.columns:
        cvd_norm = _safe_div(cvd, df['vol_total'])
    else:
        cvd_norm = None
    aggr = df['aggr_imb'] if 'aggr_imb' in df.columns else None
    imb = df['imb_L5'] if 'imb_L5' in df.columns else None

    if aggr is not None:
        df['aggr_imb_x_dist_ajuste_norm'] = aggr * df['dist_ajuste_norm']
        df['aggr_imb_x_posicao_range_dia'] = aggr * df['posicao_range_dia']
        df['aggr_imb_x_dist_maxima_dia_norm'] = aggr * df['dist_maxima_dia_norm']
        df['aggr_imb_x_dist_minima_dia_norm'] = aggr * df['dist_minima_dia_norm']
    if cvd_norm is not None:
        df['cvd_norm_x_acima_abertura'] = cvd_norm * df['acima_abertura']
        df['cvd_norm_x_acima_ajuste'] = cvd_norm * df['acima_ajuste']
    if imb is not None:
        df['imb_L5_x_dist_maxima_dia_norm'] = imb * df['dist_maxima_dia_norm']
        df['imb_L5_x_dist_minima_dia_norm'] = imb * df['dist_minima_dia_norm']

    # ---------- 10-b. Interações adicionais: delta × contexto ----------
    # delta = variação de preço 1-tick (causal: diff dentro do dia)
    delta1 = df.groupby([ativo_col, '_dia'])[pc].diff()
    if 'acima_abertura' in df.columns:
        df['delta_x_acima_abertura'] = delta1 * df['acima_abertura']
    if 'acima_ajuste' in df.columns:
        df['delta_x_acima_ajuste'] = delta1 * df['acima_ajuste']

    # limpeza de colunas auxiliares internas (mantém _dia e _vol_pts, que
    # são derivadas legítimas e úteis; o chamador decide se as descarta)
    return df


# ============================================================
#   16. AUDITORIA DE LEAKAGE
# ============================================================
def auditoria_leakage():
    """Retorna lista de dicts documentando cada nova feature.
    'olha_futuro' é sempre 'NÃO' (garantido pelas escolhas acima)."""
    rows = [
        ("maxima_dia", "running max do preço até t (expanding no dia)", "t", "expanding até t", "NÃO"),
        ("minima_dia", "running min do preço até t", "t", "expanding até t", "NÃO"),
        ("abertura", "primeiro preço do dia", "abertura do dia", "ponto único", "NÃO"),
        ("_vol_pts", "EWMA causal de |Δpreço|", "t (só passado)", "EWMA", "NÃO"),
        ("dist_fechamento_anterior_pts", "preco - fechamento D-1", "abertura de D (D-1 fechado)", "ponto D-1", "NÃO"),
        ("dist_ajuste_pts", "preco - ajuste D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_abertura_pts", "preco - abertura", "abertura do dia", "ponto", "NÃO"),
        ("dist_maxima_dia_pts", "preco - maxima_dia", "t", "expanding até t", "NÃO"),
        ("dist_minima_dia_pts", "preco - minima_dia", "t", "expanding até t", "NÃO"),
        ("dist_maxima_anterior_pts", "preco - maxima D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_minima_anterior_pts", "preco - minima D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_fechamento_anterior_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_ajuste_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_abertura_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_maxima_dia_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_minima_dia_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_maxima_anterior_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("dist_minima_anterior_norm", "dist / vol", "t", "expanding+EWMA", "NÃO"),
        ("posicao_range_dia", "(preco-min)/(max-min) intraday", "t", "expanding até t", "NÃO"),
        ("posicao_range_anterior", "(preco-min)/(max-min) D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("gap_abertura_fechamento_anterior", "(abertura-fech D-1)/vol", "abertura de D", "ponto D-1+EWMA", "NÃO"),
        ("gap_abertura_ajuste", "(abertura-ajuste D-1)/vol", "abertura de D", "ponto D-1+EWMA", "NÃO"),
        ("gap_abertura_fechamento_anterior_pts", "abertura-fech D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("gap_abertura_ajuste_pts", "abertura-ajuste D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("acima_ajuste", "preco>ajuste D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("abaixo_ajuste", "preco<ajuste D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_ajuste_norm", "igual a dist_ajuste_norm", "t", "expanding+EWMA", "NÃO"),
        ("retorno_em_relacao_ao_ajuste", "(preco-ajuste)/ajuste", "abertura de D", "ponto D-1", "NÃO"),
        ("acima_abertura", "preco>abertura", "abertura do dia", "ponto", "NÃO"),
        ("abaixo_abertura", "preco<abertura", "abertura do dia", "ponto", "NÃO"),
        ("dist_abertura_reduzindo", "|dist à abertura| encolhendo vs ~60s antes", "t", "shift causal", "NÃO"),
        ("perto_maxima", "(max-preco)<=K*vol", "t", "expanding+EWMA", "NÃO"),
        ("perto_minima", "(preco-min)<=K*vol", "t", "expanding+EWMA", "NÃO"),
        ("rompimento_maxima", "novo topo do dia (causal)", "t", "expanding até t", "NÃO"),
        ("rompimento_minima", "novo fundo do dia (causal)", "t", "expanding até t", "NÃO"),
        ("rejeicao_maxima", "(max-preco)>K*vol (voltou do topo)", "t", "expanding+EWMA", "NÃO"),
        ("rejeicao_minima", "(preco-min)>K*vol (voltou do fundo)", "t", "expanding+EWMA", "NÃO"),
        ("range_anterior_pts", "max D-1 - min D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("posicao_vs_range_anterior", "=posicao_range_anterior", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_maxima_anterior", "=dist_maxima_anterior_pts", "abertura de D", "ponto D-1", "NÃO"),
        ("dist_minima_anterior", "=dist_minima_anterior_pts", "abertura de D", "ponto D-1", "NÃO"),
        ("preco_acima_maxima_anterior", "preco>max D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("preco_abaixo_minima_anterior", "preco<min D-1", "abertura de D", "ponto D-1", "NÃO"),
        ("rompimento_maxima_anterior", "=preco_acima_maxima_anterior", "abertura de D", "ponto D-1", "NÃO"),
        ("rompimento_minima_anterior", "=preco_abaixo_minima_anterior", "abertura de D", "ponto D-1", "NÃO"),
        ("aggr_imb_x_dist_ajuste_norm", "aggr_imb (micro) × dist_ajuste_norm", "t", "ambas em t", "NÃO"),
        ("aggr_imb_x_posicao_range_dia", "aggr_imb × posicao_range_dia", "t", "ambas em t", "NÃO"),
        ("aggr_imb_x_dist_maxima_dia_norm", "aggr_imb × dist_maxima_dia_norm", "t", "ambas em t", "NÃO"),
        ("aggr_imb_x_dist_minima_dia_norm", "aggr_imb × dist_minima_dia_norm", "t", "ambas em t", "NÃO"),
        ("cvd_norm_x_acima_abertura", "cvd_norm × acima_abertura", "t", "ambas em t", "NÃO"),
        ("cvd_norm_x_acima_ajuste", "cvd_norm × acima_ajuste", "t", "ambas em t", "NÃO"),
        ("imb_L5_x_dist_maxima_dia_norm", "imb_L5 × dist_maxima_dia_norm", "t", "ambas em t", "NÃO"),
        ("imb_L5_x_dist_minima_dia_norm", "imb_L5 × dist_minima_dia_norm", "t", "ambas em t", "NÃO"),
        ("dist_ajuste_abs", "|preco - ajuste D-1|", "t", "ponto D-1", "NÃO"),
        ("abertura_vs_ajuste_pts", "abertura D - ajuste D-1 (proxy)", "abertura de D", "ponto D-1", "NÃO"),
        ("abertura_vs_ajuste_norm", "(abertura-ajuste)/vol", "abertura de D", "ponto D-1+EWMA", "NÃO"),
        ("delta_x_acima_abertura", "Δpreco_1tick × acima_abertura", "t", "shift-1 × t", "NÃO"),
        ("delta_x_acima_ajuste", "Δpreco_1tick × acima_ajuste", "t", "shift-1 × t", "NÃO"),
    ]
    return [{"feature": f, "fonte": s, "disponivel_em": d, "janela": j, "olha_futuro": o}
            for (f, s, d, j, o) in rows]


if __name__ == '__main__':
    import json
    print(json.dumps(auditoria_leakage(), indent=2, ensure_ascii=False))
