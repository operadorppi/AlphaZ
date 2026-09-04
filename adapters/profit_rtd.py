# -*- coding: utf-8 -*-
"""
adapters/profit_rtd.py — Implementação Live do MarketDataSource via Profit RTD.
"""

import os
import time
import logging
from datetime import datetime
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
from core.lograte import LogRateLimit
from adapters.rtd_writer import (
    thread_escritora, thread_escritora_tt,
    write_parquet_part, consolidar_book_parquet, consolidar_tt_parquet,
    limpar_pasta,
)

log = logging.getLogger(__name__)


def _hora_evt(ts_ms):
    """Formata ts epoch-ms em HH:MM:SS.mmm (fuso local/Brasília) p/ logs."""
    try:
        return datetime.fromtimestamp(ts_ms / 1000.0).strftime('%H:%M:%S.%f')[:-3]
    except Exception:
        return str(ts_ms)


# Adiciona raiz do projeto ao path
import sys
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _linhas_tt_por_ativo(rtd_cfg, sym):
    """Profundidade de linhas T&T por ativo (v15.39).

    Rajadas de WIN/WDO usam o teto de 500 linhas (medido 2026-09-04: max/100ms
    = 500 em WIN, 364 em WDO). IND/DOL (~10 trades/min) com 500 linhas guardam
    ~45-54 min de historico visivel -> reentrega massiva de linhas antigas e
    rejeicoes por >300s. Para eles bastam 100-200 linhas (rajada medida: IND
    26/100ms, DOL 77/100ms), com folga 3-4x.

    Resolucao por PREFIXO do simbolo (WINV26 -> 'WIN'), mais longo primeiro,
    com fallback para rtd.tt_linhas.
    """
    rtd_cfg = rtd_cfg or {}
    por_ativo = rtd_cfg.get('tt_linhas_por_ativo') or {}
    default = int(rtd_cfg.get('tt_linhas', 500))
    sym_u = str(sym).upper()
    melhor = None  # (tamanho_do_prefixo, linhas)
    for prefixo, n in por_ativo.items():
        p = str(prefixo).upper()
        if p and sym_u.startswith(p):
            if melhor is None or len(p) > melhor[0]:
                melhor = (len(p), int(n))
    return melhor[1] if melhor else default


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
            backward_sequence_threshold=100,
        )
        self._tt_recebidos = defaultdict(int)  # (sym) -> total trades received (running counter)
        # v15.34: dedup de reemissoes persistentes da janela T&T/RLP.
        # Medicao 2026-09-03: o RTD reentrega as linhas visiveis a cada
        # RefreshData e 76-98% das linhas gravadas eram reemissoes. A chave e
        # o trio ESTAVEL (ts + preco + qtd = DAT/PRE/QUL): compradora/vendedora
        # (e as vezes agressor) OSCILAM entre reemissoes da MESMA linha
        # ('' -> '-' -> 'Agora' -> 'XP') — inclui-los na chave (v15.33) fazia a
        # mesma linha reentregue parecer trade novo (IND: 7.320 unicos reais vs
        # 39.012 com identidade completa). Rajadas GENUINAS (N trades identicos
        # no mesmo ms, mesmo ciclo de RefreshData) sao preservadas: dentro do
        # MESMO ciclo nada e suprimido; a reentrega em ciclos posteriores sim.
        rtd_cfg = self.config.get('rtd') or {}
        self._dedup_tt_on = bool(rtd_cfg.get('dedup_tt', True))
        self._dedup_tt_expiry_s = float(rtd_cfg.get('dedup_tt_expiry_s', 900))
        self._dedup_tt_max = int(rtd_cfg.get('dedup_tt_max_por_ativo', 200_000))
        # v15.36: console limpo — avisos repetidos (timestamp rejeitado,
        # fora de ordem, salto, sequencia) agregados por janela com contador.
        # Janela de 60s: condição persistente = no máx. 1 linha/min (não 1 a
        # cada 5s); a 1ª ocorrência loga na hora.
        self._lograte = LogRateLimit(janela_s=float(rtd_cfg.get('log_janela_s', 60.0)),
                                     logger=log)
        self._vistos_tt = defaultdict(OrderedDict)  # (sym, kind) -> OrderedDict{sig: receive_ns} — persistente entre ciclos
        self._vistos_ciclo = defaultdict(set)       # (sym, kind) -> sigs emitidos NESTE ciclo (merge no fim)
        self._tt_unicos = defaultdict(int)          # (sym, kind) -> emitidos unicos
        self._tt_duplicados = defaultdict(int)      # (sym, kind) -> reemissoes suprimidas
        self._baseline_pending = defaultdict(lambda: True)
        self._connect_ts_ms = 0  # início da captura (wall clock) — baseline compara DAT com isto
        self._cell_lote = defaultdict(dict)  # (sym, linha) -> {field: lote_num} — ciclo RefreshData de cada campo
        self._lote_atual = 0  # contador de ciclos RefreshData
        self._book_cells = defaultdict(lambda: defaultdict(dict))  # (sym, kind, janela) -> {linha: {field: val}}
        self._last_book_yield = defaultdict(float)
        # v15.33: linhas cujo trio DAT/PRE/QUL coesionou neste ciclo — a
        # decisão de emissão é ADIADA para o fim do lote RefreshData (os campos
        # de identidade AGR/ACP/AVD chegam depois no mesmo ciclo; emitir no
        # meio gerava identidade incompleta + re-emissão dupla).
        self._coerentes_do_ciclo = []
        self._coerentes_ciclo_set = set()

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
            if n_tt > 0:
                return True
            # v15.2: sem janelas T&T (Profit fechado / janelas não configuradas) —
            # retorna False para o App tentar de novo; encerra o servidor criado
            # para não vazar objetos COM a cada tentativa.
            log.warning(
                "[RTD] Nenhuma janela T&T encontrada — Profit aberto? Janelas "
                "T&T configuradas? O motor vai tentar de novo em 30s."
            )
            try:
                self._srv.ServerTerminate()
            except Exception:
                pass
            self._srv = None
            return False
        except Exception as e:
            log.error(f"[RTD] Falha na conexão: {e}")
            try:
                if self._srv is not None:
                    self._srv.ServerTerminate()
            except Exception:
                pass
            self._srv = None
            return False

    def _assinar_topicos(self):
        BK_FIELDS = ('OCP', 'VOC', 'ACP', 'OVD', 'VOV', 'AVD')
        TT_FIELDS = ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD', 'AGAG')

        # Linhas vêm da seção 'rtd' do config (ex.: 500/500). Assinar mais linhas
        # do que a janela RTD suporta faz o servidor crashar (Access Violation).
        # v15.39: T&T usa profundidade POR ATIVO (_linhas_tt_por_ativo) —
        # WIN/WDO 500 (rajadas), IND 100 / DOL 200 (reduz reentrega de linhas
        # antigas em ativos de baixo volume). BOOK permanece 500 global.
        rtd_cfg = self.config.get('rtd') or {}
        book_linhas = int(rtd_cfg.get('book_linhas', 500))
        
        # Assinatura resiliente: cada ConnectData é protegido (o servidor RTD pode
        # crashar com Access Violation em janelas corrompidas) e o pump roda entre
        # janelas para o servidor se recuperar. Tópicos que falham são pulados.
        for j_idx, sym in self._book_map.items():
            ok = fail = 0
            for linha in range(book_linhas):
                for field in BK_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"BOOK{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("book", sym, field, linha, j_idx)
                        ok += 1
                    except Exception:
                        fail += 1
            self.com_client.PumpEvents(0.05)
            if fail:
                log.warning(f"[RTD] BOOK{j_idx} ({sym}): {ok} ok / {fail} falhas (janela corrompida?)")

        for j_idx, sym in self._tt_map.items():
            ok = fail = 0
            linhas = _linhas_tt_por_ativo(rtd_cfg, sym)
            for linha in range(linhas):
                for field in TT_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"T&T{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("tt", sym, field, linha, j_idx)
                        ok += 1
                    except Exception:
                        fail += 1
            self.com_client.PumpEvents(0.05)
            if fail:
                log.warning(f"[RTD] T&T{j_idx} ({sym}): {ok} ok / {fail} falhas")

        # v12.5: Assinar topicos RLP (janelas duplicadas)
        for j_idx, sym in self._rlp_map.items():
            ok = fail = 0
            linhas = _linhas_tt_por_ativo(rtd_cfg, sym)
            for linha in range(linhas):
                for field in TT_FIELDS:
                    try:
                        tid, _ = _connect(self._srv, [f"T&T{j_idx}", field, str(linha)])
                        self._topic_map[tid] = ("rlp", sym, field, linha, j_idx)
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

    def _sig_tt(self, event_ts_ms, pre, qtd):
        """Chave ESTAVEL do trade p/ dedup de reemissao (v15.34).

        Identidade = trio DAT/PRE/QUL (ts + preco + qtd). Medicao 2026-09-03:
        compradora/vendedora (e as vezes agressor) OSCILAM entre reemissoes da
        MESMA linha — inclui-los na chave fazia a mesma linha reentregue
        parecer trade novo. O trio e o unico conjunto estavel entre reemissoes
        e e exatamente o que o Profit entrega sempre.
        """
        return (int(event_ts_ms), float(pre), int(qtd))

    def _emitir_unicos(self, sym: str, kind: str, sig) -> bool:
        """Dedup v15.34: True se o trade deve ser emitido.

        Regras:
          - DENTRO do mesmo ciclo RefreshData: NUNCA suprime — N linhas
            identicas no mesmo ms (rajada) emitem N eventos (regra
            EVENTO != FEATURE: 100 negocios legitimos = 100 eventos);
          - Entre ciclos: a MESMA identidade reentregue (reemissao
            persistente da janela) e suprimida e contada em _tt_duplicados.

        A chave e o trio ESTAVEL (ts, preco, qtd) — ver _sig_tt. Controle de
        memoria: expira por idade (expiry_s) + cap FIFO por (sym, kind),
        aplicados no merge do fim do ciclo (_fechar_ciclo_tt). Desligavel via
        config rtd.dedup_tt=false.
        """
        if not self._dedup_tt_on:
            return True
        chave = (sym, kind)
        vistos = self._vistos_tt[chave]
        agora = time.time()
        # prune expirados (mais antigos primeiro)
        while vistos:
            k0, t0 = next(iter(vistos.items()))
            if agora - t0 > self._dedup_tt_expiry_s:
                vistos.popitem(last=False)
            else:
                break
        if sig in vistos:
            self._tt_duplicados[chave] += 1
            return False
        # 1a ocorrencia no ciclo: emite (rajada preservada) e marca p/ merge
        self._tt_unicos[chave] += 1
        self._vistos_ciclo[chave].add(sig)
        return True

    def _fechar_ciclo_tt(self):
        """Merge das emissoes do ciclo na estrutura persistente (v15.34).

        Chamado ao fim de cada RefreshData: as identidades emitidas NESTE
        ciclo passam a constar como 'vistas' — a reentrega nos proximos ciclos
        sera suprimida. Rajadas do MESMO ciclo nunca sao afetadas (o merge so
        ocorre no fim). Aplica expiracao + cap FIFO apos o merge.
        """
        if not self._dedup_tt_on:
            self._vistos_ciclo = defaultdict(set)
            return
        agora = time.time()
        for chave, sigs in list(self._vistos_ciclo.items()):
            vistos = self._vistos_tt[chave]
            # prune expirados antes do cap (nao ocupar espaco com lixo)
            while vistos:
                k0, t0 = next(iter(vistos.items()))
                if agora - t0 > self._dedup_tt_expiry_s:
                    vistos.popitem(last=False)
                else:
                    break
            for s in sorted(sigs):  # ordem deterministica p/ FIFO previsivel
                vistos[s] = agora
            while len(vistos) > self._dedup_tt_max:
                vistos.popitem(last=False)
        self._vistos_ciclo = defaultdict(set)

    def _deve_emitir_tt(self, sym: str, event_ts_ms: int) -> bool:
        """Baseline R1 (v15.4): decide se uma linha T&T/RLP coerente é emitida.

        Absorve SEM emitir apenas o retrato PRÉ-conexão da janela — linhas com
        DAT anterior ao início da captura (proteção anti-duplicata quando o
        motor (re)conecta com a janela já populada).

        Trade com DAT >= início da captura é dado REAL e NUNCA é descartado:
        encerra o baseline deste símbolo e é emitido — RAW é a fonte de
        verdade (fix: o 1º trade do dia de cada ativo era engolido quando o
        motor iniciava antes do pregão e a janela estava vazia na conexão).
        """
        if not self._baseline_pending[sym]:
            return True
        if event_ts_ms > 0 and event_ts_ms < self._connect_ts_ms:
            return False  # retrato pré-conexão — continua em baseline
        # Dado novo (DAT >= início da captura) ou DAT indeterminado: encerra
        # baseline e emite.
        self._baseline_pending[sym] = False
        return True

    def _emitir_linha_coerente(self, kind, sym, j_idx, linha):
        """Decide e constrói o evento de uma linha T&T/RLP coerente (v15.33).

        Chamado no FIM do ciclo RefreshData, com as células FINAIS (campos de
        identidade AGR/ACP/AVD já atualizados neste ciclo). Retorna MarketEvent
        ou None (suprimido / aguardando / rejeitado).

        Gates, em ordem:
          1. AGR/ACP/AVD no mesmo lote do trio (ou nunca entregues — janela
             sem o campo) → identidade COMPLETA e estável para o dedup;
          2. conteúdo válido (pre > 0, qtd > 0);
          3. baseline: retrato pré-conexão absorvido SEM emitir (mas marcado
             como visto p/ não virar evento depois);
          4. timestamp válido (DAT inválido → fallback receive_ts);
          5. dedup de reemissão persistente (identidade completa).
        """
        stream_key = (sym, kind, j_idx)
        cell = self._book_cells[stream_key][linha]
        lotes_linha = self._cell_lote[(stream_key, linha)]
        lote_ref = lotes_linha.get('DAT', 0)
        # v15.33: identidade exige TODOS os 6 campos no mesmo ciclo (medido no
        # RAW 2026-09-03: AGR/ACP/AVD entregues em ~100% das linhas dos 4
        # ativos). Linha com trio coerente mas AGR/ACP/AVD de outro ciclo
        # aguarda 1 ciclo e emite 1x com identidade completa (sem re-emissao
        # dupla com campos vazios).
        for F in ('AGR', 'ACP', 'AVD'):
            if lotes_linha.get(F, 0) != lote_ref:
                return None
        pre = fnum(cell.get('PRE'))
        if pre <= 0:
            return None
        qtd = fint(cell.get('QUL'))
        if qtd <= 0:
            return None
        dat_str = sstr(cell.get('DAT'))
        event_ts_ms = dat_to_epoch_ms(dat_str)
        receive_ns = now_ns()
        agressor = "Comprador" if "compr" in sstr(cell.get('AGR')).lower() else "Vendedor"
        buyer = sstr(cell.get('ACP'))
        seller = sstr(cell.get('AVD'))

        if not self._deve_emitir_tt(sym, event_ts_ms):
            # Baseline absorve o retrato pré-conexão SEM emitir — mas marca a
            # identidade como vista p/ a reentrega não virar evento depois.
            if self._dedup_tt_on:
                self._vistos_tt[(sym, kind)][
                    self._sig_tt(event_ts_ms, pre, qtd)] = time.time()
            return None

        self._tt_recebidos[sym] += 1

        # Se DAT inválido, usar receive_ts como fallback (documentado)
        if event_ts_ms <= 0:
            event_ts_ms = receive_ns // 1_000_000
            self._lograte.aviso(('dat_invalido', sym),
                                f"[RTD] {sym}: DAT invalido, usando receive_ts como fallback",
                                f"(DAT='{dat_str}')")

        valido, motivo = validate_event_ts(event_ts_ms, receive_ns)
        if not valido:
            # v15.38: chave ESTÁVEL — o motivo traz os segundos do lag
            # ('timestamp_passado (343s behind)'), que muda a cada ciclo e
            # faria cada linha do drain logar separado. Agrupa por tipo:
            # 'timestamp_passado' / 'ts_futuro' → 1 resumo por minuto.
            motivo_tipo = motivo.split(' (')[0]
            self._lograte.aviso(('ts_rejeitado', sym, motivo_tipo),
                                f"[RTD] {sym}: timestamp rejeitado: {motivo}",
                                f"(DAT={dat_str})")
            return None

        # v15.34: dedup de reemissão persistente (chave ESTÁVEL ts+preço+qtd),
        # antes do detector de ordenamento p/ não poluir as métricas temporais.
        # Rajadas do MESMO ciclo passam (EVENTO != FEATURE); reentrega em
        # ciclos posteriores é suprimida no _fechar_ciclo_tt.
        if not self._emitir_unicos(sym, kind,
                                   self._sig_tt(event_ts_ms, pre, qtd)):
            return None

        # Fase 3: Detectar anomalias temporais
        ord_result = self._ordering_detector.check(sym, event_ts_ms, receive_ns)
        if ord_result.action == "REJECT":
            return None
        if ord_result.is_late:
            log.debug(f"[RTD] {sym}: evento atrasado lag={ord_result.lag_ms}ms")
        evt_hora = _hora_evt(event_ts_ms)
        if ord_result.is_out_of_order:
            self._lograte.aviso(('fora_ordem', sym),
                                f"[RTD] {sym}: fora de ordem",
                                f"evento {evt_hora} gap={ord_result.gap_ms}ms ({ord_result.reason})")
        if ord_result.is_forward_jump:
            self._lograte.aviso(('salto_temporal', sym),
                                f"[RTD] {sym}: salto temporal",
                                f"evento {evt_hora} gap={ord_result.gap_ms}ms")
        if ord_result.is_backward_sequence:
            self._lograte.aviso(('sequencia_regressiva', sym),
                                f"[RTD] {sym}: sequencia regressiva",
                                f"evento {evt_hora} ({ord_result.reason})")

        seq_id = next_sequence_id()
        trade = TradeEvent(
            symbol=sym, timestamp_ms=event_ts_ms, price=pre,
            quantity=qtd,
            aggressor=agressor,
            buyer=buyer, seller=seller,
            received_at_ns=receive_ns,
            sequence_id=seq_id,
        )
        return MarketEvent(
            type='RLP' if kind == 'rlp' else 'TRADE',
            payload=trade, timestamp_ms=event_ts_ms,
            symbol=sym, janela_id=j_idx,
            window_name=f'T&T{j_idx}', is_rlp=(kind == 'rlp'),
        )

    def events(self) -> Iterator[MarketEvent]:
        """Loop de polling transformado em iterador de contratos."""
        if self._connect_ts_ms == 0:
            self._connect_ts_ms = int(time.time() * 1000)
            log.info(f"[RTD] Captura iniciada em ts={self._connect_ts_ms} — baseline "
                     f"absorve só retrato pré-conexão; 1º dado novo de cada ativo é emitido")
        while not self._shutdown:
            self.com_client.PumpEvents(0.005)
            data = _refresh(self._srv)
            if not data:
                time.sleep(0.01)
                continue

            pairs = parse_refresh_data(data)
            self._lote_atual += 1  # identifica este ciclo RefreshData
            self._coerentes_do_ciclo = []
            self._coerentes_ciclo_set = set()
            for tid, val in pairs:
                info = self._topic_map.get(tid)
                if not info: continue
                kind, sym, field, linha, j_idx = info

                if kind == "tt":
                    # v14.5+: coerência de lote + DAT-primário.
                    # v15.33: dedup de REEMISSÃO persistente (o RTD reentrega as
                    # linhas visíveis da janela a cada RefreshData) com
                    # identidade COMPLETA — ts+preco+qtd+agressor+compradora+
                    # vendedora. Trades genuinamente distintos (qualquer campo
                    # diferente) NUNCA colidem; apenas a mesma linha reentregue
                    # é suprimida (1 trade = 1 evento).
                    # O gatilho de coerência dispara em QUALQUER um dos 3 campos
                    # chave (DAT/PRE/QUL), não só no DAT. Isso resolve o race
                    # condition onde DAT chega antes de PRE/QUL no mesmo ciclo.
                    # v14.8: Estado das células separado POR JANELA — TT, RLP e
                    # BOOK do mesmo ativo não podem compartilhar o mesmo dict.
                    # v15.33: aqui só ACUMULA a linha coerente; a decisão de
                    # emissão é adiada para o fim do ciclo (AGR/ACP/AVD chegam
                    # depois de DAT/PRE/QUL no mesmo refresh).
                    stream_key = (sym, 'tt', j_idx)
                    cell = self._book_cells[stream_key][linha]
                    cell[field] = val
                    lotes_linha = self._cell_lote[(stream_key, linha)]
                    lotes_linha[field] = self._lote_atual

                    # Só processa quando algum dos 3 campos-chave chega
                    if field not in ('DAT', 'PRE', 'QUL'):
                        continue

                    # Coerência (trio): DAT/PRE/QUL no mesmo lote
                    lote_ref = lotes_linha.get('DAT', 0)
                    if (lote_ref == 0
                            or lotes_linha.get('PRE', 0) != lote_ref
                            or lotes_linha.get('QUL', 0) != lote_ref):
                        continue  # Aguardar convergência — re-avalia no próximo campo

                    chave = ('tt', sym, j_idx, linha)
                    if chave not in self._coerentes_ciclo_set:
                        self._coerentes_ciclo_set.add(chave)
                        self._coerentes_do_ciclo.append(chave)

                elif kind == "rlp":
                    # v14.5: RLP — coerência + gatilho em qualquer campo-chave
                    # v14.8: Estado separado por janela (ver nota no bloco tt)
                    # v15.33: RLP — mesma política do TT (acumula e decide no
                    # fim do ciclo; dedup próprio por (sym, 'rlp')).
                    stream_key = (sym, 'rlp', j_idx)
                    cell = self._book_cells[stream_key][linha]
                    cell[field] = val
                    lotes_linha = self._cell_lote[(stream_key, linha)]
                    lotes_linha[field] = self._lote_atual

                    if field not in ('DAT', 'PRE', 'QUL'):
                        continue

                    # Coerência (trio): DAT/PRE/QUL no mesmo lote
                    lote_ref = lotes_linha.get('DAT', 0)
                    if (lote_ref == 0
                            or lotes_linha.get('PRE', 0) != lote_ref
                            or lotes_linha.get('QUL', 0) != lote_ref):
                        continue

                    chave = ('rlp', sym, j_idx, linha)
                    if chave not in self._coerentes_ciclo_set:
                        self._coerentes_ciclo_set.add(chave)
                        self._coerentes_do_ciclo.append(chave)

                elif kind == "book":
                    # v14.8: BOOK com estado próprio — não compartilha células
                    # com TT/RLP (ACP/AVD do BOOK colidiam com o do TT).
                    stream_key = (sym, 'book', j_idx)
                    self._book_cells[stream_key][linha][field] = val
                    self._book_recv_count[sym] = self._book_recv_count.get(sym, 0) + 1
                    
                    # Throttle: emite snapshot do book a cada 100ms por ativo (alinhado com janela ML)
                    agora = time.time()
                    if agora - self._last_book_yield[sym] > 0.10:
                        self._last_book_yield[sym] = agora
                        receive_ns = now_ns()
                        bids, asks = [], []
                        for l_idx in range(int((self.config.get('rtd') or {}).get('book_linhas', 500))):
                            c = self._book_cells[stream_key][l_idx]
                            if c.get('OCP'): bids.append(BookLevel(price=fnum(c['OCP']), volume=fint(c.get('VOC')), broker=sstr(c.get('ACP'))))
                            if c.get('OVD'): asks.append(BookLevel(price=fnum(c['OVD']), volume=fint(c.get('VOV')), broker=sstr(c.get('AVD'))))
                        
                        # P1-A09 (v15.7): contrato temporal do BOOK. Os tópicos BOOK
                        # do RTD NÃO carregam DAT (timestamp de exchange) — o único
                        # tempo disponível é o da observação no poll. Formalização:
                        #   event_ts_ms(BOOK) = receive_ts_ns // 1_000_000
                        # ou seja, event_ts e receive_ts vêm da MESMA leitura de
                        # relógio (now_ns) — nunca de relógios diferentes. O
                        # `agora`/time.time() acima serve SÓ ao throttle.
                        book_ts = receive_ns // 1_000_000
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
            
            # v15.34: processa as linhas coerentes deste ciclo com as células
            # FINAIS (campos de identidade completos). Emissão adiada p/ fim do
            # ciclo preserva rajadas (nada é suprimido dentro do MESMO refresh)
            # e a reentrega nos ciclos seguintes é suprimida pelo dedup.
            for chave in self._coerentes_do_ciclo:
                kind, sym, j_idx, linha = chave
                ev = self._emitir_linha_coerente(kind, sym, j_idx, linha)
                if ev is not None:
                    yield ev
            # v15.34: marca as identidades deste ciclo como vistas — a mesma
            # linha reentregue no próximo RefreshData é reemissão, não trade.
            self._fechar_ciclo_tt()

            # v15.4: sem reset global por ciclo — o baseline de cada símbolo
            # encerra individualmente quando chega o 1º dado com DAT >= início
            # da captura (_deve_emitir_tt). Reset por ciclo engolia o 1º trade
            # real de ativos que estavam vazios na conexão (ex: motor 08:45,
            # mercado abre 09:02 — o 1º trade do dia era perdido).
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
        """Retorna estatísticas de RECEBIMENTO por ativo (nome legado mantido
        para compatibilidade com o dashboard — key JSON `dedup_stats`).

        v15.33: `tt_recebidos` = linhas COERENTES recebidas (inclui
        reemissões); `tt_unicos` = trades emitidos (após dedup de reemissão);
        `tt_duplicados` = reemissões suprimidas. Inclui TODOS os ativos
        conectados (tt_map + rlp_map + book_map), não só os que já tiveram
        trades.
        """
        # União de todos os ativos conhecidos
        todos_ativos = set(self._tt_map.values()) | set(self._rlp_map.values()) | set(self._book_map.values())
        resultado = {}
        for sym in sorted(todos_ativos):
            resultado[sym] = {
                'tt_recebidos': self._tt_recebidos.get(sym, 0),
                'tt_unicos': (self._tt_unicos.get((sym, 'tt'), 0)
                              + self._tt_unicos.get((sym, 'rlp'), 0)),
                'tt_duplicados': (self._tt_duplicados.get((sym, 'tt'), 0)
                                  + self._tt_duplicados.get((sym, 'rlp'), 0)),
                'baseline_pendente': self._baseline_pending.get(sym, False),
            }
        return resultado

    def _reset_dedup(self):
        """Reseta baseline + estrutura de dedup (para testes)."""
        self._baseline_pending = defaultdict(lambda: True)
        self._vistos_tt = defaultdict(OrderedDict)
        self._vistos_ciclo = defaultdict(set)
        self._tt_unicos = defaultdict(int)
        self._tt_duplicados = defaultdict(int)

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
