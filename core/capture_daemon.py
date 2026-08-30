# -*- coding: utf-8 -*-
"""
core/capture_daemon.py — Daemon de captura bruta (thread imortal).

Responsabilidade ÚNICA: receber eventos de mercado e gravar JSONL em disco.
Executa como daemon thread separada do loop de trading.

Fase 4 — Overflow e perda de eventos:
  - Nenhuma perda deve ocorrer silenciosamente.
  - Contadores detalhados: recebidos, processados, descartados, motivo.
  - Watermark (pico da fila) e backlog (latência de gravação).
  - Se a política for drop-on-overflow, é EXPLÍCITA e gera estado de falha.
  - health_check reporta `data_loss_detected` para o watchdog.

Fluxo:
  App._loop() → capture_daemon.registrar_negocios() / registrar_book()
                → thread interna → FileStorage (JSONL) → disco

Garantias:
  - Thread daemon: morre automaticamente quando o processo pai morre
  - try/except por evento: 1 evento com erro não mata o daemon
  - flush periódico: dados não ficam presos em memória
  - stats: contadores detalhados de recebidos/processados/descartados
  - health_check: dashboard/watchdog pode verificar se a captura está saudável
  - drop-on-overflow é EXPLÍCITO: gera log.error + estado data_loss_detected
"""

import os
import time
import queue
import logging
import threading
from datetime import datetime
from collections import defaultdict
from adapters.file_storage import FileStorage, CapturaEventosMS

log = logging.getLogger(__name__)

# Tamanho máximo da fila interna (eventos que aguardam gravação)
_MAX_QUEUE = 100_000
# Intervalo de flush para disco (segundos)
_FLUSH_INTERVAL_S = 2.0
# Intervalo de log de saúde (segundos)
_HEALTH_LOG_INTERVAL_S = 300  # 5 min


