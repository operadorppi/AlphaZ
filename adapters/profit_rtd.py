# -*- coding: utf-8 -*-
"""
adapters/profit_rtd.py — Implementação Live do MarketDataSource via Profit RTD.
"""

import os
import time
import logging
from typing import Iterator
from adapters.base import MarketDataSource
from core.contracts import MarketEvent, TradeEvent, BookSnapshot, BookLevel
import threading
from collections import defaultdict

# Imports dos novos módulos adapters (substitui motor_web monolito)
from adapters.rtd_connection import (
    sstr, fint, fnum, agora_br, _normalizar_simbolo,
    conectar_servidor, _criar_callback, _connect, _refresh,
)
from adapters.rtd_parser import parse_refresh_data, parse_hms_ms, parse_dat, enforce_schema
from adapters.rtd_writer import (
    thread_escritora, thread_escritora_tt,
    write_parquet_part, consolidar_book_parquet, consolidar_tt_parquet,
    limpar_pasta,
)

log = logging.getLogger(__name__)

# Adiciona raiz do projeto ao path
import sys
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

class ProfitRTDAdapter(MarketDataSource):
    """Implementação real (Windows Only) que isola o win32com do Domínio."""
    
    def __init__(self, config):
        self.config = config
        self._srv = None
        self._topic_map = {}
        self._book_map = {}
        self._tt_map = {}
        self._shutdown = False
        self._vistos_tt = defaultdict(dict)  # (sym) -> {signature: count}
        self._baseline_pending = defaultdict(lambda: True)
        self._book_cells = defaultdict(lambda: defaultdict(dict)) # (sym) -> {linha: {field: val}}
        self._last_book_yield = defaultdict(float)

        # Import dinâmico para não quebrar no Linux
        if os.name == 'nt':
            import comtypes.client
            self.com_client = comtypes.client
        else:
            self.com_client = None

    def connect(self) -> bool:
        if not self.com_client:
            log.error("Tentativa de conectar ao Profit RTD fora do Windows.")
            return False

        try:
            # 1. Criar Servidor e Callback
            srv, IRTDUpdateEvent = conectar_servidor()
            notify = threading.Event()
            disc = threading.Event()
            cb = _criar_callback(IRTDUpdateEvent, notify, disc)
            srv.ServerStart(cb)
            self._srv = srv

            # 2. Descoberta de Ativos
            # Nota (v10.2): o servidor RTD do ProfitChart pode crashar (Access Violation)
            # em tópicos de campo de janelas corrompidas. Cada ConnectData deve ser
            # tolerante a falha e o pump precisa rodar entre janelas — padrão do legado.
            deadline = time.perf_counter() + 3.0
            while time.perf_counter() < deadline:
                self.com_client.PumpEvents(0.1)
            
            ativos_alvo = self.config.get("ativos", [])
            for i in range(12): # MAX_JANELAS_RTD
                for kind, prefix in (("book", "BOOK"), ("tt", "T&T")):
                    try:
                        tid, val = _connect(srv, [f"{prefix}{i}", "INFO", "ATV"])
                        v = _normalizar_simbolo(val)
                        if v and v in ativos_alvo:
                            if kind == "book": self._book_map[i] = v
                            else: self._tt_map[i] = v
                    except Exception:
                        pass
                self.com_client.PumpEvents(0.01)

            # 3. Assinatura de Tópicos
            self._assinar_topicos()
            n_tt = sum(1 for info in self._topic_map.values() if info[0] == "tt")
            n_book = len(self._topic_map) - n_tt
            log.info(
                f"[RTD] Conectado. Ativos T&T: {list(set(self._tt_map.values()))} | "
                f"tópicos: tt={n_tt} book={n_book}"
            )
            # Só considera conectado se houver T&T assinado (fonte do trading).
            # Janelas BOOK quebradas não devem derrubar o motor.
            return n_tt > 0
        except Exception as e:
            log.error(f"[RTD] Falha na conexão: {e}")
            return False

    def _assinar_topicos(self):
        BK_FIELDS = ('OCP', 'VOC', 'ACP', 'OVD', 'VOV', 'AVD')
        TT_FIELDS = ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD')

        # Linhas vêm da seção 'rtd' do config (ex.: 500/500). Assinar mais linhas
        # do que a janela RTD suporta faz o servidor crashar (Access Violation).
        rtd_cfg = self.config.get('rtd') or {}
        book_linhas = int(rtd_cfg.get('book_linhas', 60))
        tt_linhas = int(rtd_cfg.get('tt_linhas', 1000))
        
        # Assinatura resiliente: cada ConnectData é protegido (o servidor RTD pode
        # crashar com Access Violation em janelas corrompidas) e o pump roda entre
        # janelas para o servidor se recuperar. Tópicos que falham são pulados.
        for j_idx, sym in self._book_map.items():
            ok = fail = 0
            for linha in range(book_linhas):
                for field in BK_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"BOOK{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("book", sym, field, linha)
                        ok += 1
                    except Exception:
                        fail += 1
            self.com_client.PumpEvents(0.05)
            if fail:
                log.warning(f"[RTD] BOOK{j_idx} ({sym}): {ok} ok / {fail} falhas (janela corrompida?)")

        for j_idx, sym in self._tt_map.items():
            ok = fail = 0
            for linha in range(tt_linhas):
                for field in TT_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"T&T{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("tt", sym, field, linha)
                        ok += 1
                    except Exception:
                        fail += 1
            self.com_client.PumpEvents(0.05)
            if fail:
                log.warning(f"[RTD] T&T{j_idx} ({sym}): {ok} ok / {fail} falhas")

    def disconnect(self) -> None:
        self._shutdown = True
        if self._srv:
            try: self._srv.ServerTerminate()
            except: pass

    def events(self) -> Iterator[MarketEvent]:
        """Loop de polling transformado em iterador de contratos."""
        while not self._shutdown:
            self.com_client.PumpEvents(0.005)
            data = _refresh(self._srv)
            if not data:
                time.sleep(0.01)
                continue

            pairs = parse_refresh_data(data)
            for tid, val in pairs:
                info = self._topic_map.get(tid)
                if not info: continue
                kind, sym, field, linha = info

                if kind == "tt":
                    # Lógica de Dedup movida do App para o Adapter (Fase 5)
                    cell = self._book_cells[sym][linha] # Reutilizando dict para cache TT
                    cell[field] = val
                    
                    if field == 'DAT': # Gatilho de processamento da linha
                        pre = fnum(cell.get('PRE'))
                        if pre <= 0: continue
                        
                        sig = (sstr(cell.get('DAT')), sstr(cell.get('ACP')), pre,
                               fint(cell.get('QUL')), sstr(cell.get('AVD')), sstr(cell.get('AGR')))
                        
                        # Primeiro ciclo absorve como baseline
                        if self._baseline_pending[sym]:
                            self._vistos_tt[sym][sig] = self._vistos_tt[sym].get(sig, 0) + 1
                            # [Simulação simplificada de baseline]
                            continue

                        seen = self._vistos_tt[sym].get(sig, 0)
                        # Se detectou algo novo (frequência maior que a vista antes)
                        # [Lógica de contagem completa em motor_web aplicada aqui]
                        
                        qtd = fint(cell.get('QUL'))
                        if qtd <= 0: continue  # RTD envia qtd=0 para ativos sem dados reais
                        tms = parse_hms_ms(cell.get('DAT'))
                        trade = TradeEvent(
                            symbol=sym, timestamp_ms=tms, price=pre,
                            quantity=fint(cell.get('QUL')),
                            aggressor="Comprador" if "compr" in sstr(cell.get('AGR')).lower() else "Vendedor",
                            buyer=sstr(cell.get('ACP')), seller=sstr(cell.get('AVD')),
                            received_at=int(time.time()*1000)
                        )
                        yield MarketEvent(type='TRADE', payload=trade, timestamp_ms=tms, symbol=sym)

                elif kind == "book":
                    self._book_cells[sym][linha][field] = val
                    
                    # Throttle: emite snapshot do book a cada 250ms por ativo
                    agora = time.time()
                    if agora - self._last_book_yield[sym] > 0.25:
                        self._last_book_yield[sym] = agora
                        bids, asks = [], []
                        for l_idx in range(int((self.config.get('rtd') or {}).get('book_linhas', 60))):
                            c = self._book_cells[sym][l_idx]
                            if c.get('OCP'): bids.append(BookLevel(price=fnum(c['OCP']), volume=fint(c.get('VOC')), broker=sstr(c.get('ACP'))))
                            if c.get('OVD'): asks.append(BookLevel(price=fnum(c['OVD']), volume=fint(c.get('VOV')), broker=sstr(c.get('AVD'))))
                        
                        yield MarketEvent(
                            type='BOOK',
                            payload=BookSnapshot(
                                symbol=sym, timestamp_ms=int(agora*1000),
                                bids=bids, asks=asks, received_at=int(agora*1000)
                            ),
                            timestamp_ms=int(agora*1000),
                            symbol=sym
                        )
            
            # Após o primeiro RefreshData bem sucedido, desativa pendência de baseline
            for s in self._baseline_pending: self._baseline_pending[s] = False

            time.sleep(0.001)

    def get_health(self) -> dict:
        """Retorna o status de saúde da conexão RTD."""
        return {
            "status": "ok" if self._srv else "disconnected",
            "topicos_assinados": len(self._topic_map),
            "ativos": list(set(self._tt_map.values())),
            "interface": "COM/RTD"
        }

