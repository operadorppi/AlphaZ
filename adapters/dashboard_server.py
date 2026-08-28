# -*- coding: utf-8 -*-
"""
adapters/dashboard_server.py — Adaptador de infraestrutura para o servidor do Dashboard.
"""

import logging
import threading
from http.server import ThreadingHTTPServer
from adapters.dashboard_api import DashboardAPI

log = logging.getLogger(__name__)

class DashboardServer:
    """Gerencia o ciclo de vida do servidor HTTP para telemetria."""

    def __init__(self, app, host='127.0.0.1', port=5001):
        self.app = app
        self.host = host
        self.port = port
        self.server = None
        self._thread = None

    def start(self):
        """Inicia o servidor HTTP em uma thread separada."""
        DashboardAPI.app = self.app
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), DashboardAPI)
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()
            log.info(f"[INFRA] Dashboard Server ativo em http://{self.host}:{self.port}/")
        except Exception as e:
            log.error(f"[INFRA] Falha ao iniciar Dashboard Server: {e}")

    def stop(self):
        """Encerra o servidor de forma controlada."""
        if self.server:
            log.info("[INFRA] Encerrando Dashboard Server...")
            self.server.shutdown()
            self.server.server_close()