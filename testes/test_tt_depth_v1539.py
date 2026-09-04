# -*- coding: utf-8 -*-
"""
testes/test_tt_depth_v1539.py — Profundidade de linhas T&T por ativo.

Motivacao (medida 2026-09-04 no RAW): WIN e WDO usam o teto de 500 linhas em
rajadas (max/100ms = 500 e 364); IND e DOL (~10 trades/min) com 500 linhas
guardam ~45-54 min de historico visivel e reentregam linhas antigas a cada
refresh. Com profundidade menor por ativo (IND 100, DOL 200), reduz-se a
reentrega/rejeicao sem perder rajadas.

Cenarios:
  1. Resolucao por prefixo exato (WINV26 -> WIN: 500);
  2. IND/DOL usam valores menores;
  3. prefixo mais longo vence (WIN_RLP vs WIN);
  4. simbolo desconhecido cai no default (rtd.tt_linhas);
  5. config sem tt_linhas_por_ativo usa default;
  6. case-insensitive.
"""

from adapters.profit_rtd import _linhas_tt_por_ativo

CFG = {
    'book_linhas': 500,
    'tt_linhas': 500,
    'tt_linhas_por_ativo': {'WIN': 500, 'WDO': 500, 'IND': 100, 'DOL': 200},
}


def test_win_mantem_500():
    assert _linhas_tt_por_ativo(CFG, 'WINV26') == 500
    assert _linhas_tt_por_ativo(CFG, 'WINZ26') == 500


def test_ind_dol_reduzidos():
    assert _linhas_tt_por_ativo(CFG, 'INDV26') == 100
    assert _linhas_tt_por_ativo(CFG, 'DOLV26') == 200


def test_wdo_500():
    assert _linhas_tt_por_ativo(CFG, 'WDOV26') == 500


def test_prefixo_mais_longo_vence():
    cfg = {'tt_linhas': 500,
           'tt_linhas_por_ativo': {'WIN': 500, 'WIN_RLP': 300}}
    assert _linhas_tt_por_ativo(cfg, 'WIN_RLP') == 300
    assert _linhas_tt_por_ativo(cfg, 'WINV26') == 500


def test_simbolo_desconhecido_cai_no_default():
    assert _linhas_tt_por_ativo(CFG, 'SOJA26') == 500
    assert _linhas_tt_por_ativo(CFG, '') == 500


def test_sem_por_ativo_usa_default():
    cfg = {'tt_linhas': 300}
    assert _linhas_tt_por_ativo(cfg, 'INDV26') == 300
    assert _linhas_tt_por_ativo({}, 'WINV26') == 500


def test_case_insensitive():
    assert _linhas_tt_por_ativo(CFG, 'winv26') == 500
    assert _linhas_tt_por_ativo(CFG, 'indv26') == 100
