# -*- coding: utf-8 -*-
"""
features/trade_features.py — JanelaFeatures + GeradorJanelas.

Features de microestrutura de T&T em janelas deslizantes de tempo.
"""

import math
import datetime as _dt
from collections import deque, defaultdict

from .utils import (
    ewma_update, hhi, entropia, fase_sessao, dias_ate_vencimento,
    _sanitize, _tod_de_ts,
)
from .vpin import VPINTracker
from .book_features import BookLevelFeatures
from .volume_profile import VolumeProfileTracker
from .kyle_lambda import KyleLambdaTracker


class JanelaFeatures:
    """Estado incremental de features de microestrutura para UM
    instrumento, dentro de uma janela deslizante de `janela_ms`."""

    ALPHAS_EWMA = {'curta': 0.35, 'media': 0.12, 'longa': 0.04}

    def __init__(self, janela_ms=100, vpin_bucket_vol=500, vpin_n_buckets=50,
                 simbolo=None, alpha_vol=0.1):
        self.janela_ms = janela_ms
        self.simbolo = simbolo
        self.alpha_vol = alpha_vol
        self.eventos = deque()

        self.vol_compra = 0
        self.vol_venda = 0
        self.vol_por_corretora = defaultdict(lambda: {'C': 0, 'V': 0})

        self.ewma_imb = {k: 0.0 for k in self.ALPHAS_EWMA}
        self.vpin = VPINTracker(bucket_vol=vpin_bucket_vol, n_buckets=vpin_n_buckets)

        self.preco_ultimo = 0.0
        self.preco_inicio_janela = 0.0
        self.n_total_eventos = 0

        self._cvd_total = 0.0
        self._cvd_div = 0
        self._topo_preco = 0.0
        self._topo_cvd = None
        self._fundo_preco = None
        self._fundo_cvd = None
        self._cvd_max = None
        self._cvd_min = None
        self._ewma_ret2 = 0.0

    def reset_diario(self):
        """Zera estado acumulado no dia anterior (evita vazamento entre dias)."""
        self.eventos.clear()
        self.vol_compra = 0
        self.vol_venda = 0
        self.vol_por_corretora.clear()
        self.ewma_imb = {k: 0.0 for k in self.ALPHAS_EWMA}
        self.vpin = VPINTracker(bucket_vol=self.vpin.bucket_vol,
                                n_buckets=self.vpin.n_buckets)
        self.preco_ultimo = 0.0
        self.preco_inicio_janela = 0.0
        self.n_total_eventos = 0
        self._cvd_total = 0.0
        self._cvd_div = 0
        self._topo_preco = 0.0
        self._topo_cvd = None
        self._fundo_preco = None
        self._fundo_cvd = None
        self._cvd_max = None
        self._cvd_min = None
        self._ewma_ret2 = 0.0

    def add_evento(self, ts_ms, preco, qtd, agressor, comp, vend):
        self._expirar(ts_ms)
        self.eventos.append((ts_ms, preco, qtd, agressor, comp, vend))
        self.n_total_eventos += 1

        if agressor == 'Comprador':
            self.vol_compra += qtd
        elif agressor == 'Vendedor':
            self.vol_venda += qtd

        comp = comp if comp and comp not in ('None', '') else None
        vend = vend if vend and vend not in ('None', '') else None
        if comp:
            self.vol_por_corretora[comp]['C'] += qtd
        if vend:
            self.vol_por_corretora[vend]['V'] += qtd

        if agressor == 'Comprador':
            self._cvd_total += qtd
        elif agressor == 'Vendedor':
            self._cvd_total -= qtd
        if self.preco_ultimo > 0:
            ret = preco / self.preco_ultimo - 1.0
            self._ewma_ret2 = ewma_update(self._ewma_ret2, ret * ret, self.alpha_vol)
        if preco > self._topo_preco:
            cvd_ant = self._topo_cvd
            self._topo_preco = preco
            self._topo_cvd = self._cvd_total
            if cvd_ant is not None:
                self._cvd_div = -1 if self._cvd_total < self._cvd_max else 0
        if self._fundo_preco is None or preco < self._fundo_preco:
            cvd_ant = self._fundo_cvd
            self._fundo_preco = preco
            self._fundo_cvd = self._cvd_total
            if cvd_ant is not None:
                self._cvd_div = 1 if self._cvd_total > self._cvd_min else 0
        self._cvd_max = max(getattr(self, '_cvd_max', self._cvd_total) or self._cvd_total, self._cvd_total)
        self._cvd_min = min(getattr(self, '_cvd_min', self._cvd_total) or self._cvd_total, self._cvd_total)

        vt = self.vol_compra + self.vol_venda
        imb_instantaneo = (self.vol_compra - self.vol_venda) / vt if vt > 0 else 0.0
        for k, alpha in self.ALPHAS_EWMA.items():
            self.ewma_imb[k] = ewma_update(self.ewma_imb[k], imb_instantaneo, alpha)

        self.vpin.add_evento(qtd, agressor)

        if not self.preco_inicio_janela:
            self.preco_inicio_janela = preco
        self.preco_ultimo = preco

    def _expirar(self, ts_agora):
        corte = ts_agora - self.janela_ms
        while self.eventos and self.eventos[0][0] < corte:
            ts0, preco0, qtd0, agr0, comp0, vend0 = self.eventos.popleft()
            if agr0 == 'Comprador':
                self.vol_compra -= qtd0
            elif agr0 == 'Vendedor':
                self.vol_venda -= qtd0
            if comp0 and comp0 in self.vol_por_corretora:
                d = self.vol_por_corretora[comp0]
                d['C'] -= qtd0
                if d['C'] <= 0 and d['V'] <= 0:
                    del self.vol_por_corretora[comp0]
            if vend0 and vend0 in self.vol_por_corretora:
                d = self.vol_por_corretora[vend0]
                d['V'] -= qtd0
                if d['C'] <= 0 and d['V'] <= 0:
                    del self.vol_por_corretora[vend0]
        self.preco_inicio_janela = self.eventos[0][1] if self.eventos else self.preco_ultimo

    def snapshot(self, ts_ms=None):
        ts_ms = ts_ms if ts_ms is not None else (self.eventos[-1][0] if self.eventos else 0)
        vt = self.vol_compra + self.vol_venda
        aggr_imb = (self.vol_compra - self.vol_venda) / vt if vt > 0 else 0.0

        vols_compra = [d['C'] for d in self.vol_por_corretora.values() if d['C'] > 0]
        vols_venda = [d['V'] for d in self.vol_por_corretora.values() if d['V'] > 0]

        precos_janela = [e[1] for e in self.eventos if e[1] > 0]
        if len(precos_janela) >= 2:
            mid_p = (max(precos_janela) + min(precos_janela)) / 2
            range_vol_bps = (max(precos_janela) - min(precos_janela)) / mid_p * 10000 if mid_p > 0 else 0.0
        else:
            range_vol_bps = 0.0

        snap = {
            'vol_compra': self.vol_compra, 'vol_venda': self.vol_venda, 'vol_total': vt,
            'aggr_imb': aggr_imb,
            'ewma_imb_curta': self.ewma_imb['curta'],
            'ewma_imb_media': self.ewma_imb['media'],
            'ewma_imb_longa': self.ewma_imb['longa'],
            'hhi_compra': hhi(vols_compra), 'hhi_venda': hhi(vols_venda),
            'entropy_compra': entropia(vols_compra), 'entropy_venda': entropia(vols_venda),
            'vpin': self.vpin.valor(),
            'preco_ultimo': self.preco_ultimo,
            'delta_preco_janela': self.preco_ultimo - self.preco_inicio_janela,
            'cvd_total': round(self._cvd_total, 1),
            'cvd_div': self._cvd_div,
            'realized_vol_bps': round(math.sqrt(self._ewma_ret2) * 10000, 2),
            'range_vol_bps': round(range_vol_bps, 2),
            'taxa_eventos': round(len(self.eventos) / (self.janela_ms / 1000), 1),
            'fase_sessao': fase_sessao(_tod_de_ts(ts_ms)),
            'dias_ate_venc': dias_ate_vencimento(self.simbolo) or 0 if self.simbolo else 0,
        }
        return {k: _sanitize(v) for k, v in snap.items()}


