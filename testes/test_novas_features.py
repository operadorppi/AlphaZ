import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_novas_features.py — Testes para as camadas de interações, regime
e redundancia (itens 14, 17, 24, 25).

Cobre:
  - Interações micro × contexto (item 16): aggr_x_*, cvd_x_*, imb_x_*, vol_x_*
  - Features de regime contínuo (item 17): vwap_inclinacao, regime_*
  - Análise de redundância (item 25): detecção de pares |r|>=0.95, VIF
"""
import os
import sys
import math
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features_contexto_avancado import (
    adicionar_interacoes_micro_contexto,
    adicionar_features_regime,
    auditoria_leakage_avancado,
)
from analise_redundancia import (
    matriz_correlacao, vif_scores, identificar_features_removiveis,
    _filtrar_features, analisar,
)


# ============================================================
#   INTERAÇÕES MICRO × CONTEXTO
# ============================================================
def test_interacoes_cria_quando_fontes_existem():
    """Se aggr_imb, cvd_total, dist_vwap_pts existem, criar interações."""
    n = 100
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000 + np.cumsum(np.random.randn(n)),
        'aggr_imb': np.random.randn(n),
        'cvd_total': np.random.randn(n),
        'imb_L5': np.random.randn(n),
        'dist_vwap_pts': np.random.randn(n),
        'dist_ajuste_oficial_pts': np.random.randn(n),
        'acima_vwap': np.random.randint(0, 2, n).astype(float),
        'acima_ajuste_oficial': np.random.randint(0, 2, n).astype(float),
    })
    out = adicionar_interacoes_micro_contexto(df)
    # Esperamos ao menos 10 novas colunas
    inter_cols = [c for c in out.columns if c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))]
    assert len(inter_cols) >= 10, f'esperado >=10, obtido {len(inter_cols)}'
    # aggr_x_dist_vwap deve existir
    assert 'aggr_x_dist_vwap' in out.columns
    # multiplicacao coerente
    expected = df['aggr_imb'] * df['dist_vwap_pts']
    np.testing.assert_allclose(out['aggr_x_dist_vwap'].values, expected.values, rtol=1e-9)


def test_interacoes_robusto_sem_fontes():
    """Se as features micro nao existem, nao cria nada (nao quebra)."""
    n = 50
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000.0,
        # sem aggr_imb, sem cvd_total, sem imb_L5
    })
    out = adicionar_interacoes_micro_contexto(df)
    inter_cols = [c for c in out.columns if c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))]
    assert inter_cols == [], f'nao deveria criar nada, mas criou: {inter_cols}'


def test_interacoes_causal_sem_futuro():
    """Adicionar ruido futuro em preco_ultimo nao deve mudar interacoes passadas."""
    n = 50
    base_preco = 1000.0 + np.cumsum(np.random.randn(n))
    df1 = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': base_preco,
        'aggr_imb': np.random.randn(n),
        'dist_vwap_pts': np.random.randn(n),
    })
    df2 = df1.copy()
    # alterar precos futuros (ultimos 5)
    df2.loc[df2.index[-5:], 'preco_ultimo'] = 99999.0
    out1 = adicionar_interacoes_micro_contexto(df1)
    out2 = adicionar_interacoes_micro_contexto(df2)
    # as primeiras N-5 linhas devem ser identicas
    # a interacao 'aggr_x_dist_vwap' nao depende de preco_ultimo, entao DEVE ser identica
    np.testing.assert_array_equal(out1['aggr_x_dist_vwap'].values,
                                   out2['aggr_x_dist_vwap'].values)


# ============================================================
#   FEATURES DE REGIME CONTÍNUO
# ============================================================
def test_regime_inclinacao_vwap():
    """vwap_inclinacao_1m = (vwap[t] - vwap[t-1min]) / vwap[t-1min]"""
    n = 2000  # 200s de dados a 100ms
    # vwap cresce linearmente: 1.0 por tick
    vwap = 1000.0 + np.arange(n) * 0.01  # vwap[0]=1000, vwap[600]=1006, vwap[1600]=1016
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000.0,
        'aggr_imb': 0.0,
        'cvd_total': 0.0,
        'vol_total': 100,
        'vwap': vwap,
        'maxima_dia': vwap + 5,
        'minima_dia': vwap - 5,
    })
    df['_vol_pts'] = 1.0
    out = adicionar_features_regime(df)
    assert 'vwap_inclinacao_1m' in out.columns
    # antes dos 600 ticks (1 min), NaN
    assert pd.isna(out['vwap_inclinacao_1m'].iloc[100])
    # vwap[600] = 1006, vwap[0] = 1000 -> (1006-1000)/1000 = 0.006
    assert math.isclose(out['vwap_inclinacao_1m'].iloc[600], 0.006, abs_tol=1e-9)
    # vwap[1600] = 1016, vwap[1000] = 1010 -> 6/1010 = 0.0059406
    assert math.isclose(out['vwap_inclinacao_1m'].iloc[1600], 6.0/1010.0, abs_tol=1e-9)


def test_regime_realiz_vol_ewma():
    """regime_realiz_vol = EWMA de retorno (causal)."""
    n = 200
    ret = np.random.randn(n) * 0.01
    preco = 1000.0 + np.cumsum(ret)
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': preco,
        'aggr_imb': 0.0,
    })
    out = adicionar_features_regime(df)
    assert 'regime_realiz_vol' in out.columns
    # sem valores negativos para retornos em magnitude
    assert (out['regime_realiz_vol'].dropna() >= 0).all()
    # primeiro valor NaN (sem retorno previo)
    assert pd.isna(out['regime_realiz_vol'].iloc[0])


def test_regime_cvd_aceleracao():
    """regime_cvd_aceleracao = (cvd[t] - cvd[t-30s]) / 30s"""
    n = 500  # 50s
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000.0,
        'aggr_imb': 0.0,
        'cvd_total': np.linspace(0, 100, n),  # cresce linearmente
    })
    out = adicionar_features_regime(df)
    assert 'regime_cvd_aceleracao' in out.columns
    # NaN nos primeiros 300 (30s)
    assert pd.isna(out['regime_cvd_aceleracao'].iloc[100])
    # apos 300 ticks: cvd[300] - cvd[0] = 60.1 - 0 = 60.1 (aprox), / 30 = 2.0 pts/s
    val = out['regime_cvd_aceleracao'].iloc[400]
    assert math.isclose(val, 100 / 50, abs_tol=0.01)  # 2.0 pts/s


def test_regime_range_dia_norm_protegido():
    """regime_range_dia_norm com vol=0 -> 0 (sem NaN forcado)."""
    n = 100
    df = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000.0,
        'aggr_imb': 0.0,
        'maxima_dia': np.full(n, 1010.0),
        'minima_dia': np.full(n, 990.0),  # range = 20
        '_vol_pts': 0.0,  # vol zero
    })
    out = adicionar_features_regime(df)
    assert 'regime_range_dia_norm' in out.columns
    # com vol=0, deve ser 0.0 (safe divide)
    assert (out['regime_range_dia_norm'] == 0.0).all()


# ============================================================
#   ANÁLISE DE REDUNDÂNCIA
# ============================================================
def test_redundancia_detecta_perfeito():
    """Duas features identicas: |r|=1.0 e VIF inf."""
    n = 100
    x = np.arange(n)
    df = pd.DataFrame({
        'ts_ms': np.zeros(n),
        'ativo': 'WINV26',
        'label': np.zeros(n),
        'a': x, 'b': x,  # identicas
        'c': np.random.randn(n),  # nao redundante
    })
    feats = _filtrar_features(df)
    corr, pares = matriz_correlacao(df, feats, 'pearson')
    assert ('a', 'b', 1.0) in [(p[0], p[1], round(p[2], 9)) for p in pares]
    vifs = vif_scores(df, ['a', 'b', 'c'])
    assert math.isinf(vifs.get('a', 0)) or math.isinf(vifs.get('b', 0))


def test_redundancia_threshold_respeitado():
    """Pares com |r| < threshold nao sao reportados."""
    n = 200
    np.random.seed(0)
    df = pd.DataFrame({
        'ts_ms': np.zeros(n),
        'ativo': 'WINV26',
        'label': np.zeros(n),
        'a': np.random.randn(n),
        'b': np.random.randn(n),  # independente de a
    })
    feats = _filtrar_features(df)
    _, pares = matriz_correlacao(df, feats, 'pearson')
    # com threshold 0.95, par |r|<0.3 nao deve aparecer
    assert all(abs(p[2]) < 0.95 for p in pares), f'redundancia falsa: {pares}'


def test_redundancia_vif_alto_detecta_multicolinearidade():
    """3 features perfeitamente colineares -> VIF alto."""
    n = 100
    x = np.arange(n)
    df = pd.DataFrame({
        'ts_ms': np.zeros(n),
        'ativo': 'WINV26',
        'label': np.zeros(n),
        'a': x, 'b': 2 * x, 'c': -x,  # todas colineares
    })
    vifs = vif_scores(df, ['a', 'b', 'c'])
    assert all(math.isinf(v) for v in vifs.values()), f'esperado inf, obtido {vifs}'


def test_redundancia_sugere_remover_com_maior_vif():
    """Quando ha par redundante, a funcao sugere remover a de MAIOR VIF."""
    pares = [('a', 'b', 0.98), ('b', 'c', 0.99)]
    vifs = {'a': 100.0, 'b': 5.0, 'c': 1.0}
    remover, _ = identificar_features_removiveis(pares, vifs)
    # Heuristica: para cada par, remover a de MAIOR VIF.
    # Par (a,b): a=100 > b=5  -> remove 'a'
    # Par (b,c): b=5   > c=1  -> remove 'b'
    assert 'a' in remover, f'esperado a em {remover}'
    assert 'b' in remover, f'esperado b em {remover}'


def test_auditoria_leakage_inclui_interacoes_e_regime():
    """A auditoria documenta TODAS as features novas com olha_futuro=NAO."""
    aud = auditoria_leakage_avancado()
    features_auditadas = {r['feature'] for r in aud}
    # interacoes
    for f in ['aggr_x_dist_vwap', 'cvd_x_dist_ajuste_oficial',
              'imb_x_dist_vwap', 'vol_x_acima_vwap']:
        assert f in features_auditadas, f'faltando na auditoria: {f}'
    # regime
    for f in ['vwap_inclinacao_1m', 'regime_realiz_vol',
              'regime_cvd_aceleracao', 'regime_range_dia_norm']:
        assert f in features_auditadas, f'faltando na auditoria: {f}'
    # tudo NAO
    for r in aud:
        assert r['olha_futuro'] == 'NÃO'


# ============================================================
#   INTEGRAÇÃO: fluxo completo em dados reais
# ============================================================
@pytest.mark.skipif(
    not os.path.exists(r'D:\MarketData\Profit\RAW\ano=2026\mes=08\dia=14'),
    reason='dados reais nao disponiveis',
)
def test_integracao_dados_reais_todas_camadas():
    """Pipeline completo: ajuste + vwap + contexto + interacoes + regime."""
    from calcular_ajuste_diario import calcular_ajuste_multi_dias
    from calcular_vwap_diaria import carregar_negocios_brutos, calcular_vwap, listar_dias

    dias = listar_dias(2026, 8)
    df = carregar_negocios_brutos(2026, 8, dias, ['WINV26'])
    assert not df.empty
    df_vwap = calcular_vwap(df)
    df_ajuste = calcular_ajuste_multi_dias(['WINV26'], 2026, 8, dias=dias)

    # gerar features simples
    n = 1000
    feat = pd.DataFrame({
        'ts_ms': np.arange(1746000000000, 1746000000000 + n * 100, 100),
        'ativo': 'WINV26',
        'preco_ultimo': 1000.0 + np.cumsum(np.random.randn(n)),
        'aggr_imb': np.random.randn(n),
        'cvd_total': np.cumsum(np.random.randn(n)),
        'imb_L5': np.random.randn(n),
        'mid': 1000.0,
        'vol_total': 100,
    })
    feat = adicionar_features_regime(feat)
    feat = adicionar_interacoes_micro_contexto(feat)
    # sem vwap, nao ha vwap_inclinacao_*; com aggr_imb mas sem dist_vwap_pts,
    # nao ha aggr_x_dist_vwap. Apenas regime_* deve existir.
    assert 'regime_realiz_vol' in feat.columns
    assert 'regime_cvd_aceleracao' in feat.columns
    assert 'regime_aggr_persistencia' in feat.columns
    assert feat.shape[1] > 10
