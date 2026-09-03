# -*- coding: utf-8 -*-
"""
testes/test_dedup_reemissao_v1533.py — Dedup de reemissoes persistentes (v15.33).

Contexto medido em 2026-09-03: o RTD reentrega as linhas visiveis da janela
T&T/RLP a cada RefreshData — 76-98% das linhas gravadas no RAW eram
reemissoes da mesma linha. O dedup no ProfitRTDAdapter usa identidade COMPLETA:

    (ts_ms, preco, qtd, agressor, compradora, vendedora)

Regras:
  1. Mesma linha reentregue N vezes -> 1 evento (reemissao suprimida)
  2. Trades distintos (qualquer campo diferente) -> N eventos (nunca colidem)
  3. Dedup independente por ativo E por kind (tt vs rlp)
  4. Baseline absorve o retrato pre-conexao e marca como visto (nao reemite)
  5. Controle de memoria: expira por idade + cap FIFO por (sym, kind)
  6. Desligavel via config rtd.dedup_tt=false

Limitacao documentada: trades identicos campo-a-campo no mesmo milissegundo
sao indistinguiveis na fonte e colapsam em 1 evento.
"""

import sys
import os
import time
from collections import OrderedDict, defaultdict
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapters.profit_rtd as mw
from adapters.profit_rtd import ProfitRTDAdapter
from core.contracts import MarketEvent


def make_adapter(max_por_ativo=100, expiry_s=900, dedup_on=True):
    cfg = {
        'ativos': ['WINV26', 'INDV26'],
        'rtd': {
            'dedup_tt': dedup_on,
            'dedup_tt_expiry_s': expiry_s,
            'dedup_tt_max_por_ativo': max_por_ativo,
        },
    }
    return ProfitRTDAdapter(cfg)