class GeradorJanelas:
    """Mantém uma JanelaFeatures por instrumento e emite snapshots a
    cada `passo_ms` de relógio."""

    def __init__(self, instrumentos, janela_ms=100, passo_ms=100,
                 vpin_bucket_vol=500, vpin_n_buckets=50):
        self.janelas = {
            a: JanelaFeatures(janela_ms, vpin_bucket_vol, vpin_n_buckets, simbolo=a)
            for a in instrumentos
        }
        self.book_trackers = {
            a: BookLevelFeatures() for a in instrumentos
        }
        self.vp_trackers = {
            a: VolumeProfileTracker(tick=5, value_area=0.70) for a in instrumentos
        }
        self.kyle_trackers = {
            a: KyleLambdaTracker(janela=200) for a in instrumentos
        }
        self.passo_ms = passo_ms
        self._proximo_corte = None
        self._ultimo_book = {}
        self._ultimo_preco = {}
        self._ultimo_dia = None

    def processar_evento(self, ativo, ts_ms, preco, qtd, agressor, comp, vend):
        if ativo not in self.janelas:
            return []

        _dia_atual = _dt.datetime.fromtimestamp(ts_ms / 1000).date()
        if self._ultimo_dia is not None and _dia_atual != self._ultimo_dia:
            for _ja in self.janelas.values():
                _ja.reset_diario()
            for _a in self.vp_trackers.values():
                _a.reset()
            for _a in self.kyle_trackers.values():
                _a.reset()
            for _a in self.book_trackers.values():
                _a.reset_diario()
            self._ultimo_book = {}
            self._ultimo_preco = {}
        self._ultimo_dia = _dia_atual

        saidas = []
        if self._proximo_corte is None:
            self._proximo_corte = (ts_ms // self.passo_ms + 1) * self.passo_ms

        while ts_ms >= self._proximo_corte:
            for a, ja in self.janelas.items():
                snap = ja.snapshot(self._proximo_corte)
                snap['ts_ms'] = self._proximo_corte
                if a in self._ultimo_book:
                    snap['book'] = self._ultimo_book[a]
                if a in self.vp_trackers and a in self._ultimo_preco:
                    snap['vp'] = self.vp_trackers[a].calcular(self._ultimo_preco[a])
                if a in self.kyle_trackers:
                    snap['kyle'] = self.kyle_trackers[a].calcular()
                saidas.append((a, snap))
            self._proximo_corte += self.passo_ms

        self.janelas[ativo].add_evento(ts_ms, preco, qtd, agressor, comp, vend)
        if ativo in self.vp_trackers:
            self.vp_trackers[ativo].atualizar(preco, qtd, agressor)
        if ativo in self.kyle_trackers:
            self.kyle_trackers[ativo].atualizar(preco, qtd, agressor)
        self._ultimo_preco[ativo] = preco
        return saidas

    def processar_book(self, ativo, ts_ms, book_snapshot):
        if ativo not in self.book_trackers:
            return None
        bt = self.book_trackers[ativo]
        book_features = bt.calcular(book_snapshot, ativo, ts_ms)
        if book_features:
            self._ultimo_book[ativo] = book_features
        return book_features
