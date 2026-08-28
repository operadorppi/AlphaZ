# -*- coding: utf-8 -*-
import json
import threading
import time
import logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from core.market_state import MarketState
from features.feature_engine import FeatureEngine

log = logging.getLogger("WebAdapterV10")

class DashboardHandler(BaseHTTPRequestHandler):
    """Handler para expor as métricas do MarketState via API."""
    market_state = None

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == '/':
            self._send_response({"status": "ok", "message": "Motor RT Alphaz v10 API"}, 200)
        elif p.path == '/api/features':
            # Consome o estado thread-safe do core
            data = self.market_state.get_features()
            self._send_response(data, 200)
        elif p.path == '/api/memoria':
            data = self.market_state.get_memoria()
            self._send_response(data, 200)
        else:
            self._send_response({"error": "Not Found"}, 404)

    def _send_response(self, data, status):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode('utf-8'))

    def log_message(self, format, *args):
        return # Silencia logs de requisição no terminal

def iniciar_servidor(state, port=5001):
    DashboardHandler.market_state = state
    server = ThreadingHTTPServer(('0.0.0.0', port), DashboardHandler)
    log.info(f"Dashboard API iniciada na porta {port}")
    server.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 1. Configuração e Inicialização do Estado
    config = {
        "ativo_principal": "WINV26",
        "ativo_contexto": "WDOU26",
        "hist_segs_max": 3600
    }
    
    state = MarketState(config=config)
    engine = FeatureEngine(state)
    
    # 2. Iniciar Dashboard em Thread separada
    web_thread = threading.Thread(target=iniciar_servidor, args=(state,), daemon=True)
    web_thread.start()
    
    log.info("Motor v10 iniciado. Aguardando dados do RTD...")
    
    # 3. Loop de Simulação/Processamento
    # Aqui entraria a conexão com o RTD. Para teste, simulamos o processamento.
    try:
        while True:
            # Exemplo de fluxo:
            # 1. Recebe dados brutos do RTD (Adapter)
            # 2. state.alimentar_negocio(evento)
            # 3. engine.processar_lote(ativo, negs, seg)
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Encerrando motor...")