# -*- coding: utf-8 -*-
"""
testes/test_historico_1_por_seg_v155.py — P0-A05 (v15.5).

O histórico de features (historico + features_por_seg) deve conter EXATAMENTE
1 linha por (ativo, segundo) FECHADO — nunca 1 linha por trade. N trades no
mesmo segundo são N eventos legítimos, mas produzem 1 feature final daquele
segundo (mesma granularidade do batch).

Sem o fix: 100 trades no mesmo segundo geravam 100 appends quase idênticos e
as janelas de história (aceleracao[-6], cvd_div[-10], range_vol[-60],
regime.detectar) liam "6/10/60 entradas" = frações de segundo em live vs
segundos inteiros no batch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.market_state import MarketState  # noqa: E402
from core.signal_engine import SignalEngine  # noqa: E402

# Epoch ms de um dia útil (segundos inteiros arbitrários para os testes)
BASE_MS = 1_787_000_000_000


def _montar_motor(batch_mode=False):
    ms = MarketState(config={'book_split': 30, 'features_seg_max': 10000})
    se = SignalEngine(ms, config={'features_seg_max': 10000})
    if batch_mode:
        se._batch_mode = True
    return ms, se


def _alimentar_e_calcular(ms, se, ativo, ts_ms_list, preco_ini=170000.0):
    """Simula o App: alimentar_negocio + signal.calcular por TRADE.

    Preços variam a cada trade (preco_ini + idx*0.2) para que o dedup interno
    do FeatureEngine (v10.2, por assinatura) não colapse os trades do teste —
    aqui o alvo é a granularidade do histórico, não o dedup de features.
    """
    for i, ts in enumerate(ts_ms_list):
        preco = preco_ini + (i % 7) * 0.2
        ok = ms.alimentar_negocio(
            ativo=ativo, ts_ms=ts, preco=preco, qtd=1 + (i % 3),
            agressor='Comprador' if i % 2 == 0 else 'Vendedor',
            compradora='XP' if i % 2 == 0 else 'BTG',
            vendedora='BTG' if i % 2 == 0 else 'XP')
        assert ok, f'trade ts={ts} rejeitado pelo sanity check'
        seg = ts // 1000
        se.calcular(seg, skip_avaliar=True)


def test_live_100_trades_mesmo_segundo_1_linha_no_historico():
    ms, se = _montar_motor()

    # 100 trades DENTRO do mesmo segundo (S)
    s0 = BASE_MS // 1000
    trades_s0 = [s0 * 1000 + k for k in range(100)]
    _alimentar_e_calcular(ms, se, 'WINV26', trades_s0)

    # Segundo ainda aberto: nada persistido (só fecha quando o próximo chega)
    assert len(se.state.historico.get('WINV26', [])) == 0, (
        'segundo aberto não pode gerar linha no histórico')

    # Chega 1 trade do segundo seguinte → fecha S → exatamente 1 linha
    trades_s1 = [(s0 + 1) * 1000 + 0]
    _alimentar_e_calcular(ms, se, 'WINV26', trades_s1)

    hist = se.state.historico.get('WINV26', [])
    assert len(hist) == 1, f'esperado 1 linha (segundo fechado), veio {len(hist)}'
    assert len(se.state.features_por_seg) == 1

    # Mais trades no mesmo S+1, depois fecha S+1 → 2 linhas, 2 segs distintos
    trades_s1_rest = [(s0 + 1) * 1000 + k for k in range(1, 50)]
    trades_s2 = [(s0 + 2) * 1000 + 0]
    _alimentar_e_calcular(ms, se, 'WINV26', trades_s1_rest + trades_s2)

    hist = se.state.historico.get('WINV26', [])
    segs_guardados = sorted({h['time_ms'] // 1000 for h in hist})
    assert len(hist) == 2, f'esperado 2 linhas (2 segundos fechados), veio {len(hist)}'
    assert segs_guardados == [s0, s0 + 1], segs_guardados
    assert len(se.state.features_por_seg) == 2


def test_live_feature_do_segundo_fechado_e_a_completa():
    """A linha persistida reflete o buffer COMPLETO do segundo (paridade batch)."""
    ms, se = _montar_motor()
    s0 = BASE_MS // 1000

    # 5 trades no mesmo segundo
    _alimentar_e_calcular(ms, se, 'WINV26', [s0 * 1000 + k for k in range(5)])
    # fecha o segundo
    _alimentar_e_calcular(ms, se, 'WINV26', [(s0 + 1) * 1000])

    hist = se.state.historico.get('WINV26', [])
    assert len(hist) == 1
    assert hist[0]['n'] == 5, (
        f"feature do segundo fechado deve ter n=5 (buffer completo), veio n={hist[0]['n']}")


def test_batch_continua_persistindo_1_linha_por_seg():
    """Batch mode: comportamento preservado — 1 linha por segundo.

    O replay/batch alimenta o segundo INTEIRO e chama calcular() UMA vez
    (não por trade). Recompute só na mudança de segundo; persiste 1 linha.
    """
    ms, se = _montar_motor(batch_mode=True)
    s0 = BASE_MS // 1000

    ts_list = [s0 * 1000 + k for k in range(3)]
    for i, ts in enumerate(ts_list):
        preco = 170000.0 + (i % 3) * 0.2
        ok = ms.alimentar_negocio(
            ativo='WINV26', ts_ms=ts, preco=preco, qtd=1 + (i % 2),
            agressor='Comprador', compradora='XP', vendedora='BTG')
        assert ok
    se.calcular(s0, skip_avaliar=True)  # UMA chamada para o segundo completo

    hist = se.state.historico.get('WINV26', [])
    assert len(hist) == 1, f'batch deve persistir 1 linha/seg, veio {len(hist)}'
    assert hist[0]['n'] == 3, f"batch n=3 (3 trades no segundo), veio {hist[0]['n']}"
