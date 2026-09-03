# -*- coding: utf-8 -*-
"""
testes/test_baseline_tt_v154.py — Baseline R1 corrigido (v15.4, P0-A03).

Valida a política de baseline do ProfitRTDAdapter SEM COM: o baseline deve
absorver apenas o retrato PRÉ-conexão da janela (DAT anterior ao início da
captura) e NUNCA descartar um trade real (DAT >= início da captura).

Cenários:
  1. Retrato pré-conexão (DAT < connect) -> NÃO emitido, baseline segue ativo
  2. 1º trade novo do dia (DAT >= connect, janela vazia na conexão) -> EMITIDO
     (regressão do bug: motor ligado 08:45 com janela vazia perdia o 1º trade)
  3. Linhas seguintes (baseline já encerrado) -> sempre emitidas
  4. Reconexão no meio do pregão: retrato de trades pré-crash absorvido,
     trade pós-reconexão emitido
  5. DAT indeterminado (<= 0) durante baseline -> emitido (fallback receive_ts)
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


def _adapter(connect_ts_ms):
    """Instancia ProfitRTDAdapter real (sem COM — __init__ não conecta)."""
    from adapters.profit_rtd import ProfitRTDAdapter
    a = ProfitRTDAdapter(config={})
    a._connect_ts_ms = connect_ts_ms
    a._baseline_pending = defaultdict(lambda: True)
    return a


def test_retrato_pre_conexao_nao_emitido_e_baseline_continua():
    a = _adapter(connect_ts_ms=1_700_000_000_000)  # connect 10:00:00
    # Trade das 09:58 (antes da conexão) — retrato da janela
    assert a._deve_emitir_tt('WINV26', 1_699_998_000_000) is False
    assert a._baseline_pending['WINV26'] is True, (
        "baseline deve continuar ativo após absorver retrato pré-conexão")


def test_primeiro_trade_novo_do_dia_emitido():
    a = _adapter(connect_ts_ms=1_700_000_000_000)  # motor 08:45, connect 10:00
    # Janela vazia na conexão; 1º trade real chega depois (ex: abertura)
    assert a._deve_emitir_tt('WINV26', 1_700_120_000_000) is True, (
        "1º trade real com DAT >= connect NUNCA pode ser descartado (P0-A03)")
    assert a._baseline_pending['WINV26'] is False, (
        "baseline deve encerrar ao receber o 1º dado novo")


def test_linhas_seguintes_emitidas_sempre():
    a = _adapter(connect_ts_ms=1_700_000_000_000)
    a._baseline_pending['WINV26'] = False  # baseline já encerrado
    # Mesmo uma linha com DAT antigo não é bloqueada após baseline encerrado
    # (quem filtra DAT muito velho é o validate_event_ts, não o baseline)
    assert a._deve_emitir_tt('WINV26', 1_699_990_000_000) is True


def test_reconexao_meio_pregao_absorve_retrato_e_emite_novo():
    a = _adapter(connect_ts_ms=1_700_000_000_000)  # reconexão 10:00 (pós-crash)
    # Retrato pré-crash (09:58) — deve ser absorvido, não duplicado
    assert a._deve_emitir_tt('WDOU26', 1_699_998_000_000) is False
    assert a._baseline_pending['WDOU26'] is True
    # Trade real pós-reconexão (10:01) — emitido
    assert a._deve_emitir_tt('WDOU26', 1_700_060_000_000) is True
    assert a._baseline_pending['WDOU26'] is False


def test_dat_indeterminado_durante_baseline_emitido():
    a = _adapter(connect_ts_ms=1_700_000_000_000)
    # DAT <= 0 (indeterminado): não pode ser classificado como retrato —
    # emite (o fallback receive_ts decide o timestamp real)
    assert a._deve_emitir_tt('INDV26', 0) is True
    assert a._baseline_pending['INDV26'] is False


def test_baseline_por_ativo_independente():
    a = _adapter(connect_ts_ms=1_700_000_000_000)
    # WIN já recebeu dado novo (baseline encerrado); DOL ainda em baseline
    a._deve_emitir_tt('WINV26', 1_700_060_000_000)
    assert a._baseline_pending['WINV26'] is False
    assert a._baseline_pending['DOLV26'] is True, (
        "baseline de um ativo não pode afetar o de outro")
    # Retrato de DOL ainda é absorvido
    assert a._deve_emitir_tt('DOLV26', 1_699_990_000_000) is False
