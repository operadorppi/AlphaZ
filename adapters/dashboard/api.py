# -*- coding: utf-8 -*-
"""
adapters/dashboard/api.py — Roteamento HTTP para o dashboard.

Substitui o antigo adapters/dashboard_api.py.
Roteamento limpo: delega cada rota para um handler em handlers.py.
Cache de snapshot 1x/s para /api/all.

Endpoints:
  /                     → dashboard_pro.html
  /api/status           → DashboardState.payload()
  /api/features         → signal_engine.get_features()
  /api/sinais           → signal_engine.get_sinais()
  /api/posicao          → position_manager.get_posicao()
  /api/learning         → learning.get_estatisticas()
  /api/memoria          → market_state.get_memoria()
  /api/book             → market_state.get_book_stats()
  /api/book_level       → market_state.get_book_level()
  /api/metricas         → metrics.calcular()
  /api/resumo           → market_state.get_resumo(ativo)
  /api/padroes          → padroes.get_resumo()
  /api/rtd_health       → profit_rtd.get_health()
  /api/saldo_corretoras → market_state.get_saldo_corretoras()
  /api/contexto         → market_state.get_contexto_mercado()
  /api/ml_health        → scorer.estado_salud()
  /api/historico        → market_state.get_historico()
  /api/decisoes         → journal.listar()
  /api/decisoes/{id}    → journal.buscar(id)
  /api/all              → snapshot agregado (cached 1x/s)
  /health               → status + uptime
"""

import json
import time
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from adapters.dashboard.handlers import DashboardHandlers

log = logging.getLogger(__name__)


# ============================================================================
# ROTEAMENTO
# ============================================================================

# Tabela de rotas: (path, method_name, needs_params)
_ROUTES = [
    ('/',                     'handle_root',                False),
    ('/api/status',           'handle_api_status',         True),
    ('/api/features',         'handle_api_features',       True),
    ('/api/sinais',           'handle_api_sinais',         True),
    ('/api/posicao',          'handle_api_posicao',        True),
    ('/api/learning',         'handle_api_learning',       True),
    ('/api/memoria',          'handle_api_memoria',        True),
    ('/api/book',             'handle_api_book',           True),
    ('/api/book_level',       'handle_api_book_level',     True),
    ('/api/metricas',         'handle_api_metricas',       True),
    ('/api/resumo',           'handle_api_resumo',         True),
    ('/api/padroes',          'handle_api_padroes',        True),
    ('/api/rtd_health',       'handle_api_rtd_health',     True),
    ('/api/capture_health',   'handle_api_capture_health', True),
    ('/api/saldo_corretoras', 'handle_api_saldo_corretoras', True),
    ('/api/contexto',         'handle_api_contexto',       True),
    ('/api/ml_health',        'handle_api_ml_health',      True),
    ('/api/historico',        'handle_api_historico',      True),
    ('/api/decisoes',         'handle_api_decisoes',       True),
    ('/api/all',              'handle_api_all',            True),
    ('/health',               'handle_health',             True),
    ('/legacy',               'handle_legacy',             True),
]


class DashboardAPI(BaseHTTPRequestHandler):
    """Handler HTTP para o dashboard. Roteamento limpo via tabela de rotas."""

    app = None       # Referência para App (setado externamente)
    state = None     # Referência para DashboardState
    _snapshot = None
    _snapshot_ts = 0.0
    _dash_html = None
    _dash_mtime = None

    def do_GET(self):
        p = urlparse(self.path)
        params = parse_qs(p.query)

        # Rota exata
        for path, method_name, needs_params in _ROUTES:
            if p.path == path:
                handler_fn = getattr(DashboardHandlers, method_name)
                result = handler_fn(self, params) if needs_params else handler_fn(self)

                if isinstance(result, tuple):
                    body, content_type = result
                elif isinstance(result, str):
                    body = result
                    content_type = 'text/html' if result.startswith('<') else 'text/plain'
                else:
                    body = result
                    content_type = None

                if content_type == 'text/html':
                    self._html(body)
                elif content_type:
                    self._html(body)
                else:
                    # JSON response
                    etag = f'"{getattr(self.app, "revision", 0)}"' if path == '/api/all' else None
                    if path == '/api/all' and self.headers.get('If-None-Match') == etag:
                        self.send_response(304)
                        self.end_headers()
                        return
                    self._json(body, etag=etag)
                return

        # Rota dinâmica: /api/decisoes/{id}
        if p.path.startswith('/api/decisoes/'):
            did = p.path.split('/')[-1]
            result = DashboardHandlers.handle_api_decisoes_id(self, did)
            self._json(result)
            return

        self.send_error(404)

    def _html(self, body):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        if isinstance(body, str):
            body = body.encode()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def _json(self, obj, etag=None):
        try:
            payload = json.dumps(obj, default=str).encode()
        except Exception:
            payload = b'{}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        if etag:
            self.send_header('ETag', etag)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def log_message(self, *a):
        pass  # Suprime logs de acesso HTTP

    def handle_error(self, request, client_address):
        """Suprime tracebacks de ConnectionAbortedError."""
        import sys
        exc = sys.exc_info()[0]
        if exc in (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            return
        super().handle_error(request, client_address)