class CaptureDaemon:
    """Daemon de captura bruta — thread imortal que grava JSONL em disco.

    Política de overflow (EXPLÍCITA):
      Quando a fila está cheia (_MAX_QUEUE), novos eventos são DESCARTADOS
      (drop-on-overflow). Isso é registrado com:
        - log.error (não log.warning)
        - contador events_dropped incrementado
        - flag data_loss_detected = True no health_check
      Alternativa: se backpressure=True, a thread produtora é bloqueada
      (put com timeout) em vez de descartar. Pode causar latência no trading.

    Uso:
        daemon = CaptureDaemon(save_dir, session_ts)
        daemon.start()

        # No loop de trading (thread principal):
        daemon.registrar_negocios([...])
        daemon.registrar_book(...)

        # No shutdown:
        daemon.stop()
    """

    def __init__(self, save_dir, session_ts=None, flush_interval_s=_FLUSH_INTERVAL_S,
                 backpressure=False):
        self.save_dir = save_dir
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.flush_interval_s = flush_interval_s
        self._backpressure = backpressure  # True = bloquear em vez de descartar

        # FileStorage (JSONL writer) — criado e mantido pelo daemon
        self._storage = FileStorage(save_dir, self.session_ts)

        # Fila de eventos (thread-safe)
        self._queue = queue.Queue(maxsize=_MAX_QUEUE)

        # Controle
        self._shutdown = threading.Event()
        self._thread = None
        self._started = False

        # Stats detalhados (Fase 4)
        self._stats = {
            # Contadores de eventos
            'events_received': 0,        # total de eventos recebidos (neg + book)
            'events_processed': 0,       # total de eventos gravados com sucesso
            'events_dropped': 0,         # total de eventos DESCARTADOS por overflow
            'events_error': 0,           # total de eventos com erro de gravação
            # Por tipo
            'negocios_recebidos': 0,
            'negocios_processados': 0,
            'negocios_dropped': 0,
            'negocios_erro': 0,
            'book_recebidos': 0,
            'book_processados': 0,
            'book_dropped': 0,
            'book_erro': 0,
            # Overflow e watermark
            'overflow_count': 0,         # número de vezes que a fila encheu
            'watermark_max': 0,          # maior tamanho da fila observado
            'watermark_current': 0,      # tamanho atual da fila
            # Backlog
            'backlog_ms_max': 0,         # maior latência fila → disco
            # Flush
            'flushes': 0,
            'erros_flush': 0,
            # Daemon
            'daemon_crashes': 0,
            # Estado de falha
            'data_loss_detected': False,  # True se qualquer evento foi descartado
        }
        self._stats_lock = threading.Lock()

        # Motivo do último descarte (para auditoria)
        self._last_drop_reason = ''
        self._last_drop_ts = 0

        self._ultimo_flush = time.time()
        self._ultimo_health_log = time.time()

        log.info(f"[CAPTURE-DAEMON] Criado: {save_dir} (session={self.session_ts}, "
                 f"backpressure={backpressure})")

    def start(self):
        """Inicia o daemon thread."""
        if self._started:
            log.warning("[CAPTURE-DAEMON] Já iniciado")
            return

        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name='CaptureDaemon',
            daemon=True,
        )
        self._thread.start()
        self._started = True
        log.info("[CAPTURE-DAEMON] Thread iniciada")

    def stop(self):
        """Para o daemon e faz flush final."""
        if not self._started:
            return

        log.info("[CAPTURE-DAEMON] Parando...")
        self._shutdown.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)

        # Flush final
        try:
            self._storage.flush()
            self._storage.fechar()
        except Exception as e:
            log.error(f"[CAPTURE-DAEMON] Erro no flush final: {e}")

        self._started = False
        log.info("[CAPTURE-DAEMON] Parado")

    # ---- API pública (chamada pelo loop de trading) ----

    def registrar_negocios(self, novos):
        """Registra negócios recebidos do RTD.

        Args:
            novos: lista de tuplas (sym, tms, preco, qtd, agr, comp, vend)
        """
        if not novos:
            return

        n = len(novos)
        with self._stats_lock:
            self._stats['events_received'] += n
            self._stats['negocios_recebidos'] += n

        # Tentar enfileirar (lote inteiro como 1 item na fila)
        dropped = self._enqueue(('neg', novos), n_events=len(novos))
        if dropped > 0:
            with self._stats_lock:
                self._stats['events_dropped'] += dropped
                self._stats['negocios_dropped'] += dropped
                self._stats['overflow_count'] += 1
                self._stats['data_loss_detected'] = True
            self._last_drop_reason = f'queue_full (neg, {dropped} eventos)'
            self._last_drop_ts = time.time()
            log.error(f"[CAPTURE-DAEMON] OVERFLOW: {dropped} negócios descartados "
                      f"(fila cheia {_MAX_QUEUE})")

    def registrar_book(self, ativo, ts_ms, snap, bid_vol, ask_vol, levels=None):
        """Registra snapshot de book recebido do RTD.

        Args:
            ativo: símbolo do ativo
            ts_ms: timestamp em milissegundos
            snap: dict {corretora: {...}}
            bid_vol: volume total bid
            ask_vol: volume total ask
            levels: dict com listas por nível (opcional)
        """
        with self._stats_lock:
            self._stats['events_received'] += 1
            self._stats['book_recebidos'] += 1

        item = ('book', (ativo, ts_ms, snap, bid_vol, ask_vol, levels))
        dropped = self._enqueue(item, n_events=1)
        if dropped > 0:
            with self._stats_lock:
                self._stats['events_dropped'] += dropped
                self._stats['book_dropped'] += dropped
                self._stats['overflow_count'] += 1
                self._stats['data_loss_detected'] = True
            self._last_drop_reason = f'queue_full (book, {dropped} eventos)'
            self._last_drop_ts = time.time()
            log.error(f"[CAPTURE-DAEMON] OVERFLOW: {dropped} book snapshots descartados "
                      f"(fila cheia {_MAX_QUEUE})")

    def _enqueue(self, item, n_events=1):
        """Tenta enfileirar UM item na fila. Retorna quantos eventos foram descartados.

        Args:
            item: tupla (tipo, dados) para colocar na fila
            n_events: número de eventos contidos no item (para contagem de descarte)

        Se backpressure=True, bloqueia a thread (put com timeout) em vez
        de descartar. Se backpressure=False (default), descarta (drop-on-overflow).
        """
        if self._backpressure:
            try:
                self._queue.put(item, timeout=5.0)
                return 0
            except queue.Full:
                return n_events
        else:
            try:
                self._queue.put_nowait(item)
                return 0
            except queue.Full:
                return n_events

    def flush(self):
        """Flush manual para disco."""
        try:
            self._storage.flush()
            with self._stats_lock:
                self._stats['flushes'] += 1
        except Exception as e:
            log.error(f"[CAPTURE-DAEMON] Erro no flush: {e}")
            with self._stats_lock:
                self._stats['erros_flush'] += 1

    def stats(self):
        """Retorna contadores detalhados de captura."""
        with self._stats_lock:
            s = dict(self._stats)
        # Watermark atual
        s['watermark_current'] = self._queue.qsize()
        s['last_drop_reason'] = self._last_drop_reason
        s['last_drop_ts'] = self._last_drop_ts
        # Stats do FileStorage (rejeições, etc.)
        try:
            s['storage_rejeitados'] = self._storage.stats()
        except Exception:
            s['storage_rejeitados'] = {}
        return s

    def health_check(self):
        """Verifica se o daemon está saudável.

        Retorna:
            dict com:
              - alive: thread está viva
              - queue_size: tamanho atual da fila
              - queue_pct: % da fila usada
              - data_loss_detected: True se eventos foram descartados
              - stats: contadores detalhados
        """
        thread_alive = self._thread is not None and self._thread.is_alive()
        queue_size = self._queue.qsize()
        queue_pct = round(100 * queue_size / _MAX_QUEUE, 1)

        with self._stats_lock:
            data_loss = self._stats['data_loss_detected']
            watermark = self._stats['watermark_max']

        # Atualizar watermark se necessário
        if queue_size > watermark:
            with self._stats_lock:
                self._stats['watermark_max'] = queue_size

        return {
            'alive': thread_alive,
            'queue_size': queue_size,
            'queue_pct': queue_pct,
            'watermark_max': watermark,
            'data_loss_detected': data_loss,
            'started': self._started,
            'shutdown': self._shutdown.is_set(),
            'stats': self.stats(),
        }

    # ---- Loop interno do daemon ----

    def _loop(self):
        """Loop principal: consome fila e grava em disco."""
        log.info("[CAPTURE-DAEMON] Loop iniciado")

        while not self._shutdown.is_set():
            try:
                # Watermark: registrar pico da fila
                qs = self._queue.qsize()
                if qs > 0:
                    with self._stats_lock:
                        if qs > self._stats['watermark_max']:
                            self._stats['watermark_max'] = qs
                        self._stats['watermark_current'] = qs
                    if qs > _MAX_QUEUE * 0.8:
                        log.error(f"[CAPTURE-DAEMON] Fila quase cheia: {qs}/{_MAX_QUEUE} "
                                  f"({100*qs/_MAX_QUEUE:.1f}%)")

                # Esperar evento ou timeout para flush
                t_get = time.time()
                try:
                    tipo, dados = self._queue.get(timeout=self.flush_interval_s)
                except queue.Empty:
                    self._periodic_flush()
                    continue

                # Medir backlog (latência fila → processamento)
                backlog_ms = int((time.time() - t_get) * 1000)
                with self._stats_lock:
                    if backlog_ms > self._stats['backlog_ms_max']:
                        self._stats['backlog_ms_max'] = backlog_ms

                # Processar evento (com proteção individual)
                try:
                    if tipo == 'neg':
                        self._storage.registrar_negocios(dados)
                        with self._stats_lock:
                            self._stats['events_processed'] += len(dados)
                            self._stats['negocios_processados'] += len(dados)
                    elif tipo == 'book':
                        ativo, ts_ms, snap, bid_vol, ask_vol, levels = dados
                        self._storage.registrar_book(ativo, ts_ms, snap, bid_vol, ask_vol, levels=levels)
                        with self._stats_lock:
                            self._stats['events_processed'] += 1
                            self._stats['book_processados'] += 1
                except Exception as e:
                    with self._stats_lock:
                        self._stats['events_error'] += 1
                        if tipo == 'neg':
                            self._stats['negocios_erro'] += 1
                        else:
                            self._stats['book_erro'] += 1
                    log.error(f"[CAPTURE-DAEMON] Erro ao gravar {tipo}: {e}")

                self._periodic_flush()

            except Exception as e:
                with self._stats_lock:
                    self._stats['daemon_crashes'] += 1
                log.error(f"[CAPTURE-DAEMON] Erro no loop: {e}")
                time.sleep(0.1)

        self._drain_queue()
        log.info("[CAPTURE-DAEMON] Loop finalizado")

    def _periodic_flush(self):
        """Flush periódico se já passou o intervalo."""
        agora = time.time()
        if agora - self._ultimo_flush >= self.flush_interval_s:
            try:
                self._storage.flush()
                self._ultimo_flush = agora
                with self._stats_lock:
                    self._stats['flushes'] += 1
            except Exception as e:
                log.error(f"[CAPTURE-DAEMON] Erro no flush periódico: {e}")
                with self._stats_lock:
                    self._stats['erros_flush'] += 1

        if agora - self._ultimo_health_log >= _HEALTH_LOG_INTERVAL_S:
            self._log_health()
            self._ultimo_health_log = agora

    def _drain_queue(self):
        """Esvazia a fila restante no shutdown."""
        draining = 0
        while not self._queue.empty():
            try:
                tipo, dados = self._queue.get_nowait()
                if tipo == 'neg':
                    self._storage.registrar_negocios(dados)
                elif tipo == 'book':
                    ativo, ts_ms, snap, bid_vol, ask_vol, levels = dados
                    self._storage.registrar_book(ativo, ts_ms, snap, bid_vol, ask_vol, levels=levels)
                draining += 1
            except queue.Empty:
                break
            except Exception as e:
                log.error(f"[CAPTURE-DAEMON] Erro no drain: {e}")

        if draining:
            log.info(f"[CAPTURE-DAEMON] Drain: {draining} eventos restantes gravados")

        try:
            self._storage.flush()
        except Exception:
            pass

    def _log_health(self):
        """Log periódico de saúde da captura."""
        s = self.stats()
        q = self._queue.qsize()

        total_recv = s['events_received']
        total_proc = s['events_processed']
        total_drop = s['events_dropped']
        total_err = s['events_error']
        watermark = s['watermark_max']
        data_loss = s['data_loss_detected']

        if total_recv > 0:
            pct_proc = 100 * total_proc / total_recv
            pct_drop = 100 * total_drop / total_recv
            loss_str = f" [DATA LOSS: {total_drop} eventos]" if data_loss else ""
            log.info(
                f"[CAPTURE-DAEMON] Saúde: recv={total_recv} proc={total_proc} "
                f"({pct_proc:.1f}%) drop={total_drop} ({pct_drop:.1f}%) "
                f"err={total_err} fila={q} watermark={watermark} "
                f"backlog_max={s['backlog_ms_max']}ms{loss_str}"
            )
        else:
            log.info(f"[CAPTURE-DAEMON] Saudável: fila={q}, 0 eventos (mercado pode estar parado)")