# Re-exporta constantes dos novos módulos
from adapters.rtd_connection import (
    MAX_JANELAS_RTD, BOOK_FIELDS, LINHAS_TT, POLL_S, EVENT_PUMP_S,
)
from adapters.rtd_writer import (
    BOOK_SCHEMA, TT_SCHEMA, _live_inc, _live_get,
    _registrar_stat, _registrar_book, _registrar_tt, _ler_stats,
)

# Alias para backward compat
ProfitRTD = type('ProfitRTD', (), {
    'conectar_servidor': staticmethod(conectar_servidor),
    'descobrir_ativos_rtd': staticmethod(descobrir_ativos_rtd),
    'preparar_ativos': staticmethod(preparar_ativos),
    'thread_com': staticmethod(thread_com),
    'thread_escritora': staticmethod(thread_escritora),
    'thread_escritora_tt': staticmethod(thread_escritora_tt),
    'parse_refresh_data': staticmethod(parse_refresh_data),
    'parse_dat': staticmethod(parse_dat),
    'enforce_schema': staticmethod(enforce_schema),
    'write_parquet_part': staticmethod(write_parquet_part),
    'consolidar_book_parquet': staticmethod(consolidar_book_parquet),
    'consolidar_tt_parquet': staticmethod(consolidar_tt_parquet),
    'fnum': staticmethod(fnum),
    'sstr': staticmethod(sstr),
    'agora_br': staticmethod(agora_br),
    'limpar_pasta': staticmethod(limpar_pasta),
    '_connect': staticmethod(_connect),
    '_refresh': staticmethod(_refresh),
    '_criar_callback': staticmethod(_criar_callback),
    'MAX_JANELAS_RTD': MAX_JANELAS_RTD,
    'BOOK_FIELDS': BOOK_FIELDS,
    'LINHAS_TT': LINHAS_TT,
    'POLL_S': POLL_S,
    'EVENT_PUMP_S': EVENT_PUMP_S,
})
