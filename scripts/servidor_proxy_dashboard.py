# -*- coding: utf-8 -*-
"""
Proxy do Dashboard Profissional (v9.31).
Serve o dashboard novo em http://127.0.0.1:5002/ repassando /api/* para o motor
(que ainda roda o codigo antigo). Zero interferencia no motor.

Uso: python servidor_proxy_dashboard.py
Quando o motor reiniciar com a v9.31, este proxy e opcional (a rota / do motor
ja servira o dashboard novo direto).
"""
import json, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

MOTOR = "http://127.0.0.1:5001"
HOST, PORT = "127.0.0.1", 5002
DASH = Path(__file__).resolve().parent / "dashboard_pro.html"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            try:
                req = urllib.request.Request(MOTOR + self.path)
                with urllib.request.urlopen(req, timeout=4) as r:
                    body = r.read()
                self.send_response(r.status)
                ct = r.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                payload = json.dumps({"erro": str(e)}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
            return
        if self.path in ("/", "/index.html"):
            html = DASH.read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
            return
        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    print(f"dashboard novo em http://{HOST}:{PORT}/  (proxy p/ motor {MOTOR})")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
