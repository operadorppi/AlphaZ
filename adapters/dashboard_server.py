# -*- coding: utf-8 -*-
"""
adapters/dashboard_server.py — Adaptador de infraestrutura para o servidor do Dashboard.
"""

import logging
import time
import threading
from http.server import ThreadingHTTPServer
from adapters.dashboard.api import DashboardAPI

log = logging.getLogger(__name__)

class DashboardServer:
    """Gerencia o ciclo de vida do servidor HTTP para telemetria."""

    def __init__(self, app, host='127.0.0.1', port=5001):
        self.app = app
        self.host = host
        self.port = port
        self.server = None
        self._thread = None
        self._snap_thread = None
        self._snap_stop = threading.Event()

    def _snapshot_loop(self):
        """Atualiza o snapshot do dashboard 1x/s em thread separada.
        Evita que o HTTP handler adquira locks do motor durante requests."""
        while not self._snap_stop.is_set():
            try:
                app = self.app
                DashboardAPI._snapshot = {
                    'features': app.get_features(),
                    'sinais': app.get_sinais(),
                    'posicao': app.get_posicao() or {},
                    'learning': app.get_estatisticas(),
                    'memoria': app.get_memoria(),
                    'metricas': app.calcular_metricas(),
                    'saldo_corretoras': app.get_saldo_corretoras(),
                    'padroes': app.market_state.padroes.get_resumo(),
                    'ml_health': app.scorer.estado_salud() if app.scorer else {},
                    'book_level': app.get_book_level(),
                    'rtd_health': app.get_rtd_health() if hasattr(app, 'get_rtd_health') else {},
                    'capture_health': app.get_capture_health() if hasattr(app, 'get_capture_health') else {},
                }
                DashboardAPI._snapshot_ts = time.time()
            except Exception:
                pass
            self._snap_stop.wait(1.0)

    def start(self):
        """Inicia o servidor HTTP + refresh do snapshot em threads separadas."""
        DashboardAPI.app = self.app
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), DashboardAPI)
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()
            self._snap_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
            self._snap_thread.start()
            log.info(f"[INFRA] Dashboard Server ativo em http://{self.host}:{self.port}/")
        except Exception as e:
            log.error(f"[INFRA] Falha ao iniciar Dashboard Server: {e}")

    def stop(self):
        """Encerra o servidor e o refresh do snapshot de forma controlada."""
        self._snap_stop.set()
        if self.server:
            log.info("[INFRA] Encerrando Dashboard Server...")
            self.server.shutdown()
            self.server.server_close()