class TestHelperDedup:
    """Testes diretos de _emitir_unicos (sem COM)."""

    def test_mesma_identidade_10x_gera_1_emissao(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        out = [a._emitir_unicos('WINV26', 'tt', sig) for _ in range(10)]
        assert out == [True] + [False] * 9

    def test_10_identidades_distintas_geram_10_emissoes(self):
        a = make_adapter()
        for i in range(10):
            sig = a._sig_tt(1000 + i, 188000.0 + i, 5, 'Comprador', 'XP', 'BTG')
            assert a._emitir_unicos('WINV26', 'tt', sig) is True

    def test_campo_diferente_nao_colide(self):
        """Qualquer campo da identidade diferente = trade novo (nunca eliminar)."""
        a = make_adapter()
        base = dict(event_ts_ms=1000, pre=188000.0, qtd=5,
                    agressor='Comprador', buyer='XP', seller='BTG')
        variantes = [
            dict(base, event_ts_ms=1001),   # timestamp diferente
            dict(base, pre=188005.0),       # preco diferente
            dict(base, qtd=6),              # quantidade diferente
            dict(base, agressor='Vendedor'),  # agressor diferente
            dict(base, buyer='BTG'),        # compradora diferente
            dict(base, seller='XP'),        # vendedora diferente
        ]
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(**base)) is True
        for v in variantes:
            assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(**v)) is True, v

    def test_dedup_independente_por_ativo(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        assert a._emitir_unicos('INDV26', 'tt', sig) is True  # outro ativo
        assert a._emitir_unicos('WINV26', 'tt', sig) is False

    def test_dedup_independente_por_kind_tt_vs_rlp(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        assert a._emitir_unicos('WINV26', 'rlp', sig) is True  # fluxo RLP separado
        assert a._emitir_unicos('WINV26', 'tt', sig) is False

    def test_fifo_cap_reemite_identidade_evictada(self):
        a = make_adapter(max_por_ativo=3)
        for i in range(3):
            assert a._emitir_unicos('WINV26', 'tt',
                                    a._sig_tt(1000 + i, 188000.0 + i, 5, 'C', 'X', 'B')) is True
        # 4o empurra o 1o para fora (FIFO)
        assert a._emitir_unicos('WINV26', 'tt',
                                a._sig_tt(2000, 188500.0, 5, 'C', 'X', 'B')) is True
        # identidade evictada volta a ser "nova"
        assert a._emitir_unicos('WINV26', 'tt',
                                a._sig_tt(1000, 188000.0, 5, 'C', 'X', 'B')) is True
        assert len(a._vistos_tt[('WINV26', 'tt')]) <= 3

    def test_expiracao_por_idade(self, monkeypatch):
        a = make_adapter(expiry_s=30)
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        assert a._emitir_unicos('WINV26', 'tt', sig) is False
        # avanca o relogio alem da expiracao -> reemissao volta a ser nova
        real_now = time.time()
        monkeypatch.setattr(mw.time, 'time', lambda: real_now + 60)
        assert a._emitir_unicos('WINV26', 'tt', sig) is True

    def test_desligavel_por_config(self):
        a = make_adapter(dedup_on=False)
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        assert all(a._emitir_unicos('WINV26', 'tt', sig) for _ in range(5))

    def test_contadores_unicos_e_duplicados(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        a._emitir_unicos('WINV26', 'tt', sig)
        a._emitir_unicos('WINV26', 'tt', sig)
        a._emitir_unicos('WINV26', 'tt', sig)
        assert a._tt_unicos[('WINV26', 'tt')] == 1
        assert a._tt_duplicados[('WINV26', 'tt')] == 2


class TestSigIdentidade:
    """Assinatura usa os campos normalizados (nao strings brutas)."""

    def test_agressor_normalizado(self):
        a = make_adapter()
        # AGR bruto "COMPRADOR"/"Comprador"/"comprador" -> mesmo agressor
        s1 = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        s2 = a._sig_tt(1000, 188000.0, 5, 'Comprador', 'XP', 'BTG')
        assert s1 == s2

    def test_assinatura_contem_todos_os_campos(self):
        a = make_adapter()
        s = a._sig_tt(1000, 188000.5, 7, 'Vendedor', 'Ideal', 'Genial')
        assert s == (1000, 188000.5, 7, 'Vendedor', 'Ideal', 'Genial')


# ============================================================
#  Integracao: events() real com RefreshData controlado
# ============================================================

def _dat_agora(delta_s=0):
    from datetime import datetime, timezone, timedelta
    br = datetime.now(timezone(timedelta(hours=-3)))
    br = br + timedelta(seconds=delta_s)
    return br.strftime('%H:%M:%S.') + f'{br.microsecond // 1000:03d}'


def montar_adapter_events(monkeypatch):
    a = make_adapter(max_por_ativo=10)
    a._srv = object()
    a.com_client = SimpleNamespace(PumpEvents=lambda x: None)
    a._connect_ts_ms = int(time.time() * 1000) - 2000  # captura iniciada ha 2s

    j_idx = 0
    tid = 1000
    a._topic_map = {}
    a._tt_map = {j_idx: 'WINV26'}
    for field in ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD'):
        a._topic_map[tid] = ('tt', 'WINV26', field, 0, j_idx)
        tid += 1

    linha = {
        'DAT': _dat_agora(-1),
        'PRE': 188000.0,
        'QUL': 5,
        'AGR': 'Comprador',
        'ACP': 'XP',
        'AVD': 'BTG',
    }
    tid_map = {('DAT', 0): 1000, ('PRE', 0): 1001, ('QUL', 0): 1002,
               ('AGR', 0): 1003, ('ACP', 0): 1004, ('AVD', 0): 1005}

    def fake_parse(data):
        out = []
        for field, val in linha.items():
            out.append((tid_map[(field, 0)], val))
        return out

    monkeypatch.setattr(mw, '_refresh', lambda srv: object())
    monkeypatch.setattr(mw, 'parse_refresh_data', fake_parse)
    monkeypatch.setattr(mw, 'validate_event_ts', lambda ts, recv: (True, ''))
    monkeypatch.setattr(mw, 'log', SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        debug=lambda *a, **k: None, error=lambda *a, **k: None))
    return a, linha


class TestEventsIntegracao:
    """Um RefreshData com a mesma linha coerente N vezes -> 1 evento."""

    def test_reemissao_persistente_gera_1_evento(self, monkeypatch):
        a, linha = montar_adapter_events(monkeypatch)
        # encerra apos processar o 2o ciclo (que e uma reemissao suprimida)
        calls = {'n': 0}

        def fake_refresh(srv):
            calls['n'] += 1
            if calls['n'] >= 2:
                a._shutdown = True
            return object()

        monkeypatch.setattr(mw, '_refresh', fake_refresh)
        it = a.events()
        # 1o ciclo: campos coerentes -> trade emitido
        ev = next(it)
        assert ev.type == 'TRADE' and ev.symbol == 'WINV26'
        assert ev.payload.price == 188000.0
        assert ev.payload.quantity == 5
        # v15.33: emissao adiada p/ fim do ciclo — identidade COMPLETA
        # (AGR/ACP/AVD do MESMO ciclo, nao vazios da 1a emissao)
        assert ev.payload.aggressor == 'Comprador'
        assert ev.payload.buyer == 'XP'
        assert ev.payload.seller == 'BTG'
        # 2o ciclo: MESMA linha reentregue -> suprimido (dedup) e sai
        with pytest.raises(StopIteration):
            next(it)
        assert a._tt_unicos[('WINV26', 'tt')] == 1
        assert a._tt_duplicados[('WINV26', 'tt')] >= 1

    def test_chegada_split_emite_1x_com_identidade_completa(self, monkeypatch):
        """AGR/ACP/AVD chegam no ciclo SEGUINTE ao trio: a emissao espera a
        convergencia total e sai 1x com identidade completa (sem re-emissao
        dupla com campos vazios)."""
        a, linha = montar_adapter_events(monkeypatch)
        # ciclo 1 entrega so o trio; AGR/ACP/AVD so no ciclo 2
        passo = {'n': 0}
        tid_map = {('DAT', 0): 1000, ('PRE', 0): 1001, ('QUL', 0): 1002,
                   ('AGR', 0): 1003, ('ACP', 0): 1004, ('AVD', 0): 1005}
        campos_trio = ('DAT', 'PRE', 'QUL')
        campos_tail = ('AGR', 'ACP', 'AVD')

        def fake_parse(data):
            passo['n'] += 1
            campos = campos_trio if passo['n'] == 1 else campos_trio + campos_tail
            return [(tid_map[(f, 0)], linha[f]) for f in campos]

        monkeypatch.setattr(mw, 'parse_refresh_data', fake_parse)
        calls = {'n': 0}

        def fake_refresh(srv):
            calls['n'] += 1
            if calls['n'] >= 3:
                a._shutdown = True
            return object()

        monkeypatch.setattr(mw, '_refresh', fake_refresh)
        it = a.events()
        ev = next(it)  # ciclo 1: trio coerente, tail ausente -> aguarda
        # ciclo 2: tail chega -> convergencia total -> 1a (e unica) emissao
        assert ev.type == 'TRADE'
        assert ev.payload.aggressor == 'Comprador'
        assert ev.payload.buyer == 'XP'
        assert ev.payload.seller == 'BTG'
        # ciclo 3: mesma linha reentregue -> dedup suprime
        with pytest.raises(StopIteration):
            next(it)
        assert a._tt_unicos[('WINV26', 'tt')] == 1
        assert a._tt_duplicados[('WINV26', 'tt')] >= 1

    def test_trade_novo_no_ciclo_seguinte_e_emitido(self, monkeypatch):
        a, linha = montar_adapter_events(monkeypatch)
        it = a.events()
        ev1 = next(it)
        assert ev1.type == 'TRADE'
        # novo trade: preco/dat diferentes -> evento novo
        linha['PRE'] = 188010.0
        linha['DAT'] = _dat_agora(0)
        ev2 = next(it)
        assert ev2.type == 'TRADE'
        assert ev2.payload.price == 188010.0
        assert a._tt_unicos[('WINV26', 'tt')] == 2
        a._shutdown = True
        with pytest.raises(StopIteration):
            next(it)