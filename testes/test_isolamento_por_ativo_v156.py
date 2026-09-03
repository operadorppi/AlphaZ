# -*- coding: utf-8 -*-
"""
testes/test_isolamento_por_ativo_v156.py — P0-A06 (v15.6).

O buffer e o relógio de limpeza do MarketState devem ser POR ATIVO: o avanço
de segundo de um ativo (ex: WIN) NÃO pode limpar o buffer de outro (ex: WDO
ainda agregando o segundo anterior), nem rotular features de outro ativo com
o seu segundo.

Cenários:
  1. WIN avança para S+1 e o buffer do WDO (segundo S) permanece intacto
  2. WDO fecha o segundo S com TODOS os seus trades (n=3) apesar do WIN ter
     avançado antes — sem subcontagem cross-asset
  3. Feature do WDO é rotulada com o SEGUNDO DO WDO (S), nunca com o do WIN
  4. Trade atrasado do próprio ativo (seg < seu corrente) não contamina o
     buffer do segundo atual
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.market_state import MarketState  # noqa: E402
from core.signal_engine import SignalEngine  # noqa: E402

S = 1_787_000_000  # segundo base (arbitrário)


def _motor():
    ms = MarketState(config={'book_split': 30})
    se = SignalEngine(ms, config={})
    return ms, se


def _neg(ms, se, ativo, ts_ms, preco, agressor='Comprador',
         comp='XP', vend='BTG', calcular=True):
    ok = ms.alimentar_negocio(ativo=ativo, ts_ms=ts_ms, preco=preco, qtd=1,
                              agressor=agressor, compradora=comp, vendedora=vend)
    assert ok, f'trade {ativo} ts={ts_ms} rejeitado'
    if calcular:
        se.calcular(ts_ms // 1000, skip_avaliar=True)


def test_win_avancar_nao_limpa_buffer_do_wdo():
    ms, se = _motor()
    # WDO: 3 trades no segundo S
    for k in range(3):
        _neg(ms, se, 'WDOU26', S * 1000 + k * 100, 5000.0 + k)
    # WIN avança para S+1
    _neg(ms, se, 'WINV26', (S + 1) * 1000, 170000.0)

    assert len(ms.buffer.get('WDOU26', [])) == 3, (
        'buffer do WDO foi limpo pelo avanço de segundo do WIN (P0-A06)')
    assert len(ms.buffer.get('WINV26', [])) == 1


def test_wdo_fecha_segundo_com_todos_trades_apesar_do_win():
    ms, se = _motor()
    for k in range(3):
        _neg(ms, se, 'WDOU26', S * 1000 + k * 100, 5000.0 + k)
    # WIN avança primeiro para S+1 (cenário do bug: limpava o WDO)
    _neg(ms, se, 'WINV26', (S + 1) * 1000, 170000.0)
    # WDO fecha o próprio segundo S
    _neg(ms, se, 'WDOU26', (S + 1) * 1000 + 50, 5000.5)

    hist = ms.historico.get('WDOU26', [])
    assert len(hist) == 1
    assert hist[0]['n'] == 3, (
        f'feature do WDO S deve ter n=3 (buffer completo), veio n={hist[0]["n"]}')


def test_feature_wdo_rotulada_com_segundo_do_wdo():
    ms, se = _motor()
    for k in range(3):
        _neg(ms, se, 'WDOU26', S * 1000 + k * 100, 5000.0 + k)
    _neg(ms, se, 'WINV26', (S + 1) * 1000, 170000.0)
    _neg(ms, se, 'WDOU26', (S + 1) * 1000 + 50, 5000.5)

    wdo_rows = [seg for (ativo, seg) in ms.features_por_seg if ativo == 'WDOU26']
    assert wdo_rows == [S], (
        f'feature do WDO rotulada com seg do WIN? rows={wdo_rows}')
    # Nenhuma linha do WDO no segundo do WIN
    assert (('WDOU26', S + 1)) not in ms.features_por_seg


def test_trade_atrasado_do_proprio_ativo_nao_contamina_buffer():
    ms, se = _motor()
    # WDO no segundo S+1 já aberto
    _neg(ms, se, 'WDOU26', (S + 1) * 1000, 5000.0)
    # Trade atrasado do MESMO ativo com seg S (já fechado)
    _neg(ms, se, 'WDOU26', S * 1000 + 900, 4999.0)

    buf = ms.buffer.get('WDOU26', [])
    assert len(buf) == 1, f'buffer corrente contaminado por trade atrasado: {len(buf)}'
    assert ms._neg_atrasados['WDOU26'] == 1
    # Fecha S+1: feature com n=1 (só o trade do segundo corrente)
    _neg(ms, se, 'WDOU26', (S + 2) * 1000, 5001.0)
    hist = ms.historico.get('WDOU26', [])
    assert hist and hist[0]['n'] == 1
