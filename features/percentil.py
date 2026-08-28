# -*- coding: utf-8 -*-
"""
features/percentil.py — PercentilTracker + RangeTracker + AccumulationTracker.

Trackers de percentis, range e acumulação extraídos de motor_rt_alphaz.py.
"""

import bisect
import time
from collections import deque, defaultdict

from .utils import classificar_corretora


class PercentilTracker:
    """Tracker de percentis com janela deslizante temporal."""

    def __init__(self, janela_segs=1800, amostra_minima=60):
        self.valores_ts = deque()
        self.ordenado = []
        self.janela_segs = janela_segs
        self.amostra_minima = amostra_minima

    def add(self, v, ts=None):
        ts = ts or time.time()
        self.valores_ts.append((ts, v))
        bisect.insort(self.ordenado, v)
        while self.valores_ts and ts - self.valores_ts[0][0] > self.janela_segs:
            old_ts, old_v = self.valores_ts.popleft()
            idx = bisect.bisect_left(self.ordenado, old_v)
            if idx < len(self.ordenado) and self.ordenado[idx] == old_v:
                self.ordenado.pop(idx)

    def percentil(self, p, fallback):
        if len(self.ordenado) < self.amostra_minima:
            return fallback
        idx = min(int(len(self.ordenado) * p), len(self.ordenado) - 1)
        return self.ordenado[idx]


class RangeTracker:
    """Detecta range de varredura: preço testando a mesma zona repetidamente."""

    def __init__(self, janela_segs=300, n_testes_min=3):
        self.precos = deque(maxlen=5000)
        self.janela_segs = janela_segs
        self.n_testes_min = n_testes_min
        self.range_topo = 0.0
        self.range_fundo = 0.0
        self.testes_topo = 0
        self.testes_fundo = 0
        self.estado = 'indefinido'
        self.expansao = 0.0
        self._prev_range = 0.0

    def atualizar(self, preco, ts):
        self.precos.append((ts, preco))
        while self.precos and ts - self.precos[0][0] > self.janela_segs:
            self.precos.popleft()
        if len(self.precos) < 10:
            return
        ps = [p for _, p in self.precos]
        topo = max(ps)
        fundo = min(ps)
        amplitude = topo - fundo
        if self._prev_range > 0:
            self.expansao = (amplitude - self._prev_range) / self._prev_range
        self._prev_range = amplitude
        tol = max(amplitude * 0.10, 5)
        self.testes_topo = sum(1 for p in ps if abs(p - topo) <= tol)
        self.testes_fundo = sum(1 for p in ps if abs(p - fundo) <= tol)
        self.range_topo = topo
        self.range_fundo = fundo
        margem = amplitude * 0.15
        if preco >= topo - margem:
            self.estado = 'topo'
        elif preco <= fundo + margem:
            self.estado = 'fundo'
        elif fundo + margem < preco < topo - margem:
            self.estado = 'dentro'
        else:
            self.estado = 'indefinido'

    def get_estado(self):
        return {
            'topo': round(self.range_topo, 1),
            'fundo': round(self.range_fundo, 1),
            'amplitude': round(self.range_topo - self.range_fundo, 1),
            'testes_topo': self.testes_topo,
            'testes_fundo': self.testes_fundo,
            'estado': self.estado,
            'expansao': round(self.expansao, 4),
            'n_amostras': len(self.precos),
        }


class AccumulationTracker:
    """Detecta acumulação por corretora no range e direção provável do rompimento."""

    def __init__(self, janela_segs=300):
        self.janela_segs = janela_segs
        self.flows = deque(maxlen=50000)
        self.saldo_corretora = defaultdict(lambda: {'c': 0, 'v': 0})

    def registrar(self, ts, broker, lado, preco, qtd):
        if not broker or broker in ('None', ''):
            return
        self.flows.append((ts, broker, lado, preco, qtd))
        sd = self.saldo_corretora[broker]
        if lado == 'Comprador':
            sd['c'] += qtd
        elif lado == 'Vendedor':
            sd['v'] += qtd

    def _limpar_antigos(self, ts):
        corte = ts - self.janela_segs
        while self.flows and self.flows[0][0] < corte:
            _, broker, lado, _, qtd = self.flows.popleft()
            sd = self.saldo_corretora.get(broker)
            if sd is None:
                continue
            if lado == 'Comprador':
                sd['c'] -= qtd
            elif lado == 'Vendedor':
                sd['v'] -= qtd
            if sd['c'] <= 0 and sd['v'] <= 0:
                self.saldo_corretora.pop(broker, None)

    def detectar(self, ts, range_topo, range_fundo, preco_atual):
        self._limpar_antigos(ts)
        if len(self.flows) < 20:
            return None
        amplitude = range_topo - range_fundo
        if amplitude < 10:
            return None
        margem = amplitude * 0.20
        if preco_atual >= range_topo - margem:
            zona = 'topo'
        elif preco_atual <= range_fundo + margem:
            zona = 'fundo'
        else:
            zona = 'meio'
        inst_c = inst_v = var_c = var_v = 0
        for broker, sd in self.saldo_corretora.items():
            c, v = sd['c'], sd['v']
            tipo = classificar_corretora(broker)
            if tipo == 'inst':
                inst_c += c
                inst_v += v
            else:
                var_c += c
                var_v += v
        inst_net = inst_c - inst_v
        var_net = var_c - var_v
        inst_comprando = inst_net > 50
        inst_vendendo = inst_net < -50
        varejo_comprando = var_net > 50
        varejo_vendendo = var_net < -50
        direcao = 'neutro'
        forca = 0.0
        if zona == 'topo':
            if inst_comprando and not varejo_comprando:
                direcao = 'cima'
                forca = min(1.0, abs(inst_net) / 200)
            elif varejo_comprando and inst_vendendo:
                direcao = 'baixo'
                forca = min(1.0, abs(var_net + abs(inst_net)) / 300)
            elif inst_comprando and varejo_comprando:
                direcao = 'baixo'
                forca = 0.3
        elif zona == 'fundo':
            if inst_vendendo and not varejo_vendendo:
                direcao = 'baixo'
                forca = min(1.0, abs(inst_net) / 200)
            elif varejo_vendendo and inst_comprando:
                direcao = 'cima'
                forca = min(1.0, abs(var_net + abs(inst_net)) / 300)
            elif inst_vendendo and varejo_vendendo:
                direcao = 'cima'
                forca = 0.3
        else:
            if inst_comprando and forca == 0:
                direcao = 'cima'
                forca = min(0.5, abs(inst_net) / 400)
            elif inst_vendendo and forca == 0:
                direcao = 'baixo'
                forca = min(0.5, abs(inst_net) / 400)
        return {
            'inst_comprando': inst_comprando,
            'inst_vendendo': inst_vendendo,
            'varejo_comprando': varejo_comprando,
            'varejo_vendendo': varejo_vendendo,
            'inst_net': round(inst_net),
            'var_net': round(var_net),
            'zona': zona,
            'direcao_provavel': direcao,
            'forca': round(forca, 3),
            'n_trades': len(self.flows),
        }
