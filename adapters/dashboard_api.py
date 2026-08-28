# -*- coding: utf-8 -*-
"""
adapters/dashboard_api.py — Roteamento HTTP para o dashboard.

Extrai de motor_rt_alphaz.py:
  - Handler (BaseHTTPRequestHandler) — 15+ endpoints

Esta classe faz o roteamento HTTP. O HTML do dashboard é servido
como arquivo estático (dashboard_pro.html), não inline.

Endpoints:
  /                     → dashboard_pro.html (estático, cache por mtime)
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
  /api/historico        → market_state.get_historico()
  /api/all              → agregação de todos
  /health               → status + uptime
"""

import json
import time
import pathlib
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

log = logging.getLogger(__name__)


DASHBOARD_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>B3 Trading — Captura RTD</title>
<style>
:root {
  --bg: #0a0e1a;
  --panel: #111827;
  --panel-hover: #1a2332;
  --border: #1f2937;
  --text: #e5e7eb;
  --text-dim: #9ca3af;
  --green: #10b981;
  --green-dim: #059669;
  --red: #ef4444;
  --red-dim: #dc2626;
  --yellow: #f59e0b;
  --cyan: #06b6d4;
  --font-mono: 'SF Mono', Monaco, 'Cascadia Code', 'Fira Code', Consolas, monospace;
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  line-height: 1.5;
  min-height: 100vh;
}
.wrap { max-width: 1400px; margin: 0 auto; padding: 24px; }
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.brand { display: flex; align-items: center; gap: 14px; }
.logo {
  width: 42px; height: 42px;
  background: linear-gradient(135deg, var(--green-dim), var(--green));
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 700; color: #fff;
  box-shadow: 0 0 12px rgba(16,185,129,0.25);
}
h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
h1 .accent { color: var(--green); font-weight: 400; }
.sub { font-size: 13px; color: var(--text-dim); margin-top: 2px; }
.status-pill {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 16px; border-radius: 999px;
  background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3);
  font-size: 13px; font-weight: 600; color: var(--green);
  transition: all 0.3s ease;
}
.status-pill.stalled { background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.3); color: var(--yellow); }
.status-pill.offline { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.3); color: var(--red); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.metric-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px;
  transition: transform 0.15s ease, border-color 0.15s ease;
}
.metric-card:hover { transform: translateY(-2px); border-color: #374151; }
.metric-label { font-size: 11px; text-transform: uppercase; color: var(--text-dim); letter-spacing: 0.5px; margin-bottom: 6px; }
.metric-value {
  font-family: var(--font-mono);
  font-size: 26px; font-weight: 700;
  color: var(--text);
}
.metric-value.good { color: var(--green); }
.metric-value.warn { color: var(--yellow); }
.metric-value.bad { color: var(--red); }
.metric-bar {
  height: 4px; background: #1f2937; border-radius: 2px; margin-top: 10px; overflow: hidden;
}
.metric-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s ease; }
.metric-bar-fill.good { background: var(--green); }
.metric-bar-fill.warn { background: var(--yellow); }
.metric-bar-fill.bad { background: var(--red); }
.chart-section {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px;
  margin-bottom: 24px;
}
.chart-title { font-size: 12px; text-transform: uppercase; color: var(--text-dim); margin-bottom: 10px; letter-spacing: 0.5px; }
#throughputChart { width: 100%; height: 160px; display: block; }
.table-section {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 20px;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 12px 14px; text-align: right; border-bottom: 1px solid var(--border); }
th:first-child, td:first-child { text-align: left; }
th {
  background: rgba(31,41,55,0.6);
  font-size: 11px; text-transform: uppercase; color: var(--text-dim);
  font-weight: 600; letter-spacing: 0.4px;
}
tr:hover td { background: var(--panel-hover); }
td { font-family: var(--font-mono); transition: background 0.15s; }
.symbol { font-weight: 700; color: var(--text); font-family: var(--font-sans); }
.sparkline { width: 80px; height: 28px; display: inline-block; vertical-align: middle; }
.int-bar {
  display: inline-block; width: 60px; height: 6px; background: #1f2937; border-radius: 3px; overflow: hidden; vertical-align: middle; margin-left: 8px;
}
.int-bar-fill { height: 100%; border-radius: 3px; transition: width 0.4s ease; }
.int-bar-fill.good { background: var(--green); }
.int-bar-fill.warn { background: var(--yellow); }
.int-bar-fill.bad { background: var(--red); }
footer {
  display: flex; justify-content: space-between;
  font-size: 12px; color: var(--text-dim);
  margin-top: 8px;
}
@media (max-width: 900px) {
  .metrics { grid-template-columns: repeat(2, 1fr); }
  th, td { padding: 10px 8px; font-size: 12px; }
  .sparkline { width: 60px; height: 24px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo">&#x2B21;</div>
      <div>
        <h1>B3 TRADING <span class="accent">RTD</span></h1>
        <div class="sub" id="sub">Dashboard de Operações em Tempo Real</div>
      </div>
    </div>
    <div class="status-pill" id="statusPill">
      <span class="dot" id="statusDot"></span>
      <span id="statusText">CONECTADO</span>
    </div>
  </header>
  
  <div class="metrics" id="top-metrics">
    <div class="metric-card">
      <div class="metric-label">Status Scorer ML</div>
      <div class="metric-value" id="ml-status">---</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Latência Loop</div>
      <div class="metric-value" id="loop-latency">---</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Eventos Processados</div>
      <div class="metric-value" id="events-total">---</div>
    </div>
  </div>

  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
    <section class="table-section">
      <div class="chart-title" style="padding: 15px 15px 0;">Sinais Ativos</div>
      <table>
        <thead><tr><th>Ativo</th><th>Sinal</th><th>Confiança</th><th>ML Prob</th></tr></thead>
        <tbody id="signals-body"></tbody>
      </table>
    </section>
    
    <section class="chart-section">
      <div class="chart-title">Importância de Features (Gain)</div>
      <div id="ml-features" style="margin-top: 10px;"></div>
    </section>
  </div>
</div>

<script>
let lastFeaturesJson = '';

async function update() {
    try {
        const [rAll, rHealth] = await Promise.all([fetch('/api/all'), fetch('/health')]);
        const data = await rAll.json();
        const health = await rHealth.json();

        // 1. Atualizar Status e Gráfico de Features
        if (data.ml_health) {
            document.getElementById('ml-status').innerText = data.ml_health.fallos > 0 ? `ERRO (${data.ml_health.fallos})` : 'OPERACIONAL';
            document.getElementById('ml-status').className = 'metric-value ' + (data.ml_health.fallos > 0 ? 'bad' : 'good');

            const featuresJson = JSON.stringify(data.ml_health.top_features || {});
            if (data.ml_health.top_features && featuresJson !== lastFeaturesJson) {
                lastFeaturesJson = featuresJson;
            const container = document.getElementById('ml-features');
            const features = data.ml_health.top_features;
            const maxVal = Math.max(...Object.values(features), 1);
            
            let html = '<table style="width:100%; border:none;">';
            Object.entries(features).slice(0, 12).forEach(([name, val]) => {
                const pct = (val / maxVal * 100).toFixed(1);
                html += `<tr style="border:none;">
                    <td style="text-align:left; border:none; padding:4px 0; font-size:11px; color:var(--text-dim); width:40%;">${name}</td>
                    <td style="border:none; padding:4px 0;">
                        <div class="metric-bar" style="margin:0; height:6px;">
                            <div class="metric-bar-fill good" style="width:${pct}%"></div>
                        </div>
                    </td>
                </tr>`;
            });
            container.innerHTML = html + '</table>';
            }
        }

        // 2. Atualizar Sinais
        document.getElementById('signals-body').innerHTML = Object.entries(data.sinais || {}).map(([sym, s]) => `
            <tr>
                <td class="symbol">${sym}</td>
                <td class="${s.sinal > 0 ? 'good' : (s.sinal < 0 ? 'bad' : '')}">${s.sinal > 0 ? 'COMPRA' : (s.sinal < 0 ? 'VENDA' : 'NEUTRO')}</td>
                <td>${(s.confianca * 100).toFixed(1)}%</td>
                <td>${s.ml_prob ? s.ml_prob.toFixed(3) : '---'}</td>
            </tr>`).join('');

        // 3. Atualizar Saúde
        document.getElementById('loop-latency').innerText = health.latencia_loop_ms + 'ms';
        document.getElementById('events-total').innerText = health.eventos_total.toLocaleString();
    } catch (e) { console.error("Erro dashboard:", e); }
}
setInterval(update, 1000); update();
</script>
</body>
</html>
"""

class DashboardState:
    def __init__(self, filas_book, filas_tt, live_stats, base_pasta, ativos_config):
        self.filas_book = filas_book
        self.filas_tt = filas_tt
        self.live_stats = live_stats
        self.base_pasta = base_pasta
        self.ativos_config = ativos_config

    def _live_get(self, a_idx, campo):
        # LIVE_FIELDS order mapping
        fields = ["book_capturados", "book_gravados", "tt_detectados", "tt_gravados", "drops", "falhas_gravacao"]
        try:
            idx = fields.index(campo)
            return int(self.live_stats[a_idx * len(fields) + idx])
        except: return 0

    def payload(self):
        import datetime
        ativos = []
        total = {k: 0 for k in ('book_capturados','book_gravados','tt_detectados','tt_gravados','drops','falhas_gravacao')}
        for i, ativo in enumerate(self.ativos_config):
            bcap = self._live_get(i, 'book_capturados')
            bgrav = self._live_get(i, 'book_gravados')
            ttcap = self._live_get(i, 'tt_detectados')
            ttgrav = self._live_get(i, 'tt_gravados')
            drops = self._live_get(i, 'drops')
            fails = self._live_get(i, 'falhas_gravacao')
            integridade = (ttgrav / ttcap * 100.0) if ttcap else 100.0
            for k,v in [('book_capturados',bcap),('book_gravados',bgrav),('tt_detectados',ttcap),('tt_gravados',ttgrav),('drops',drops),('falhas_gravacao',fails)]: 
                total[k]+=v
            ativos.append({
                'simbolo': ativo['simbolo'],
                'book_capturados': bcap, 'book_gravados': bgrav,
                'tt_detectados': ttcap, 'tt_gravados': ttgrav,
                'drops': drops, 'falhas_gravacao': fails,
                'integridade': round(integridade, 4)
            })
        return {
            'timestamp': datetime.datetime.now().strftime('%H:%M:%S'),
            'base': self.base_pasta,
            'ativos': ativos,
            'total': total
        }

class DashboardAPI(BaseHTTPRequestHandler):
    """Handler HTTP para o dashboard. Roteia para os módulos core/."""

    app = None  # Referência para App (setado externamente)
    state = None # Referência para DashboardState
    _snapshot = None  # Cache de dados (refreshed 1x/s)
    _snapshot_ts = 0.0  # Timestamp do último refresh

    def _get_snapshot(self):
        """Retorna snapshot cacheado (refreshed 1x/s) para reduzir lock contention."""
        now = time.time()
        if DashboardAPI._snapshot is None or (now - DashboardAPI._snapshot_ts) > 1.0:
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
            }
            DashboardAPI._snapshot_ts = now
        return DashboardAPI._snapshot

    def do_GET(self):
        p = urlparse(self.path)
        params = parse_qs(p.query)
        app = self.app

        if p.path == '/':
            self._serve_dashboard_html()
        elif p.path == '/api/status':
            if self.state:
                self._json(self.state.payload())
            else:
                self._json({"error": "State not initialized"})
        elif p.path == '/legacy':
            self._html(app.html() if hasattr(app, 'html') else 'legacy mode disabled')
        elif p.path == '/api/book_level':
            self._json(app.get_book_level())
        elif p.path == '/api/features':
            f = app.get_features()
            from config import ATIVO_PRINCIPAL, ATIVO_CONTEXTO
            f['_principal'] = ATIVO_PRINCIPAL
            f['_contexto'] = ATIVO_CONTEXTO
            self._json(f)
        elif p.path == '/api/sinais':
            self._json(app.get_sinais())
        elif p.path == '/api/posicao':
            self._json(app.get_posicao() or {'acao': 'SEM_POSICAO'})
        elif p.path == '/api/learning':
            self._json(app.get_estatisticas())
        elif p.path == '/api/memoria':
            self._json(app.get_memoria())
        elif p.path == '/api/book':
            self._json(app.get_book_stats())
        elif p.path == '/api/metricas':
            self._json(app.calcular_metricas())
        elif p.path == '/api/resumo':
            from config import ATIVO_PRINCIPAL
            a = params.get('ativo', [ATIVO_PRINCIPAL])[0]
            self._json(app.get_resumo(a))
        elif p.path == '/api/padroes':
            self._json(app.market_state.padroes.get_resumo())
        elif p.path == '/api/rtd_health':
            self._json(app.get_rtd_health())
        elif p.path == '/api/saldo_corretoras':
            self._json(app.get_saldo_corretoras())
        elif p.path == '/api/contexto':
            self._json(app.get_contexto_mercado())
        elif p.path == '/api/ml_health':
            if app.scorer:
                self._json(app.scorer.estado_salud())
            else:
                self._json({"error": "Scorer not initialized"})
        elif p.path == '/api/historico':
            self._json(app.get_historico())
        elif p.path == '/api/decisoes':
            # Decision Journal: últimas decisões
            decisoes = app.journal.listar(limite=50) if hasattr(app, 'journal') else []
            self._json([d.to_dict() for d in decisoes])
        elif p.path.startswith('/api/decisoes/'):
            # Decision Journal: buscar por ID
            did = p.path.split('/')[-1]
            entry = app.journal.buscar(id=did) if hasattr(app, 'journal') else None
            self._json(entry.to_dict() if entry else {'error': 'not found'})
        elif p.path == '/api/all':
            # Usa snapshot cacheado (1x/s) para reduzir lock contention no loop principal
            snap = self._get_snapshot()
            etag = f'"{getattr(app, "revision", 0)}"'
            if self.headers.get('If-None-Match') == etag:
                self.send_response(304)
                self.end_headers()
                return
            self._json(snap, etag=etag)
        elif p.path == '/health':
            uptime = time.time() - getattr(app, 'tempo_inicio', time.time())
            from config import ATIVO_PRINCIPAL
            self._json({
                'status': 'ok' if getattr(app, '_conexao_ok', False) else 'disconnected',
                'uptime_s': round(uptime, 1),
                'latencia_loop_ms': round(getattr(app, 'latencia_atual_ms', 0), 2),
                'eventos_total': getattr(app, 'eventos_processados', 0),
                'negocios_total': app.market_state.stats.get(ATIVO_PRINCIPAL, {}).get('n', 0),
            })
        else:
            self.send_error(404)

    def _serve_dashboard_html(self):
        """Serve dashboard_pro.html com cache por mtime (0 I/O no hot path)."""
        try:
            _dash = pathlib.Path(__file__).resolve().parent.parent / 'dashboard_pro.html'
            _mtime = _dash.stat().st_mtime if _dash.exists() else -1
            if getattr(DashboardAPI, '_dash_mtime', None) != _mtime:
                DashboardAPI._dash_html = _dash.read_text(encoding='utf-8') if _dash.exists() else None
                DashboardAPI._dash_mtime = _mtime
            if DashboardAPI._dash_html:
                self._html(DashboardAPI._dash_html)
                return
        except Exception:
            pass
        self._html(self.app.html() if hasattr(self.app, 'html') else 'dashboard not found')

    def _html(self, body):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode())

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
        pass

    def handle_error(self, request, client_address):
        """Suprime tracebacks de ConnectionAbortedError."""
        import sys
        exc = sys.exc_info()[0]
        if exc in (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            return
        super().handle_error(request, client_address)
