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
from collections import defaultdict, OrderedDict

# Imports dos novos módulos adapters (substitui motor_web monolito)
from adapters.rtd_connection import (
    sstr, fint, fnum, agora_br, _normalizar_simbolo,
    conectar_servidor, _criar_callback, _connect, _refresh,
)
from adapters.rtd_parser import parse_refresh_data, parse_hms_ms, parse_dat, enforce_schema
from core.temporal import dat_to_epoch_ms, now_ns, next_sequence_id, validate_event_ts
from core.event_ordering import EventOrderingDetector
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
        self._book_recv_count = defaultdict(int)
        self._tt_map = {}
        self._rlp_map = {}  # janelas RLP (duplicatas de T&T)
        self._shutdown = False
        # Detector de ordenamento temporal (Fase 3)
        self._ordering_detector = EventOrderingDetector(
            late_threshold_ms=500,
            forward_jump_threshold_ms=60_000,
            backward_sequence_threshold=3,
        )
        self._vistos_tt = defaultdict(OrderedDict)  # (sym) -> OrderedDict[signature -> True]
        self._tt_recebidos = defaultdict(int)  # (sym) -> total trades received (running counter)
        self._baseline_pending = defaultdict(lambda: True)
        self._book_cells = defaultdict(lambda: defaultdict(dict)) # (sym) -> {linha: {field: val}}
        self._last_book_yield = defaultdict(float)
        # Limite de memória para dedup (LRU eviction)
        self._dedup_max_per_ativo = 50000  # max 50K assinaturas por ativo

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
                            if kind == "book":
                                self._book_map[i] = v
                            else:
                                # v12.5: Janela duplicata = RLP (Registro de Livros e Posicoes)
                                if v not in self._tt_map.values():
                                    self._tt_map[i] = v
                                else:
                                    # Marcar como RLP para gravacao separada
                                    self._rlp_map[i] = v
                                    log.info(f"[RTD] T&T{i} ({v}): mapeado como RLP")
                    except Exception as e:
                        log.debug(f"[RTD] Window {i}/{kind.upper()} not available: {e}")
                self.com_client.PumpEvents(0.01)

            # 3. Assinatura de Tópicos
            self._assinar_topicos()
            n_tt = sum(1 for info in self._topic_map.values() if info[0] == "tt")
            n_book = len(self._topic_map) - n_tt
            log.info(
                f"[RTD] Conectado. Ativos T&T: {list(set(self._tt_map.values()))} | "
                f"tópicos: tt={n_tt} book={n_book} | "
                f"tt_map: {dict(self._tt_map)} | book_map: {dict(self._book_map)}"
            )
            # Só considera conectado se houver T&T assinado (fonte do trading).
            # Janelas BOOK quebradas não devem derrubar o motor.
            return n_tt > 0
        except Exception as e:
            log.error(f"[RTD] Falha na conexão: {e}")
            return False

    def _assinar_topicos(self):
        BK_FIELDS = ('OCP', 'VOC', 'ACP', 'OVD', 'VOV', 'AVD')
        TT_FIELDS = ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD', 'AGAG')

        # Linhas vêm da seção 'rtd' do config (ex.: 500/500). Assinar mais linhas
        # do que a janela RTD suporta faz o servidor crashar (Access Violation).
        rtd_cfg = self.config.get('rtd') or {}
        book_linhas = int(rtd_cfg.get('book_linhas', 500))
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

        # v12.5: Assinar topicos RLP (janelas duplicadas)
        for j_idx, sym in self._rlp_map.items():
            ok = fail = 0
            for linha in range(tt_linhas):
                for field in TT_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"T&T{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("rlp", sym, field, linha)
                        ok += 1
                    except Exception:
                        fail += 1
            self.com_client.PumpEvents(0.05)
            if fail:
                log.warning(f"[RTD] RLP T&T{j_idx} ({sym}): {ok} ok / {fail} falhas")
            else:
                log.info(f"[RTD] RLP T&T{j_idx} ({sym}): {ok} topicos assinados")

    def disconnect(self) -> None:
        self._shutdown = True
        if self._srv:
            try:
                self._srv.ServerTerminate()
            except Exception as e:
                log.warning(f"[RTD] Erro ao desconectar: {e}")

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
                    # Lógica de Dedup (Fase 1 — corrigida)
                    cell = self._book_cells[sym][linha]
                    cell[field] = val
                    
                    if field == 'DAT':  # Gatilho de processamento da linha
                        pre = fnum(cell.get('PRE'))
                        if pre <= 0: continue
                        qtd = fint(cell.get('QUL'))
                        if qtd <= 0: continue  # RTD envia qtd=0 para ativos sem dados reais
                        
                        # Assinatura determinística do negócio.
                        # Campos: DAT + ACP + PRE + QUL + AVD + AGR + AGAG
                        # O AGAG (agressor agregado) é incluido para distinguir
                        # trades que podem ter a mesma combinação básica mas
                        # que o Profit classifica diferentemente (ex: direto
                        # vs carteira).
                        sig = (
                            sstr(cell.get('DAT')),
                            sstr(cell.get('ACP')),
                            pre,
                            qtd,
                            sstr(cell.get('AVD')),
                            sstr(cell.get('AGR')),
                            sstr(cell.get('AGAG')),
                        )
                        
                        # Primeiro refresh absorve como baseline (evita emitir
                        # histórico acumulado na primeira chamada de RefreshData)
                        if self._baseline_pending[sym]:
                            self._vistos_tt[sym][sig] = True
                            continue

                        # DEDUP: se a assinatura já foi vista, NÃO emitir.
                        # O RTD mantém linhas T&T persistentes e pode reenviar
                        # o mesmo trade em refreshes subsequentes.
                        if sig in self._vistos_tt[sym]:
                            continue  # Duplicata — descartar silenciosamente
                        
                        # Marcar como visto (LRU eviction se exceder limite)
                        vistos = self._vistos_tt[sym]
                        vistos[sig] = True
                        self._tt_recebidos[sym] += 1
                        if len(vistos) > self._dedup_max_per_ativo:
                            # Remove o item mais antigo (primeiro inserido)
                            vistos.popitem(last=False)
                        
                        # Fase 2: Preservar timestamp do mercado (DAT do Profit)
                        # NUNCA usar wall clock como timestamp do evento.
                        dat_str = sstr(cell.get('DAT'))
                        event_ts_ms = dat_to_epoch_ms(dat_str)
                        receive_ns = now_ns()

                        # Se DAT invalido, usar receive_ts como fallback (documentado)
                        if event_ts_ms <= 0:
                            event_ts_ms = receive_ns // 1_000_000
                            log.warning(f"[RTD] DAT invalido '{dat_str}' para {sym}, usando receive_ts como fallback")

                        # Validar timestamp (rejeitar se muito no futuro/passado)
                        valido, motivo = validate_event_ts(event_ts_ms, receive_ns)
                        if not valido:
                            log.warning(f"[RTD] {sym}: timestamp rejeitado: {motivo} (DAT={dat_str})")
                            continue

                        # Fase 3: Detectar anomalias temporais
                        ord_result = self._ordering_detector.check(sym, event_ts_ms, receive_ns)

                        if ord_result.action == "REJECT":
                            # Duplicatas e timestamps invalidos sao rejeitados
                            continue

                        if ord_result.is_late:
                            log.debug(f"[RTD] {sym}: evento atrasado lag={ord_result.lag_ms}ms")
                        if ord_result.is_out_of_order:
                            log.warning(f"[RTD] {sym}: fora de ordem gap={ord_result.gap_ms}ms ({ord_result.reason})")
                        if ord_result.is_forward_jump:
                            log.warning(f"[RTD] {sym}: salto temporal {ord_result.gap_ms}ms")
                        if ord_result.is_backward_sequence:
                            log.warning(f"[RTD] {sym}: sequencia regressiva ({ord_result.reason})")

                        seq_id = next_sequence_id()
                        trade = TradeEvent(
                            symbol=sym, timestamp_ms=event_ts_ms, price=pre,
                            quantity=qtd,
                            aggressor="Comprador" if "compr" in sstr(cell.get('AGR')).lower() else "Vendedor",
                            buyer=sstr(cell.get('ACP')), seller=sstr(cell.get('AVD')),
                            received_at_ns=receive_ns,
                            sequence_id=seq_id,
                        )
                        # v14: incluir janela_id no MarketEvent
                        janela_idx = next((k for k, v in self._tt_map.items() if v == sym), 0)
                        yield MarketEvent(type='TRADE', payload=trade, timestamp_ms=event_ts_ms,
                                          symbol=sym, janela_id=janela_idx,
                                          window_name=f'T&T{janela_idx}')

                elif kind == "rlp":
                    # v12.5: RLP (Registro de Livros e Posicoes) - mesmo fluxo do T&T
                    # mas com flag separada para gravacao distinta
                    cell = self._book_cells[sym][linha]
                    cell[field] = val
                    
                    if field == 'DAT':
                        pre = fnum(cell.get('PRE'))
                        if pre <= 0: continue
                        qtd = fint(cell.get('QUL'))
                        if qtd <= 0: continue
                        
                        sig = ('rlp', sstr(cell.get('DAT')), sstr(cell.get('ACP')),
                               pre, qtd, sstr(cell.get('AVD')),
                               sstr(cell.get('AGR')),
                               sstr(cell.get('AGAG')))
                        
                        if self._baseline_pending[sym]:
                            self._vistos_tt[sym][sig] = True
                            continue
                        if sig in self._vistos_tt[sym]:
                            continue
                        
                        vistos = self._vistos_tt[sym]
                        vistos[sig] = True
                        self._tt_recebidos[sym] += 1
                        if len(vistos) > self._dedup_max_per_ativo:
                            vistos.popitem(last=False)
                        
                        dat_str = sstr(cell.get('DAT'))
                        event_ts_ms = dat_to_epoch_ms(dat_str)
                        receive_ns = now_ns()
                        if event_ts_ms <= 0:
                            event_ts_ms = receive_ns // 1_000_000
                        
                        valido, motivo = validate_event_ts(event_ts_ms, receive_ns)
                        if not valido: continue
                        
                        ord_result = self._ordering_detector.check(sym, event_ts_ms, receive_ns)
                        if ord_result.action == "REJECT": continue
                        
                        seq_id = next_sequence_id()
                        trade = TradeEvent(
                            symbol=sym, timestamp_ms=event_ts_ms, price=pre,
                            quantity=qtd,
                            aggressor="Comprador" if "compr" in sstr(cell.get('AGR')).lower() else "Vendedor",
                            buyer=sstr(cell.get('ACP')), seller=sstr(cell.get('AVD')),
                            received_at_ns=receive_ns,
                            sequence_id=seq_id,
                        )
                        # v14: RLP com janela_id
                        janela_idx = next((k for k, v in self._rlp_map.items() if v == sym), 0)
                        yield MarketEvent(type='RLP', payload=trade, timestamp_ms=event_ts_ms,
                                          symbol=sym, janela_id=janela_idx,
                                          window_name=f'T&T{janela_idx}', is_rlp=True)

                elif kind == "book":
                    self._book_cells[sym][linha][field] = val
                    self._book_recv_count[sym] = self._book_recv_count.get(sym, 0) + 1
                    
                    # Throttle: emite snapshot do book a cada 100ms por ativo (alinhado com janela ML)
                    agora = time.time()
                    if agora - self._last_book_yield[sym] > 0.10:
                        self._last_book_yield[sym] = agora
                        receive_ns = now_ns()
                        bids, asks = [], []
                        for l_idx in range(int((self.config.get('rtd') or {}).get('book_linhas', 500))):
                            c = self._book_cells[sym][l_idx]
                            if c.get('OCP'): bids.append(BookLevel(price=fnum(c['OCP']), volume=fint(c.get('VOC')), broker=sstr(c.get('ACP'))))
                            if c.get('OVD'): asks.append(BookLevel(price=fnum(c['OVD']), volume=fint(c.get('VOV')), broker=sstr(c.get('AVD'))))
                        
                        book_ts = int(agora*1000)
                        janela_idx = next((k for k, v in self._book_map.items() if v == sym), 0)
                        yield MarketEvent(
                            type='BOOK',
                            payload=BookSnapshot(
                                symbol=sym, timestamp_ms=book_ts,
                                bids=bids, asks=asks, received_at_ns=receive_ns
                            ),
                            timestamp_ms=book_ts,
                            symbol=sym,
                            janela_id=janela_idx,
                            window_name=f'BOOK{janela_idx}'
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
            "interface": "COM/RTD",
            "dedup_stats": self._dedup_stats(),
            "ordering_stats": self._ordering_detector.get_stats_for_dashboard(),
        }

    def _dedup_stats(self):
        """Retorna estatísticas de deduplicação por ativo."""
        return {
            sym: {
                'asssinaturas_vistas': len(vistos),
                'tt_recebidos': self._tt_recebidos.get(sym, 0),
                'baseline_pendente': self._baseline_pending.get(sym, False),
            }
            for sym, vistos in self._vistos_tt.items()
        }

    def _reset_dedup(self):
        """Reseta estado de deduplicação (para testes)."""
        self._vistos_tt = defaultdict(OrderedDict)
        self._baseline_pending = defaultdict(lambda: True)

# Re-exporta constantes dos novos módulos
from adapters.rtd_connection import (
    MAX_JANELAS_RTD, BOOK_FIELDS, LINHAS_TT, POLL_S, EVENT_PUMP_S,
    descobrir_ativos_rtd, preparar_ativos,
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
