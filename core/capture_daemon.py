# -*- coding: utf-8 -*-
"""
core/capture_daemon.py — Daemon de captura bruta (thread imortal).

Responsabilidade ÚNICA: receber eventos de mercado e gravar JSONL em disco.
Executa como daemon thread separada do loop de trading.

Por que existe:
  O loop de trading (core/app.py) pode crashar a qualquer momento
  (COM error, OOM, division by zero, risk engine exception). Se a
  gravação de dados brutos estiver no mesmo thread, o crash mata a
  captura e o dia é perdido.

  Este daemon roda em thread separada com try/except em cada evento.
  Se o trading crashar, o daemon continua gravando. Se o daemon
  crashar (disco cheio, permission denied), o trading continua.

Fluxo:
  App._loop() → capture_daemon.registrar_negocios() / registrar_book()
                → thread interna → FileStorage (JSONL) → disco

Garantias:
  - Thread daemon: morre automaticamente quando o processo pai morre
  - try/except por evento: 1 evento com erro não mata o daemon
  - flush periódico: dados não ficam presos em memória
  - stats: contadores de rejeição/erro para monitoramento
  - health_check: dashboard pode verificar se a captura está saudável
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
    
    Uso:
        daemon = CaptureDaemon(save_dir, session_ts)
        daemon.start()
        
        # No loop de trading (thread principal):
        daemon.registrar_negocios([...])
        daemon.registrar_book(...)
        
        # No shutdown:
        daemon.stop()
    
    O daemon NÃO decide trade, NÃO calcula features. Só grava dados brutos.
    """

    def __init__(self, save_dir, session_ts=None, flush_interval_s=_FLUSH_INTERVAL_S):
        self.save_dir = save_dir
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.flush_interval_s = flush_interval_s
        
        # FileStorage (JSONL writer) — criado e mantido pelo daemon
        self._storage = FileStorage(save_dir, self.session_ts)
        
        # Fila de eventos (thread-safe)
        self._queue = queue.Queue(maxsize=_MAX_QUEUE)
        
        # Controle
        self._shutdown = threading.Event()
        self._thread = None
        self._started = False
        
        # Stats
        self._stats = {
            'negocios_recebidos': 0,
            'negocios_enfileirados': 0,
            'negocios_erro': 0,
            'book_recebidos': 0,
            'book_enfileirados': 0,
            'book_erro': 0,
            'flushes': 0,
            'erros_flush': 0,
            'fila_max_atingido': 0,
            'daemon_crashes': 0,
        }
        self._stats_lock = threading.Lock()
        self._ultimo_flush = time.time()
        self._ultimo_health_log = time.time()
        
        log.info(f"[CAPTURE-DAEMON] Criado: {save_dir} (session={self.session_ts})")

    def start(self):
        """Inicia o daemon thread."""
        if self._started:
            log.warning("[CAPTURE-DAEMON] Já iniciado")
            return
        
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name='CaptureDaemon',
            daemon=True,  # Morre automaticamente com o processo pai
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
        
        with self._stats_lock:
            self._stats['negocios_recebidos'] += len(novos)
        
        try:
            self._queue.put_nowait(('neg', novos))
        except queue.Full:
            with self._stats_lock:
                self._stats['fila_max_atingido'] += 1
            log.warning(f"[CAPTURE-DAEMON] Fila cheia! {len(novos)} negócios descartados")

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
            self._stats['book_recebidos'] += 1
        
        try:
            self._queue.put_nowait(('book', (ativo, ts_ms, snap, bid_vol, ask_vol, levels)))
        except queue.Full:
            with self._stats_lock:
                self._stats['fila_max_atingido'] += 1
            log.warning(f"[CAPTURE-DAEMON] Fila cheia! Book snapshot descartado")

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
        """Retorna contadores de captura para monitoramento."""
        with self._stats_lock:
            s = dict(self._stats)
        # Adicionar stats do FileStorage (rejeições, etc.)
        try:
            s['storage_rejeitados'] = self._storage.stats()
        except Exception:
            s['storage_rejeitados'] = {}
        return s

    def health_check(self):
        """Verifica se o daemon está saudável."""
        thread_alive = self._thread is not None and self._thread.is_alive()
        queue_size = self._queue.qsize()
        
        return {
            'alive': thread_alive,
            'queue_size': queue_size,
            'queue_pct': round(100 * queue_size / _MAX_QUEUE, 1),
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
                # Esperar evento ou timeout para flush
                try:
                    tipo, dados = self._queue.get(timeout=self.flush_interval_s)
                except queue.Empty:
                    # Timeout → flush periódico
                    self._periodic_flush()
                    continue
                
                # Processar evento (com proteção individual)
                try:
                    if tipo == 'neg':
                        self._storage.registrar_negocios(dados)
                        with self._stats_lock:
                            self._stats['negocios_enfileirados'] += len(dados)
                    elif tipo == 'book':
                        ativo, ts_ms, snap, bid_vol, ask_vol, levels = dados
                        self._storage.registrar_book(ativo, ts_ms, snap, bid_vol, ask_vol, levels=levels)
                        with self._stats_lock:
                            self._stats['book_enfileirados'] += 1
                except Exception as e:
                    with self._stats_lock:
                        if tipo == 'neg':
                            self._stats['negocios_erro'] += 1
                        else:
                            self._stats['book_erro'] += 1
                    log.error(f"[CAPTURE-DAEMON] Erro ao gravar {tipo}: {e}")
                    # NÃO mata o daemon — continua rodando
                
                # Flush periódico
                self._periodic_flush()
                
            except Exception as e:
                # Erro inesperado no loop → incrementar contador e continuar
                with self._stats_lock:
                    self._stats['daemon_crashes'] += 1
                log.error(f"[CAPTURE-DAEMON] Erro no loop: {e}")
                time.sleep(0.1)  # respira antes de continuar
        
        # Drain da fila antes de parar
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
        
        # Log de saúde periódico
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
        
        # Flush final
        try:
            self._storage.flush()
        except Exception:
            pass

    def _log_health(self):
        """Log periódico de saúde da captura."""
        s = self.stats()
        q = self._queue.qsize()
        
        total_neg = s['negocios_recebidos']
        total_book = s['book_recebidos']
        erros_neg = s['negocios_erro']
        erros_book = s['book_erro']
        
        if total_neg > 0 or total_book > 0:
            log.info(
                f"[CAPTURE-DAEMON] Saúde: neg={total_neg} book={total_book} "
                f"erros={erros_neg + erros_book} fila={q} "
                f"flushes={s['flushes']} crashes={s['daemon_crashes']}"
            )
        else:
            log.info(f"[CAPTURE-DAEMON] Saudável: fila={q}, 0 eventos (mercado pode estar parado)")
