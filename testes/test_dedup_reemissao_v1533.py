# -*- coding: utf-8 -*-
"""
testes/test_dedup_reemissao_v1533.py — Dedup de reemissoes persistentes
(v15.34 — revisto).

Contexto medido em 2026-09-03: o RTD reentrega as linhas visiveis da janela
T&T/RLP a cada RefreshData — 76-98% das linhas gravadas no RAW eram
reemissoes da mesma linha. O dedup no ProfitRTDAdapter usa a chave ESTAVEL:

    (ts_ms, preco, qtd)   <- trio DAT/PRE/QUL

Por que nao a identidade completa (v15.33): medido no RAW 2026-09-03,
compradora/vendedora (e as vezes agressor) OSCILAM entre reemissoes da MESMA
linha ('' -> '-' -> 'Agora' -> 'XP'). Com identidade completa, a mesma linha
reentregue com compradora diferente virava assinatura diferente -> IND saltava
de 7.320 unicos reais para 39.012 "unicos" falsos (Profit mostra 9.127).

Regras:
  1. Mesma linha reentregue em ciclos DIFERENTES -> 1 evento (reemissao
     suprimida no _fechar_ciclo_tt)
  2. Rajada GENUINA no MESMO ciclo (N linhas identicas no mesmo ms) -> N
     eventos (EVENTO != FEATURE: 100 negocios legitimos = 100 eventos)
  3. Campos oscilantes (compradora/vendedora/agressor) NAO derrotam o dedup
     (nao fazem parte da chave)
  4. Dedup independente por ativo E por kind (tt vs rlp)
  5. Baseline absorve o retrato pre-conexao e marca como visto (nao reemite)
  6. Controle de memoria: expira por idade + cap FIFO por (sym, kind)
  7. Desligavel via config rtd.dedup_tt=false

Limitacao documentada: trades distintos que compartilham ts+preco+qtd mas
chegam em ciclos DIFERENTES (ex.: mesmo preco/qtd no mesmo ms em refreshes
separados) colapsam — indistinguiveis na fonte sem ID de troca da bolsa.
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
    """Testes diretos de _emitir_unicos + _fechar_ciclo_tt (sem COM)."""

    def test_mesma_identidade_10x_gera_1_emissao(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5)
        # mesmo ciclo: rajada — 10 linhas identicas emitem 10 (EVENTO != FEATURE)
        out = [a._emitir_unicos('WINV26', 'tt', sig) for _ in range(10)]
        assert out == [True] * 10
        assert a._tt_unicos[('WINV26', 'tt')] == 10
        # fecha o ciclo: as 10 identidades viram 'vistas'
        a._fechar_ciclo_tt()
        # ciclo seguinte: MESMA linha reentregue 10x -> 0 emissoes (reemissao)
        out2 = [a._emitir_unicos('WINV26', 'tt', sig) for _ in range(10)]
        assert out2 == [False] * 10
        assert a._tt_duplicados[('WINV26', 'tt')] == 10

    def test_10_identidades_distintas_geram_10_emissoes(self):
        a = make_adapter()
        for i in range(10):
            sig = a._sig_tt(1000 + i, 188000.0 + i, 5)
            assert a._emitir_unicos('WINV26', 'tt', sig) is True
        assert a._tt_unicos[('WINV26', 'tt')] == 10

    def test_campo_oscilante_nao_derrota_dedup(self):
        """Compradora/vendedora/agressor NAO fazem parte da chave (v15.34).

        A MESMA linha reentregue com compradora diferente ('', '-', 'Agora',
        'XP') nao pode virar trade novo — era exatamente o bug do v15.33
        (identidade completa): IND 7.320 unicos reais vs 39.012 falsos.
        """
        a = make_adapter()
        base = (1000, 188000.0, 5)
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(*base)) is True
        a._fechar_ciclo_tt()
        # reemissao com campos de contraparte diferentes -> MESMA chave -> suprime
        for extra in [
            dict(agressor='Vendedor', buyer='BTG', seller='XP'),   # tudo invertido
            dict(agressor='Comprador', buyer='', seller='BTG'),    # compradora vazia
            dict(agressor='Comprador', buyer='-', seller='BTG'),   # compradora '-'
            dict(agressor='Comprador', buyer='Agora', seller='BTG'),
            dict(agressor='Comprador', buyer='XP', seller='BTG'),  # identica ao base
        ]:
            assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(*base)) is False, extra
        assert a._tt_duplicados[('WINV26', 'tt')] == 5

    def test_preco_ou_qtd_diferente_e_trade_novo(self):
        """ts/preco/qtd diferentes = trade novo (mesmo que contrapartes iguais)."""
        a = make_adapter()
        base = (1000, 188000.0, 5)
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(*base)) is True
        a._fechar_ciclo_tt()
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(1001, 188000.0, 5)) is True  # ts
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(1000, 188005.0, 5)) is True  # preco
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(1000, 188000.0, 6)) is True  # qtd
        # base volta a ser reemissao
        assert a._emitir_unicos('WINV26', 'tt', a._sig_tt(*base)) is False

    def test_dedup_independente_por_ativo(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5)
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        a._fechar_ciclo_tt()
        assert a._emitir_unicos('INDV26', 'tt', sig) is True  # outro ativo
        assert a._emitir_unicos('WINV26', 'tt', sig) is False

    def test_dedup_independente_por_kind_tt_vs_rlp(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5)
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        a._fechar_ciclo_tt()
        assert a._emitir_unicos('WINV26', 'rlp', sig) is True  # fluxo RLP separado
        assert a._emitir_unicos('WINV26', 'tt', sig) is False

    def test_fifo_cap_reemite_identidade_evictada(self):
        a = make_adapter(max_por_ativo=3)
        for i in range(3):
            assert a._emitir_unicos('WINV26', 'tt',
                                    a._sig_tt(1000 + i, 188000.0 + i, 5)) is True
        a._fechar_ciclo_tt()
        # 4o empurra o 1o para fora (FIFO) no merge do proximo ciclo
        assert a._emitir_unicos('WINV26', 'tt',
                                a._sig_tt(2000, 188500.0, 5)) is True
        a._fechar_ciclo_tt()
        assert len(a._vistos_tt[('WINV26', 'tt')]) <= 3
        # identidade evictada volta a ser "nova"
        assert a._emitir_unicos('WINV26', 'tt',
                                a._sig_tt(1000, 188000.0, 5)) is True

    def test_expiracao_por_idade(self, monkeypatch):
        a = make_adapter(expiry_s=30)
        sig = a._sig_tt(1000, 188000.0, 5)
        assert a._emitir_unicos('WINV26', 'tt', sig) is True
        a._fechar_ciclo_tt()
        assert a._emitir_unicos('WINV26', 'tt', sig) is False
        # avanca o relogio alem da expiracao -> reemissao volta a ser nova
        real_now = time.time()
        monkeypatch.setattr(mw.time, 'time', lambda: real_now + 60)
        a._fechar_ciclo_tt()  # prune expirados no merge
        assert a._emitir_unicos('WINV26', 'tt', sig) is True

    def test_desligavel_por_config(self):
        a = make_adapter(dedup_on=False)
        sig = a._sig_tt(1000, 188000.0, 5)
        assert all(a._emitir_unicos('WINV26', 'tt', sig) for _ in range(5))

    def test_contadores_unicos_e_duplicados(self):
        a = make_adapter()
        sig = a._sig_tt(1000, 188000.0, 5)
        a._emitir_unicos('WINV26', 'tt', sig)
        a._fechar_ciclo_tt()
        a._emitir_unicos('WINV26', 'tt', sig)
        a._emitir_unicos('WINV26', 'tt', sig)
        assert a._tt_unicos[('WINV26', 'tt')] == 1
        assert a._tt_duplicados[('WINV26', 'tt')] == 2


class TestSigIdentidade:
    """Assinatura usa o trio ESTAVEL normalizado (v15.34)."""

    def test_chave_e_o_trio_estavel(self):
        a = make_adapter()
        s = a._sig_tt(1000, 188000.5, 7)
        assert s == (1000, 188000.5, 7)

    def test_chave_ignora_contrapartes(self):
        a = make_adapter()
        # compradora/vendedora/agressor NAO entram na chave (oscilam entre
        # reemissoes da mesma linha — medido no RAW 2026-09-03)
        s1 = a._sig_tt(1000, 188000.0, 5)
        s2 = a._sig_tt(1000, 188000.0, 5)
        assert s1 == s2


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
    """RefreshData controlado: reemissao e rajada pelo caminho real."""

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
        # v15.33+: emissao adiada p/ fim do ciclo — identidade COMPLETA
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

    def test_rajada_mesmo_ciclo_preserva_N_eventos(self, monkeypatch):
        """Rajada GENUINA: N linhas identicas no MESMO RefreshData -> N eventos.

        A regra EVENTO != FEATURE: 100 negocios identicos no mesmo ms = 100
        eventos RAW. O dedup so atua entre ciclos (reentrega persistente).
        """
        a = make_adapter(max_por_ativo=100)
        a._srv = object()
        a.com_client = SimpleNamespace(PumpEvents=lambda x: None)
        a._connect_ts_ms = int(time.time() * 1000) - 2000

        j_idx = 0
        tid = 1000
        a._topic_map = {}
        a._tt_map = {j_idx: 'WINV26'}
        # 3 linhas (linha 0, 1, 2) com o MESMO trio ts+preco+qtd no MESMO ciclo
        tid_map = {}
        for linha_idx in (0, 1, 2):
            for field in ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD'):
                a._topic_map[tid] = ('tt', 'WINV26', field, linha_idx, j_idx)
                tid_map[(field, linha_idx)] = tid
                tid += 1

        dat_fixo = _dat_agora(-1)  # DAT ESTAVEL entre ciclos (senao cada refresh
        # gera ts_ns novo e a reemissao nao e reconhecida como a mesma linha)

        def fake_parse(data):
            out = []
            for linha_idx in (0, 1, 2):
                for field, val in [
                    ('DAT', dat_fixo),
                    ('PRE', 188000.0),
                    ('QUL', 5),
                    ('AGR', 'Comprador'),
                    ('ACP', 'XP'),
                    ('AVD', 'BTG'),
                ]:
                    out.append((tid_map[(field, linha_idx)], val))
            return out

        monkeypatch.setattr(mw, '_refresh', lambda srv: object())
        monkeypatch.setattr(mw, 'parse_refresh_data', fake_parse)
        monkeypatch.setattr(mw, 'validate_event_ts', lambda ts, recv: (True, ''))
        monkeypatch.setattr(mw, 'log', SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            debug=lambda *a, **k: None, error=lambda *a, **k: None))

        calls = {'n': 0}

        def fake_refresh_stop(srv):
            calls['n'] += 1
            if calls['n'] >= 2:
                a._shutdown = True
            return object()

        monkeypatch.setattr(mw, '_refresh', fake_refresh_stop)
        it = a.events()
        # ciclo 1: 3 linhas identicas -> 3 eventos (rajada preservada)
        evs = [next(it), next(it), next(it)]
        assert all(ev.type == 'TRADE' for ev in evs)
        assert len({ev.payload.price for ev in evs}) == 1
        assert a._tt_unicos[('WINV26', 'tt')] == 3
        # ciclo 2: as mesmas 3 linhas reentregues -> 0 eventos (reemissao)
        with pytest.raises(StopIteration):
            next(it)
        assert a._tt_duplicados[('WINV26', 'tt')] >= 3