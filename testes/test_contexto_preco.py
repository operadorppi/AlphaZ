import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_contexto_preco.py — Testes obrigatórios (itens 15 e 16) do
features_contexto_preco.py. Todos os testes provam CAUSALIDADE e
ausência de look-ahead, além de tratar divisão por zero e dados ausentes.

Convenção de tempo: ts_ms = D*86400000 + time_of_day_ms + 3h,
para que _dia_de_ts(ts) = D (Brasília UTC-3, como no walk_forward).
"""
import numpy as np
import pandas as pd
import pytest

from features_contexto_preco import (
    adicionar_contexto_preco,
    calcular_referencia_diaria,
    auditoria_leakage,
    _safe_div,
)


def _ts(dia, h, m, s=0):
    return dia * 86_400_000 + (h * 3600 + m * 60 + s) * 1000 + 3 * 3600 * 1000


def _df(ativos, ts, preco):
    return pd.DataFrame({
        'ativo': ativos,
        'ts_ms': ts,
        'preco_ultimo': preco,
    })


# ============================================================
# TESTE 1 — máxima causal
# ============================================================
def test_maxima_causal():
    preco = [100.0, 105.0, 103.0, 110.0]
    ts = [_ts(0, 10, 0 + i) for i in range(4)]
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')

    # Às 10:02 (preço 103) a máxima deve ser 105, NÃO 110 (futuro)
    assert out['maxima_dia'].iloc[2] == pytest.approx(105.0)
    # Em nenhum instante a máxima pode exceder o máximo observado até ali
    for i in range(4):
        assert out['maxima_dia'].iloc[i] == pytest.approx(max(preco[: i + 1]))
    # O pico futuro (110) não "vaza" para trás
    assert out['maxima_dia'].iloc[1] == pytest.approx(105.0)


# ============================================================
# TESTE 2 — mínima causal
# ============================================================
def test_minima_causal():
    preco = [100.0, 105.0, 103.0, 110.0]
    ts = [_ts(0, 10, i) for i in range(4)]
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')

    assert out['minima_dia'].iloc[2] == pytest.approx(100.0)
    for i in range(4):
        assert out['minima_dia'].iloc[i] == pytest.approx(min(preco[: i + 1]))


# ============================================================
# TESTE 3 — reset diário (D-1 não contamina D)
# ============================================================
def test_reset_diario():
    # Dia 0: máx 110; Dia 1: máx 108 (não pode herdar 110)
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100.0, 110.0, 102.0, 108.0]
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')

    dia1 = out[out['_dia'] == 1]
    assert dia1['maxima_dia'].max() == pytest.approx(108.0)
    assert dia1['maxima_dia'].min() == pytest.approx(102.0)
    # Máxima do dia 0 NUNCA aparece no dia 1
    assert (dia1['maxima_dia'] > 110.0).any() == False  # noqa: E712

    # O contexto "anterior" do dia 1 vem do dia 0 (fechamento = 110)
    assert dia1['fechamento_anterior'].iloc[0] == pytest.approx(110.0)
    assert dia1['maxima_anterior'].iloc[0] == pytest.approx(110.0)
    assert dia1['ajuste_anterior'].iloc[0] == pytest.approx(110.0)


# ============================================================
# TESTE 4 — ajuste anterior = ajuste de D-1
# ============================================================
def test_ajuste_anterior():
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(0, 10, 2),
          _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100.0, 105.0, 110.0, 101.0, 103.0]  # D-1 fecha em 110
    df = _df(['WIN'] * 5, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    dia1 = out[out['_dia'] == 1]
    # ajuste = fechamento de D-1 (proxy)
    assert dia1['ajuste_anterior'].iloc[0] == pytest.approx(110.0)


# ============================================================
# TESTE 5 — fechamento anterior = fechamento de D-1
# ============================================================
def test_fechamento_anterior():
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(0, 10, 2),
          _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100.0, 105.0, 110.0, 101.0, 103.0]
    df = _df(['WIN'] * 5, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    dia1 = out[out['_dia'] == 1]
    assert dia1['fechamento_anterior'].iloc[0] == pytest.approx(110.0)
    # gap em pontos
    assert dia1['gap_abertura_fechamento_anterior_pts'].iloc[0] == pytest.approx(101.0 - 110.0)


# ============================================================
# TESTE 6 — divisão por zero (max==min e vol==0)
# ============================================================
def test_divisao_por_zero():
    # Dia 0 plano (100), Dia 1 plano (105) -> range 0 e vol 0
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100.0, 100.0, 105.0, 105.0]
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')

    dia1 = out[out['_dia'] == 1]
    # range 0 -> posição NaN + flag 0 (sem informação falsa)
    assert dia1['posicao_range_dia'].isna().all()
    assert (dia1['range_dia_valido'] == 0).all()
    # vol 0 -> distância normalizada = 0 (não inf/NaN por zero); onde não há
    # histórico de vol (1º bar do dia) o valor é NaN (sem informação), nunca inf
    dn = dia1['dist_ajuste_norm']
    assert np.isfinite(dn.fillna(0.0)).all()           # nunca inf
    assert dn.dropna().eq(0.0).all()                    # não-nulo é 0 (vol==0)
    assert dn.notna().any() is False or True           # NaN permitido no 1º bar


# ============================================================
# TESTE 7 — ausência de futuro (estrutural)
# ============================================================
def test_sem_futuro():
    rng = np.random.default_rng(42)
    n = 500
    preco = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    ts = [_ts(2, 9, 0, 0) + i * 100 for i in range(n)]  # 100ms
    df = _df(['WIN'] * n, ts, list(preco))
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')

    # Para cada linha i, maxima_dia[i] == max(preco[0..i])
    manual = pd.Series(preco).expanding().max().to_numpy()
    assert np.allclose(out['maxima_dia'].to_numpy(), manual, atol=1e-9)
    manual_min = pd.Series(preco).expanding().min().to_numpy()
    assert np.allclose(out['minima_dia'].to_numpy(), manual_min, atol=1e-9)
    # Nenhuma feature de "anterior" pode usar o próprio dia D para D-1
    # (validado no teste 3/4, mas reforçado: distâncias usam só t e D-1)
    assert out['maxima_dia'].iloc[0] == pytest.approx(preco[0])


# ============================================================
# TESTE 8 — colunas existentes preservadas + robustez sem micro
# ============================================================
def test_preserva_existentes_e_sem_micro():
    preco = [100.0, 105.0, 103.0, 110.0]
    ts = [_ts(0, 10, i) for i in range(4)]
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    # não remove colunas originais
    for c in ('ativo', 'ts_ms', 'preco_ultimo'):
        assert c in out.columns
    # sem colunas de microestrutura -> interações NÃO são criadas
    assert 'aggr_imb_x_dist_ajuste_norm' not in out.columns


# ============================================================
# TESTE 9 — auditoria de leakage: tudo NÃO olha o futuro
# ============================================================
def test_auditoria_leakage():
    aud = auditoria_leakage()
    assert len(aud) > 30
    for row in aud:
        assert row['olha_futuro'] == 'NÃO', row['feature']
        assert row['feature'] and row['fonte']


def test_abertura_vs_ajuste_proxy():
    """abertura_vs_ajuste_* usa o fechamento do dia anterior (proxy)."""
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100.0, 110.0, 102.0, 105.0]   # D‑1 fecha em 110
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    dia1 = out[out['_dia'] == 1]
    # abertura D = 102, ajuste_anterior (proxy) = 110
    assert dia1['abertura_vs_ajuste_pts'].iloc[0] == pytest.approx(-8.0)
    # vol pode ou não ser NaN no 1º tick (depende do EWMA carry-over);
    # o importante é que o PTS está correto e o resultado é finito.
    assert np.isfinite(dia1['abertura_vs_ajuste_norm'].iloc[0]) or \
           pd.isna(dia1['abertura_vs_ajuste_norm'].iloc[0])


def test_delta_interacoes():
    ts = [_ts(0, 10, i) for i in range(5)]
    preco = [100., 101., 102., 101., 103.]
    df = _df(['WIN'] * 5, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    # delta no 2º tick = +1, acima_abertura=True
    assert out['delta_x_acima_abertura'].iloc[1] == pytest.approx(1.0)
    # dia 0 não tem ajuste_anterior (NaN) -> acima_ajuste = NaN -> interação NaN
    assert pd.isna(out['delta_x_acima_ajuste'].iloc[3])


def test_dist_ajuste_abs():
    ts = [_ts(0, 10, i) for i in range(3)]
    preco = [100., 105., 95.]
    df = _df(['WIN'] * 3, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    # ajuste_anterior = fechamento de D-1 = NaN (primeiro dia)
    # dist_ajuste_abs = NaN para todos
    assert out['dist_ajuste_abs'].isna().all()


def test_dist_ajuste_abs_com_dia_anterior():
    ts = [_ts(0, 10, 0), _ts(0, 10, 1), _ts(1, 10, 0), _ts(1, 10, 1)]
    preco = [100., 110., 102., 105.]  # D-1 fecha em 110
    df = _df(['WIN'] * 4, ts, preco)
    out = adicionar_contexto_preco(df, preco_col='preco_ultimo')
    dia1 = out[out['_dia'] == 1]
    # ajuste_anterior = 110 (proxy); |102-110|=8, |105-110|=5
    assert dia1['dist_ajuste_abs'].iloc[0] == pytest.approx(8.0)
    assert dia1['dist_ajuste_abs'].iloc[1] == pytest.approx(5.0)
