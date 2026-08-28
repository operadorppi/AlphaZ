#!/usr/bin/env python3
"""
motor_rt_alphaz.py v7 — Leitura RTD em tempo real: Book 500 + T&T 500
Merge v2 (análise de book) + v5 (gestão robusta) + v7+ (WDO inverso) + v7 (padrões)
========================================================================
MELHORIAS v7 (sobre v6)
========================================================================
NOVO: SISTEMA DE APRENDIZADO DE PADRÕES
- Detecção de spoof por corretora (coloca volume e retira rápido)
- Detecção de stop-hunt (rompimento falso de topo/fundo)
- Perfil horário por corretora (agressividade por hora do dia)
- Níveis de stop memorizados (topos/fundos que revertem)
- Persistência entre sessões em padroes_memoria.json
- Decay temporal (esquece padrões não reforçados)
- Dashboard com top spoofers e níveis de stop

CORREÇÕES WDO→WIN (v6):
- cross_asset agora trata correlação INVERSA (dólar sobe → índice cai)
- Detecção de liderança temporal WDO→WIN
- Confirmação dinâmica baseada no WDO

DO V6 (mantidas):
- Trailing stop + breakeven com stop_preco explícito
- Verificação de TP/SL em tempo real (0.25s)
- PercentilTracker adaptativo (bisect, O(log n))
- Circuit breaker (perdas consecutivas, drawdown dia, trades dia)
- Checkpoint de posição em disco
- Custos de execução (5pts WIN, 1pt WDO)
- Aprendizado MFE/MAE com decay
- CONFIG centralizado
- Fila de processamento + thread de persistência
- Todas as 16 features do score (defesa, absorção, thinning, layering, cross-asset)
- /api/book, /api/metricas, /api/resumo
- calcular_metricas() (Sharpe, PF, DD, Expectancy)
- Detecção de regime (tendência/lateral/vol)
- get_book_stats()

CORREÇÕES (v6):
- dedup_check: off-by-one do deque maxlen (memory leak no set)
- _flush_trades: quebras de linha corretas
- PercentilTracker: sort O(n log n) → bisect O(log n)
- _book_persist: limpa corretoras mortas
- _suavizar_sinal: sem código duplicado
- Sharpe: anualização correta por trade
- confirmacao_necessaria: thread-safe com lock

USO: python motor_rt_alphaz.py v7 [PRINCIPAL] [CONTEXTO]
     python motor_rt_alphaz.py v7 WINV26 WDOU26
========================================================================
"""
import sys, os, re, time, json, signal, logging, threading, queue, unicodedata, bisect, copy, math
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict, deque, OrderedDict
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motor_web as mw
from captura_eventos_ms import CapturaEventosMS
from features_lib import OFITracker, BookLevelFeatures, EWMAZScore, fase_sessao, dias_ate_vencimento

# ML scorer (opcional — se modelo existe, usa ML alongside heuristica)
try:
    from scorer import ScorerML
    HAS_SCORER = True
except ImportError:
    HAS_SCORER = False

# ============================================================
#   CONFIGURAÇÃO CENTRAL
# ============================================================
CONFIG = {
    "web_host": "127.0.0.1",
    "web_port": 5001,
    "ativos": [s.upper() for s in sys.argv[1:3]] if len(sys.argv) >= 3 else ["WINV26", "WDOU26"],
    "book_linhas": 60,
    "book_split": 30,
    "tt_linhas": 1000,
    "save_dir": os.environ.get("SINAL_RT_DIR", r"D:\MarketData\mimo"),
    "ml_modelo": "",
    "ml_threshold": 0.6,
    "save_intervalo": 60,
    "book_snapshot_intervalo": 0.25,
    "trades_mem_max": 50_000,
    "recent_dedup_max": 20_000,  # unused - dedup is per-segment
    "hist_segs_max": 3_600,
    "features_seg_max": 7_200,
    "book_events_seg_max": 600,
    "corr_hist_max": 5_000,
    "agressao_janela_s": 120,
    "max_refresh_falhas": 200,
    "confirmacao_necessaria": 3,
    "reversao_fecha": True,
    "min_holding_reversao_s": 90,
    "confianca_min_reversao": 0.75,
    "tempo_max_posicao_s": 300,
    "usar_breakeven": True,
    "usar_trailing": True,
    "aprendizado_delta": 0.02,
    "aprendizado_decay": 0.998,
    "aprendizado_min_amostras": 5,
    "confianca_decay": 0.75,
    "limiar_confirmacao": 0.55,
    "limiar_reset": 0.15,
    "janela_percentil_segs": 1800,
    "amostra_minima_percentil": 60,
    "percentil_aggr": 0.70,
    "percentil_eficiencia": 0.75,
    "percentil_aceleracao": 0.70,
    "percentil_book_imb": 0.65,
    "fallback_aggr_min": 0.1,
    "fallback_eficiencia_min": 0.02,
    "fallback_absorcao_eficiencia_max": 0.001,
    "fallback_aceleracao_min": 0.1,
    "fallback_book_imb_min": 0.1,
    "custos_execucao_pontos": {"WIN": 5.0, "WDO": 1.0},
    "horario_abertura_fim": (10, 0),
    "horario_almoco_inicio": (12, 0),
    "horario_almoco_fim": (13, 30),
    "horario_fechamento": (16, 30),
    "desligar_horarios_ruins": False,
    "cooldown_entre_trades_s": 45,
    "max_perdas_consecutivas": 3,
    "max_trades_dia": 15,
    "max_drawdown_dia_pontos": -500.0,
    # v9.7: normaliza as contribuições do score por volatilidade EWMA
    # (estacionaridade entre manhã/tarde). EXPERIMENTAL — default OFF;
    # ative em config.json apenas após validar em simulação.
    "normalizar_score": False,
    # Circuit breaker — niveis
    "cb_nivel1_perdas": 3, "cb_nivel1_pnl": -100.0,
    "cb_nivel2_perdas": 5, "cb_nivel2_pnl": -300.0,
    "cb_nivel3_perdas": 7, "cb_nivel3_pnl": -500.0,
    # v7: padrões
    "spoof_vol_min": 500,
    "spoof_retirada_pct": 0.3,
    "stop_hunt_reversao_pts": 10,
    "stop_hunt_janela_s": 30,
    "padroes_decay_horas": 0.98,
    "max_salto_preco_pct": 0.15,
    "faixas_preco": {
        "WIN": [150000, 250000],
        "IND": [150000, 250000],
        "WDO": [1000, 20000],
        "DOL": [1000, 20000],
    },
    # v8: Estrategias por regime
    "estrategias": {
        # Regimes basicos
        "tendencia_alta":  {"tipo": "momentum", "tp_mult": 1.2, "sl_mult": 0.8, "confirmacao": 2, "limiar_confirmacao": 0.45, "cooldown_entre_trades_s": 30, "max_holding_s": 120},
        "tendencia_baixa": {"tipo": "momentum", "tp_mult": 1.2, "sl_mult": 0.8, "confirmacao": 2, "limiar_confirmacao": 0.45, "cooldown_entre_trades_s": 30, "max_holding_s": 120},
        "lateral":         {"tipo": "reversao", "tp_mult": 1.0, "sl_mult": 0.8, "confirmacao": 4, "limiar_confirmacao": 0.6, "cooldown_entre_trades_s": 60, "max_holding_s": 60},
        "vol_alta":        {"tipo": "breakout", "tp_mult": 1.5, "sl_mult": 1.2, "confirmacao": 1, "limiar_confirmacao": 0.65, "cooldown_entre_trades_s": 20, "max_holding_s": 30},
        "vol_baixa":       {"tipo": "neutro",   "tp_mult": 0.5, "sl_mult": 1.0, "confirmacao": 5, "limiar_confirmacao": 0.5, "cooldown_entre_trades_s": 90, "max_holding_s": 300},
        # Regimes compostos (direcao x volatilidade)
        "tendencia_alta_vol_alta":  {"tipo": "momentum", "tp_mult": 1.5, "sl_mult": 1.0, "confirmacao": 1, "limiar_confirmacao": 0.4, "cooldown_entre_trades_s": 25, "max_holding_s": 90},
        "tendencia_alta_vol_baixa": {"tipo": "momentum", "tp_mult": 1.0, "sl_mult": 0.6, "confirmacao": 3, "limiar_confirmacao": 0.5, "cooldown_entre_trades_s": 40, "max_holding_s": 150},
        "tendencia_baixa_vol_alta":  {"tipo": "momentum", "tp_mult": 1.5, "sl_mult": 1.0, "confirmacao": 1, "limiar_confirmacao": 0.4, "cooldown_entre_trades_s": 25, "max_holding_s": 90},
        "tendencia_baixa_vol_baixa": {"tipo": "momentum", "tp_mult": 1.0, "sl_mult": 0.6, "confirmacao": 3, "limiar_confirmacao": 0.5, "cooldown_entre_trades_s": 40, "max_holding_s": 150},
        "lateral_vol_alta":  {"tipo": "breakout", "tp_mult": 1.2, "sl_mult": 1.0, "confirmacao": 2, "limiar_confirmacao": 0.55, "cooldown_entre_trades_s": 30, "max_holding_s": 60},
        "lateral_vol_baixa": {"tipo": "neutro",   "tp_mult": 0.5, "sl_mult": 0.8, "confirmacao": 5, "limiar_confirmacao": 0.5, "cooldown_entre_trades_s": 90, "max_holding_s": 300},
    },
    # v9.27: volatility-targeted position sizing
    "position_sizing": {
        "target_risk_per_trade": 60,
        "max_position_size": 10,
    },
}

CONFIG["ativo_principal"] = CONFIG["ativos"][0]
CONFIG["ativo_contexto"] = CONFIG["ativos"][1] if len(CONFIG["ativos"]) > 1 else None
ATIVO_PRINCIPAL = CONFIG["ativo_principal"]
ATIVO_CONTEXTO = CONFIG["ativo_contexto"]
SAVE_DIR = CONFIG["save_dir"]

# ============================================================
#   CONFIG EXTERNALIZADO — override via config.json ou ENV
# ============================================================
def _carregar_config_externa():
    """Le config.json e sobrescreve defaults do CONFIG.
    Tambem aceita variaveis de ambiente: SINAL_RT_DIR, WEB_PORT, ATOIVOS."""
    global CONFIG
    # 1. Arquivo config.json no mesmo diretorio
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if os.path.exists(cfg_path):
        try:
            import json as _json
            with open(cfg_path, 'r', encoding='utf-8') as f:
                ext = _json.load(f)
            if 'base_dir' in ext:
                CONFIG['save_dir'] = ext.get('save_dir', os.path.join(ext['base_dir'], 'mimo'))
            if 'save_dir' in ext:
                CONFIG['save_dir'] = ext['save_dir']
            if 'ml_modelo' in ext:
                CONFIG['ml_modelo'] = ext['ml_modelo']
            if 'ml_threshold' in ext:
                CONFIG['ml_threshold'] = ext['ml_threshold']
            if 'web' in ext:
                CONFIG['web_host'] = ext['web'].get('host', CONFIG['web_host'])
                CONFIG['web_port'] = ext['web'].get('port', CONFIG['web_port'])
            if 'ativos' in ext and len(sys.argv) < 3:
                CONFIG['ativos'] = [s.upper() for s in ext['ativos']]
            if 'rtd' in ext:
                CONFIG['book_linhas'] = ext['rtd'].get('book_linhas', CONFIG['book_linhas'])
                CONFIG['book_split'] = CONFIG['book_linhas'] // 2
                CONFIG['tt_linhas'] = ext['rtd'].get('tt_linhas', CONFIG['tt_linhas'])
            if 'desligar_horarios_ruins' in ext:
                CONFIG['desligar_horarios_ruins'] = bool(ext['desligar_horarios_ruins'])
            if 'normalizar_score' in ext:
                CONFIG['normalizar_score'] = bool(ext['normalizar_score'])
            if 'horarios' in ext:
                for k in ['abertura_fim', 'almoco_inicio', 'almoco_fim', 'fechamento']:
                    if k in ext['horarios']:
                        CONFIG['horario_' + k] = tuple(ext['horarios'][k])
            if 'trading' in ext:
                t = ext['trading']
                CONFIG['max_trades_dia'] = t.get('max_trades_dia', CONFIG['max_trades_dia'])
                CONFIG['max_drawdown_dia_pontos'] = t.get('max_drawdown_dia', CONFIG['max_drawdown_dia_pontos'])
                # max_holding_s: 0 = timeout de posição desligado
                CONFIG['tempo_max_posicao_s'] = t.get('max_holding_s', CONFIG['tempo_max_posicao_s'])
                # aceita as duas grafias de custo de execução
                if 'custo_execucao' in t or 'custos_execucao_pontos' in t:
                    CONFIG['custos_execucao_pontos'] = (t.get('custo_execucao')
                                                        or t.get('custos_execucao_pontos')
                                                        or CONFIG['custos_execucao_pontos'])
            if 'position_sizing' in ext:
                ps = ext['position_sizing']
                CONFIG['position_sizing'] = {
                    'target_risk_per_trade': ps.get('target_risk_per_trade', CONFIG.get('position_sizing', {}).get('target_risk_per_trade', 60)),
                    'max_position_size': ps.get('max_position_size', CONFIG.get('position_sizing', {}).get('max_position_size', 10)),
                }
            if 'circuit_breaker' in ext:
                cb = ext['circuit_breaker']
                CONFIG['cb_nivel1_perdas'] = cb.get('nivel1_perdas', CONFIG.get('cb_nivel1_perdas', 3))
                CONFIG['cb_nivel1_pnl'] = cb.get('nivel1_pnl', CONFIG.get('cb_nivel1_pnl', -100.0))
                CONFIG['cb_nivel2_perdas'] = cb.get('nivel2_perdas', CONFIG.get('cb_nivel2_perdas', 5))
                CONFIG['cb_nivel2_pnl'] = cb.get('nivel2_pnl', CONFIG.get('cb_nivel2_pnl', -300.0))
                CONFIG['cb_nivel3_perdas'] = cb.get('nivel3_perdas', CONFIG.get('cb_nivel3_perdas', 7))
                CONFIG['cb_nivel3_pnl'] = cb.get('nivel3_pnl', CONFIG.get('cb_nivel3_pnl', -500.0))
            SAVE_DIR = CONFIG['save_dir']
            print('[CONFIG] config.json carregado: save_dir=' + CONFIG['save_dir'])
        except Exception as e:
            print(f'[CONFIG] Erro ao ler config.json: {e} - usando defaults')
    # 2. Variaveis de ambiente sobrescrevem tudo
    if os.environ.get('SINAL_RT_DIR'):
        CONFIG['save_dir'] = os.environ['SINAL_RT_DIR']
    if os.environ.get('WEB_PORT'):
        CONFIG['web_port'] = int(os.environ['WEB_PORT'])

_carregar_config_externa()
CONFIG["ativo_principal"] = CONFIG["ativos"][0]
CONFIG["ativo_contexto"] = CONFIG["ativos"][1] if len(CONFIG["ativos"]) > 1 else None
ATIVO_PRINCIPAL = CONFIG["ativo_principal"]
ATIVO_CONTEXTO = CONFIG["ativo_contexto"]
SAVE_DIR = CONFIG["save_dir"]

shutdown = threading.Event()
log = logging.getLogger("SinalRTv7")
fila_eventos = queue.SimpleQueue()
ERROS_GLOBAIS = defaultdict(int)

# ============================================================
#   HELPERS
# ============================================================
def fnum(v, d=0.0):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d

def fint(v, d=0):
    try:
        return int(float(v)) if v is not None else d
    except (TypeError, ValueError):
        return d

def sstr(v):
    return "" if v is None else str(v).strip()

def custo_execucao(ativo):
    for prefixo, custo in CONFIG["custos_execucao_pontos"].items():
        if ativo.upper().startswith(prefixo):
            return custo
    return 0.0

def horario_permite_abrir(agora=None):
    if not CONFIG["desligar_horarios_ruins"]:
        return True
    agora = agora or datetime.now()
    hm = (agora.hour, agora.minute)
    if hm < CONFIG["horario_abertura_fim"]:
        return False
    if CONFIG["horario_almoco_inicio"] <= hm < CONFIG["horario_almoco_fim"]:
        return False
    if hm >= CONFIG["horario_fechamento"]:
        return False
    return True

def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s))
                   if unicodedata.category(c) != 'Mn').strip().casefold()

CORRETORAS_INST = {_norm(x) for x in (
    'Goldman', 'UBS', 'JP Morgan', 'Morgan', 'Safra', 'Ideal', 'Santander',
    'Itau', 'Bradesco', 'BTG', 'Tullett', 'Mirae', 'Coinvalores',
    'Terra', 'Ativa', 'Socopa',
    'BGC Liquidez', 'Merrill', 'Santander Institucional', 'Lev')}

CORRETORAS_VAREJO = {_norm(x) for x in (
    'XP', 'Genial', 'Elliot-Warren', 'Elliot', 'Warren', 'Agora', 'Nova Futura',
    'CM Capital', 'Inter')}

def classificar_corretora(nome):
    if not nome:
        return 'outro'
    n = _norm(nome)
    if n in ('', 'none'):
        return 'outro'
    if n in CORRETORAS_INST:
        return 'inst'
    if n in CORRETORAS_VAREJO:
        return 'varejo'
    if n.isdigit():
        return 'inst'
    return 'outro'

_RE_HMS = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,6}))?')

def parse_hms_ms(v):
    m = _RE_HMS.search(str(v))
    if not m:
        return 0
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    frac = m.group(4) or ''
    ms = int(frac.ljust(3, '0')[:3]) if frac else 0
    return ((h * 3600 + mi * 60 + s) * 1000) + ms


def _tod_ms(dt=None):
    """Time-of-day em ms (mesmo relógio do T&T do RTD: ex. 09:30 → 34_200_000).
    Usado pelos cutoffs do CrossAssetEngine, cujos timestamps são hora-do-dia."""
    dt = dt or datetime.now()
    return ((dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000) + dt.microsecond // 1000

# ============================================================
#   MEMÓRIA DE PADRÕES (v7)
# ============================================================
class PadroesMemoria:
    """Aprende padrões repetitivos ao longo do dia e entre sessões.
    - Spoof: corretora coloca volume grande e retira rápido sem executar
    - Stop-hunt: rompimento de extremo recente seguido de reversão rápida
    - Absorvedor: corretora defendendo nível persistentemente
    - Perfil horário: agressividade por corretora por hora do dia
    """
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.lock = threading.Lock()
        
        # Perfil por corretora
        self.perfil = defaultdict(lambda: {
            'spoofs': 0, 'absorcoes': 0, 'stop_hunts': 0,
            'liderancas_varejo': 0, 'liderancas_inst': 0,
            'ultima_spoof': 0, 'ultima_abs': 0,
            'horas_ativas': defaultdict(float),
            'consistencia_padrao': 0.0,
        })
        
        # Níveis de stop conhecidos
        self.niveis_stop = {}
        
        # Histórico de stop-hunts
        self.hunts_recentes = deque(maxlen=200)
        
        # Topos/fundos recentes
        self.extremos_preco = deque(maxlen=300)
        
        # Snapshot anterior do book
        self._book_anterior = {}
        
        # Stop-hunt em 2 fases: rompimento pendente por ativo (fase 1) →
        # reversão (fase 2). ativo -> {'tipo', 'nivel', 'preco_break', 'ts'}
        self._breakout = {}
        
        # Decay
        self.ultima_atualizacao = time.time()
        self._carregar()
    
    def _carregar(self):
        p = Path(self.base_dir) / 'padroes_memoria.json'
        if not p.exists():
            return
        try:
            st = json.loads(p.read_text(encoding='utf-8'))
            for b, dados in st.get('perfil', {}).items():
                self.perfil[b].update({
                    k: v for k, v in dados.items()
                    if k in ('spoofs', 'absorcoes', 'stop_hunts',
                             'liderancas_varejo', 'liderancas_inst',
                             'consistencia_padrao')
                })
                if isinstance(dados.get('horas_ativas'), dict):
                    self.perfil[b]['horas_ativas'] = defaultdict(
                        float, {int(h): float(v) for h, v in dados['horas_ativas'].items()})
            if st.get('data') == date.today().isoformat():
                self.niveis_stop = st.get('niveis_stop', {})
            log.info(f"[PADROES] carregado: {len(self.perfil)} corretoras, "
                     f"{len(self.niveis_stop)} níveis de stop")
        except Exception as e:
            log.warning(f"[PADROES] falha ao carregar: {e}")
    
    def salvar(self):
        with self.lock:
            try:
                out = Path(self.base_dir)
                out.mkdir(parents=True, exist_ok=True)
                st = {
                    'perfil': {b: {
                        'spoofs': d['spoofs'], 'absorcoes': d['absorcoes'],
                        'stop_hunts': d['stop_hunts'],
                        'liderancas_varejo': d['liderancas_varejo'],
                        'liderancas_inst': d['liderancas_inst'],
                        'consistencia_padrao': d['consistencia_padrao'],
                        'horas_ativas': dict(d['horas_ativas']),
                    } for b, d in self.perfil.items()},
                    'niveis_stop': self.niveis_stop,
                    'data': date.today().isoformat(),
                    'salvo_em': datetime.now().isoformat(timespec='seconds'),
                }
                (out / 'padroes_memoria.json').write_text(
                    json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
            except Exception as e:
                log.warning(f"[PADROES] falha ao salvar: {e}")
    
    def aplicar_decay(self):
        """Esquece 2% por hora os padrões não reforçados."""
        agora = time.time()
        dt_horas = (agora - self.ultima_atualizacao) / 3600
        if dt_horas < 0.1:
            return
        self.ultima_atualizacao = agora
        with self.lock:
            fator = CONFIG["padroes_decay_horas"] ** dt_horas
            for b in list(self.perfil):
                self.perfil[b]['consistencia_padrao'] *= fator
            for nivel in list(self.niveis_stop):
                self.niveis_stop[nivel]['forca'] *= fator
                if self.niveis_stop[nivel]['forca'] < 0.1:
                    del self.niveis_stop[nivel]
    
    def detectar_spoof(self, ativo, snap_atual, ts_agora):
        """Detecta spoof: corretora colocou muito e retirou muito rápido."""
        snap_ant = self._book_anterior.get(ativo, {})
        self._book_anterior[ativo] = {
            b: {'bid_vol': s.get('bid_vol_top3', 0), 'ask_vol': s.get('ask_vol_top3', 0), 'ts': ts_agora}
            for b, s in snap_atual.items()
        }
        spoofs_detectados = []
        if not snap_ant:
            return spoofs_detectados
        
        for broker, s_atual in snap_atual.items():
            s_ant = snap_ant.get(broker, {'bid_vol': 0, 'ask_vol': 0})
            for lado, campo in (('bid', 'bid_vol'), ('ask', 'ask_vol')):
                vol_ant = s_ant.get(campo, 0)
                vol_atual = s_atual.get(campo, 0)
                if vol_ant > CONFIG["spoof_vol_min"] and vol_atual < vol_ant * CONFIG["spoof_retirada_pct"]:
                    with self.lock:
                        p = self.perfil[broker]
                        p['spoofs'] += 1
                        p['ultima_spoof'] = ts_agora
                        p['consistencia_padrao'] = 0.7 * p['consistencia_padrao'] + 0.3 * 1.0
                        spoofs_detectados.append({
                            'broker': broker, 'lado': lado,
                            'vol_retirada': vol_ant - vol_atual,
                            'spoofs_total': p['spoofs']
                        })
        return spoofs_detectados
    
    def registrar_extremo(self, ativo, preco, ts_agora):
        """Registra topos e fundos recentes."""
        self.extremos_preco.append((ts_agora, preco))
        while self.extremos_preco and ts_agora - self.extremos_preco[0][0] > 900:
            self.extremos_preco.popleft()
    
    def detectar_stop_hunt(self, ativo, preco, aggr_imb, ts_agora, hist_preco):
        """Detecta stop-hunt em 2 fases (rompimento → reversão).

        Fase 1: preço rompe um extremo RECENTE (topo/fundo anterior ao
        tick atual) com agressão no mesmo sentido → marca rompimento pendente.
        Fase 2: nas próximas chamadas, se o preço volta pelo menos
        stop_hunt_reversao_pts contra o rompimento, registra o stop-hunt.

        (A versão antiga comparava preco > topo e preco < max_recente-10
        no MESMO tick — logicamente impossível, nunca disparava.)
        """
        if len(hist_preco) < 30:
            return None
        
        # Extremos ANTERIORES ao tick atual (o atual já foi registrado)
        antigos = [p for t, p in self.extremos_preco if t < ts_agora]
        if len(antigos) < 10:
            return None
        topo = max(antigos)
        fundo = min(antigos)
        
        pend = self._breakout.get(ativo)
        janela_s = CONFIG["stop_hunt_janela_s"]
        
        # Fase 1: novo rompimento (ou pendência expirada → rearma)
        if pend is None or ts_agora - pend['ts'] > janela_s:
            if preco > topo and aggr_imb > 0.3:
                self._breakout[ativo] = {'tipo': 'topo', 'nivel': topo,
                                         'preco_break': preco, 'ts': ts_agora}
                return None
            if preco < fundo and aggr_imb < -0.3:
                self._breakout[ativo] = {'tipo': 'fundo', 'nivel': fundo,
                                         'preco_break': preco, 'ts': ts_agora}
                return None
            return None
        
        # Fase 2: verifica reversão do rompimento pendente
        lim = CONFIG["stop_hunt_reversao_pts"]
        if pend['tipo'] == 'topo' and preco <= pend['preco_break'] - lim:
            self._breakout.pop(ativo, None)
            nivel = int(round(pend['nivel'] / 5) * 5)
            self._registrar_stop_hunt(nivel, 'topo', preco, ts_agora)
            return {'nivel': nivel, 'tipo': 'topo', 'preco_hunt': preco}
        if pend['tipo'] == 'fundo' and preco >= pend['preco_break'] + lim:
            self._breakout.pop(ativo, None)
            nivel = int(round(pend['nivel'] / 5) * 5)
            self._registrar_stop_hunt(nivel, 'fundo', preco, ts_agora)
            return {'nivel': nivel, 'tipo': 'fundo', 'preco_hunt': preco}
        
        return None
    
    def _registrar_stop_hunt(self, nivel, tipo, preco, ts_agora):
        with self.lock:
            if nivel not in self.niveis_stop:
                self.niveis_stop[nivel] = {
                    'tipo': tipo, 'vezes_testado': 0, 'reverteu': 0,
                    'ultimo_teste': ts_agora, 'forca': 0.5
                }
            n = self.niveis_stop[nivel]
            n['vezes_testado'] += 1
            n['reverteu'] += 1
            n['ultimo_teste'] = ts_agora
            n['forca'] = min(1.0, n['forca'] + 0.2)
            self.hunts_recentes.append({
                'ts': ts_agora, 'nivel': nivel, 'tipo': tipo, 'preco': preco
            })
            log.info(f"[PADROES] stop-hunt: {tipo} @ {nivel} "
                     f"(testado {n['vezes_testado']}x, força {n['forca']:.2f})")
    
    def assinatura_liquidez(self, broker):
        """Retorna score 0..1 de consistência do padrão de liquidez da corretora."""
        with self.lock:
            p = self.perfil.get(broker)
            if not p:
                return 0.0
            score = min(1.0, (p['spoofs'] / 10) * 0.5 + p['consistencia_padrao'] * 0.5)
            return score
    
    def corretora_no_horario(self, broker, hora):
        """Retorna volume histórico da corretora nessa hora (normalizado)."""
        with self.lock:
            p = self.perfil.get(broker)
            if not p:
                return 0.0
            vol_hora = p['horas_ativas'].get(hora, 0.0)
            vol_total = sum(p['horas_ativas'].values()) or 1.0
            media = vol_total / 8
            return vol_hora / media if media > 0 else 0.0
    
    def registrar_agressao(self, broker, qtd, lado, ts_agora):
        """Registra agressão para perfil horário."""
        hora = datetime.fromtimestamp(ts_agora).hour
        with self.lock:
            p = self.perfil[broker]
            delta = qtd if lado == 'C' else -qtd
            p['horas_ativas'][hora] += abs(delta)
            if classificar_corretora(broker) == 'varejo':
                p['liderancas_varejo'] += 1
            else:
                p['liderancas_inst'] += 1
    
    def nivel_stop_perto(self, preco, tolerancia_pts=15):
        """Retorna nível de stop conhecido próximo ao preço atual."""
        nivel_arred = int(round(preco / 5) * 5)
        with self.lock:
            for delta in range(-tolerancia_pts, tolerancia_pts + 1, 5):
                n = nivel_arred + delta
                if n in self.niveis_stop:
                    dados = self.niveis_stop[n]
                    if dados['forca'] > 0.3:
                        return {'nivel': n, **dados}
        return None
    
    def get_resumo(self):
        with self.lock:
            top_spoof = sorted(
                [(b, d['spoofs'], d['consistencia_padrao'])
                 for b, d in self.perfil.items() if d['spoofs'] > 0],
                key=lambda x: -x[1])[:10]
            return {
                'top_spoofers': [{'broker': b, 'spoofs': s, 'conf': round(c, 2)}
                                 for b, s, c in top_spoof],
                'niveis_stop': [
                    {'nivel': n, **d} for n, d in self.niveis_stop.items()
                    if d['forca'] > 0.3
                ],
                'hunts_ultimos_10min': [
                    h for h in self.hunts_recentes
                    if time.time() - h['ts'] < 600
                ],
                'total_corretoras_perfil': len(self.perfil),
            }

# ============================================================
#   CONEXÃO RTD
# ============================================================
def conectar_e_descobrir():
    import comtypes.client
    srv, IRTDUpdateEvent = mw.conectar_servidor()
    notify = threading.Event()
    disc = threading.Event()
    cb = mw._criar_callback(IRTDUpdateEvent, notify, disc)
    srv.ServerStart(cb)
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline:
        comtypes.client.PumpEvents(0.1)
    book_map, tt_map = {}, {}
    for i in range(mw.MAX_JANELAS_RTD):
        for kind, prefix in (("book", "BOOK"), ("tt", "T&T")):
            try:
                tid, val = mw._connect(srv, [f"{prefix}{i}", "INFO", "ATV"])
                v = mw._normalizar_simbolo(val)
                if v and not mw._topico_invalido(v) and v in CONFIG["ativos"]:
                    if kind == "book":
                        book_map[i] = v
                    else:
                        tt_map[i] = v
            except Exception:
                ERROS_GLOBAIS['descoberta_topico'] += 1
        comtypes.client.PumpEvents(0.01)
    log.info(f"[DESCOBERTA] Book: {book_map}")
    log.info(f"[DESCOBERTA] T&T:  {tt_map}")
    return srv, book_map, tt_map

def assinar_topicos(srv, book_map, tt_map):
    import comtypes.client
    topic_map = {}
    n = 0
    BK_BID = ('OCP', 'VOC', 'ACP')
    BK_ASK = ('OVD', 'VOV', 'AVD')
    TT = ('DAT', 'PRE', 'QUL', 'AGR', 'ACP', 'AVD')
    BOOK_SPLIT = CONFIG["book_split"]
    BOOK_LINHAS = CONFIG["book_linhas"]
    TT_LINHAS = CONFIG["tt_linhas"]
    
    for j_idx, sym in book_map.items():
        for linha in range(BOOK_LINHAS):
            campos = BK_BID if linha < BOOK_SPLIT else BK_ASK
            for field in campos:
                try:
                    tid, _ = mw._connect(srv, [f"BOOK{j_idx}", field, str(linha)])
                    topic_map[tid] = ("book", j_idx, sym, field, linha)
                    n += 1
                except Exception:
                    ERROS_GLOBAIS['assinar_book'] += 1
        comtypes.client.PumpEvents(0.05)
    
    for j_idx, sym in tt_map.items():
        for linha in range(TT_LINHAS):
            for field in TT:
                try:
                    tid, _ = mw._connect(srv, [f"T&T{j_idx}", field, str(linha)])
                    topic_map[tid] = ("tt", j_idx, sym, field, linha)
                    n += 1
                except Exception:
                    ERROS_GLOBAIS['assinar_tt'] += 1
        comtypes.client.PumpEvents(0.05)
    
    for j_idx, sym in tt_map.items():
        try:
            tid, _ = mw._connect(srv, [f"T&T{j_idx}", "INFO", "NEG"])
            topic_map[tid] = ("neg", j_idx, sym, "NEG", 0)
            n += 1
        except Exception:
            ERROS_GLOBAIS['assinar_neg'] += 1
    
    log.info(f"[ASSINATURA] Total: {n} tópicos")
    return topic_map

# ============================================================
#   ESTADO
# ============================================================
class EstadoAtivo:
    def __init__(self, sym):
        self.sym = sym
        self.book_bid = [{} for _ in range(CONFIG["book_split"])]
        self.book_ask = [{} for _ in range(CONFIG["book_split"])]
        self.tt_rows = [{} for _ in range(CONFIG["tt_linhas"])]
        self.tt_last_ms = [0] * CONFIG["tt_linhas"]
        self.neg_total = 0
        self.neg_detectados = 0
        self.last_book_snap = 0.0
        # T&T: frequency-based dedup (motor_v23)
        self.warmup_tt = 0
        self.baseline_tt = False
        self.baseline_pending_tt = False
        self.ciclo_contador_tt = 0
        self.vistos_tt = {}  # signature -> count
        # Book: snapshot comparison
        self.book_ultimo_snap = None
        self.book_ultimo_t = 0.0
        # RTD health: timestamp do último trade e book recebido
        self.ultimo_neg_tempo = time.time()
        self.ultimo_book_tempo = time.time()
        self.n_neg_total_anterior = 0  # para detectar se parou de receber
        self._ultimo_preco_valido = 0.0  # v9: sanity check de preco

def processar_dados(topic_map, data, estados):
    """Captura T&T com dedup por frequência (motor_v23).
    A cada ciclo, conta a frequência de cada assinatura na tabela RTD.
    Se a contagem atual > histórico, extrai o excedente como novos negócios.
    """
    novos = []
    for tid, val in mw.parse_refresh_data(data):
        info = topic_map.get(tid)
        if not info:
            continue
        kind, j_idx, sym, field, linha = info
        estado = estados.get(sym)
        if not estado:
            continue
        if kind == "book":
            if linha < CONFIG["book_split"]:
                estado.book_bid[linha][field] = val
            else:
                estado.book_ask[linha - CONFIG["book_split"]][field] = val
        elif kind == "tt":
            estado.tt_rows[linha][field] = val
        elif kind == "neg":
            estado.neg_total = fint(val)
    
    # Processar T&T por frequência
    for sym, estado in estados.items():
        # Warmup: primeiros 60 ciclos
        if estado.warmup_tt < 60:
            estado.warmup_tt += 1
            if estado.warmup_tt == 60:
                estado.vistos_tt.clear()
                estado.baseline_tt = False
                estado.baseline_pending_tt = True
            continue
        
        estado.ciclo_contador_tt += 1
        
        # 1. Conta frequência atual
        current_counts = {}
        example_r = {}
        for r in estado.tt_rows:
            pre = fnum(r.get("PRE"))
            if pre <= 0:
                continue
            # Assinatura: DAT + ACP + PRE + QUL + AVD + AGR
            sig = (sstr(r.get("DAT")), sstr(r.get("ACP")), pre,
                   fint(r.get("QUL")), sstr(r.get("AVD")),
                   sstr(r.get("AGR")))
            current_counts[sig] = current_counts.get(sig, 0) + 1
            example_r[sig] = r
        
        # Baseline: primeiro retrato pós-warmup
        if estado.baseline_pending_tt:
            estado.vistos_tt = dict(current_counts)
            estado.baseline_pending_tt = False
            estado.baseline_tt = bool(current_counts)
            continue
        
        # 2. Extrai excedentes
        for sig, count in current_counts.items():
            seen = estado.vistos_tt.get(sig, 0)
            if count > seen:
                diff = count - seen
                estado.vistos_tt[sig] = count
                r = example_r[sig]
                dt_str = r.get("DAT")
                tms = parse_hms_ms(dt_str)
                if tms <= 0:
                    continue
                preco = fnum(r.get("PRE"))
                qtd = fint(r.get("QUL"))
                if preco <= 0 or qtd <= 0:
                    continue
                # Validacao de faixa de preco por ativo
                faixa = CONFIG.get('faixas_preco', {}).get(sym[:3], [0, 999999999])
                if preco < faixa[0] or preco > faixa[1]:
                    continue
                # Validacao de salto de preco
                ultimo_preco = estado._ultimo_preco_valido or preco
                if ultimo_preco > 0 and preco > 0:
                    salto = abs(preco - ultimo_preco) / ultimo_preco
                    if salto > CONFIG.get('max_salto_preco_pct', 0.15):
                        continue
                estado._ultimo_preco_valido = preco
                agr_raw = sstr(r.get("AGR")).lower()
                comp = sstr(r.get("ACP"))
                vend = sstr(r.get("AVD"))
                al = agr_raw
                agr = "Comprador" if "compr" in al else ("Vendedor" if "vend" in al else "neutro")
                for _ in range(diff):
                    novos.append((sym, tms, preco, qtd, agr, comp, vend))
                    estado.neg_detectados += 1
                    estado.ultimo_neg_tempo = time.time()
        
        # 3. (C3 fix) lazy decay REMOVIDO - causava re-emissao de negocios ja emitidos
        # vistos_tt so cresce; purge abaixo limita o tamanho
        
        # Purge de segurança (O(n log n) — remove os de menor contagem)
        if len(estado.vistos_tt) > 40_000 and estado.ciclo_contador_tt % 5 == 0:
            n_remove = len(estado.vistos_tt) // 20
            for sig in sorted(estado.vistos_tt, key=estado.vistos_tt.get)[:n_remove]:
                del estado.vistos_tt[sig]
    
    return novos

# ============================================================
#   BOOK
# ============================================================
def extrair_niveis_book(estado, n_niveis):
    """Extrai (preco, vol) por nivel do EstadoAtivo, para alimentar o
    OFITracker do features_lib (mesma conta em treino e producao)."""
    bid_levels = []
    ask_levels = []
    for lvl in range(n_niveis):
        bid = estado.book_bid[lvl]
        ask = estado.book_ask[lvl]
        bid_levels.append((fnum(bid.get('OCP', 0)), fint(bid.get('VOC', 0))))
        ask_levels.append((fnum(ask.get('OVD', 0)), fint(ask.get('VOV', 0))))
    return bid_levels, ask_levels

def extrair_book_snapshot(estado):
    """Converte o EstadoAtivo em book_snapshot compativel com o
    BookLevelFeatures do features_lib (mesma conta em treino e producao).
    Retorna dict com lists ordenadas por profundidade (nivel 0 = melhor)."""
    n = CONFIG['book_split']
    bid_vols = []
    bid_precos = []
    ask_vols = []
    ask_precos = []
    for lvl in range(n):
        d = estado.book_bid[lvl]
        bp = fnum(d.get('OCP', 0))
        bv = fint(d.get('VOC', 0))
        if bp > 0 and bv > 0:
            bid_precos.append(bp)
            bid_vols.append(bv)
        da = estado.book_ask[lvl]
        ap = fnum(da.get('OVD', 0))
        av = fint(da.get('VOV', 0))
        if ap > 0 and av > 0:
            ask_precos.append(ap)
            ask_vols.append(av)
    return {
        'bid_vol': bid_vols,
        'bid_preco': bid_precos,
        'ask_vol': ask_vols,
        'ask_preco': ask_precos,
    }

def snapshot_book(estado):
    snap = defaultdict(lambda: {'bid_vol': 0, 'ask_vol': 0, 'bid_preco': 0,
                                 'ask_preco': 9e18, 'bid_niveis': 0, 'ask_niveis': 0,
                                 'bid_vol_top3': 0, 'ask_vol_top3': 0})
    total_bid_vol = 0
    total_ask_vol = 0
    for lvl in range(CONFIG["book_split"]):
        d = estado.book_bid[lvl]
        vol = fint(d.get('VOC', 0))
        if vol <= 0:
            continue
        preco = fnum(d.get('OCP', 0))
        broker = str(d.get('ACP', '')).strip() or '_anon'
        if broker == 'None':
            broker = '_anon'
        snap[broker]['bid_vol'] += vol
        snap[broker]['bid_niveis'] += 1
        if preco > snap[broker]['bid_preco']:
            snap[broker]['bid_preco'] = preco
        total_bid_vol += vol
    
    for lvl in range(CONFIG["book_split"]):
        d = estado.book_ask[lvl]
        vol = fint(d.get('VOV', 0))
        if vol <= 0:
            continue
        preco = fnum(d.get('OVD', 0))
        broker = str(d.get('AVD', '')).strip() or '_anon'
        if broker == 'None':
            broker = '_anon'
        snap[broker]['ask_vol'] += vol
        snap[broker]['ask_niveis'] += 1
        if 0 < preco < snap[broker]['ask_preco']:
            snap[broker]['ask_preco'] = preco
        total_ask_vol += vol
    return dict(snap), total_bid_vol, total_ask_vol

def comparar_books(snap_ant, snap_atu, persist_book=None):
    retiradas = []
    reposicoes = []
    defesa_persistente = []
    layering = []
    todos = set(snap_ant) | set(snap_atu)
    bv_ant = sum(v.get('bid_vol', 0) for v in snap_ant.values())
    av_ant = sum(v.get('ask_vol', 0) for v in snap_ant.values())
    bv_atu = sum(v.get('bid_vol', 0) for v in snap_atu.values())
    av_atu = sum(v.get('ask_vol', 0) for v in snap_atu.values())
    thinning_bid = bv_ant - bv_atu
    thinning_ask = av_ant - av_atu
    
    for b in todos:
        a = snap_ant.get(b, {'bid_vol': 0, 'ask_vol': 0})
        c = snap_atu.get(b, {'bid_vol': 0, 'ask_vol': 0})
        if a['bid_vol'] > 5 and c['bid_vol'] < a['bid_vol'] * 0.5:
            retiradas.append({'broker': b, 'lado': 'bid', 'delta': a['bid_vol'] - c['bid_vol'], 'tipo': 'retirada'})
        if a['ask_vol'] > 5 and c['ask_vol'] < a['ask_vol'] * 0.5:
            retiradas.append({'broker': b, 'lado': 'ask', 'delta': a['ask_vol'] - c['ask_vol'], 'tipo': 'retirada'})
        if c['bid_vol'] > 10 and a['bid_vol'] == 0:
            reposicoes.append({'broker': b, 'lado': 'bid', 'delta': c['bid_vol'], 'tipo': 'reposicao'})
        elif a['bid_vol'] > 0 and c['bid_vol'] > a['bid_vol'] * 1.5:
            reposicoes.append({'broker': b, 'lado': 'bid', 'delta': c['bid_vol'] - a['bid_vol'], 'tipo': 'reposicao'})
        if c['ask_vol'] > 10 and a['ask_vol'] == 0:
            reposicoes.append({'broker': b, 'lado': 'ask', 'delta': c['ask_vol'], 'tipo': 'reposicao'})
        elif a['ask_vol'] > 0 and c['ask_vol'] > a['ask_vol'] * 1.5:
            reposicoes.append({'broker': b, 'lado': 'ask', 'delta': c['ask_vol'] - a['ask_vol'], 'tipo': 'reposicao'})
        if persist_book and b in persist_book:
            pb = persist_book[b]
            if a['bid_vol'] > 10 and c['bid_vol'] > 10 and pb.get('bid_seguidos', 0) >= 2:
                defesa_persistente.append({'broker': b, 'lado': 'bid', 'vol': c['bid_vol'], 'seguidos': pb['bid_seguidos']})
            if a['ask_vol'] > 10 and c['ask_vol'] > 10 and pb.get('ask_seguidos', 0) >= 2:
                defesa_persistente.append({'broker': b, 'lado': 'ask', 'vol': c['ask_vol'], 'seguidos': pb['ask_seguidos']})
        if a['bid_vol'] > 0 and c['bid_vol'] == 0:
            layering.append({'broker': b, 'lado': 'bid', 'tipo': 'layering_remocao'})
        if a['ask_vol'] > 0 and c['ask_vol'] == 0:
            layering.append({'broker': b, 'lado': 'ask', 'tipo': 'layering_remocao'})
    
    return {'retiradas': retiradas, 'reposicoes': reposicoes,
            'defesa_persistente': defesa_persistente,
            'thinning_bid': thinning_bid, 'thinning_ask': thinning_ask,
            'layering': layering}

# ============================================================
#   PERCENTIL TRACKER
# ============================================================
class PercentilTracker:
    def __init__(self, janela_segs=1800, amostra_minima=60):
        self.valores_ts = deque()
        self.ordenado = []
        self.janela_segs = janela_segs
        self.amostra_minima = amostra_minima

    def add(self, v, ts=None):
        ts = ts or time.time()
        self.valores_ts.append((ts, v))
        bisect.insort(self.ordenado, v)
        while self.valores_ts and ts - self.valores_ts[0][0] > self.janela_segs:
            old_ts, old_v = self.valores_ts.popleft()
            idx = bisect.bisect_left(self.ordenado, old_v)
            if idx < len(self.ordenado) and self.ordenado[idx] == old_v:
                self.ordenado.pop(idx)

    def percentil(self, p, fallback):
        if len(self.ordenado) < self.amostra_minima:
            return fallback
        idx = min(int(len(self.ordenado) * p), len(self.ordenado) - 1)
        return self.ordenado[idx]

# ============================================================
#   RANGE TRACKER
# ============================================================
class RangeTracker:
    """Detecta range de varredura: preço testando a mesma zona repetidamente."""
    def __init__(self, janela_segs=300, n_testes_min=3):
        self.precos = deque(maxlen=5000)  # (ts, preco)
        self.janela_segs = janela_segs
        self.n_testes_min = n_testes_min
        self.range_topo = 0.0
        self.range_fundo = 0.0
        self.testes_topo = 0
        self.testes_fundo = 0
        self.estado = 'indefinido'  # dentro / topo / fundo / rompimento_cima / rompimento_baixo
        self.expansao = 0.0  # + = expandindo, - = comprimindo
        self._prev_range = 0.0
    
    def atualizar(self, preco, ts):
        self.precos.append((ts, preco))
        # Remove antigos
        while self.precos and ts - self.precos[0][0] > self.janela_segs:
            self.precos.popleft()
        if len(self.precos) < 10:
            return
        
        # Calcula range dos últimos N segundos
        ps = [p for _, p in self.precos]
        topo = max(ps)
        fundo = min(ps)
        amplitude = topo - fundo
        
        # Expansão/compressão
        if self._prev_range > 0:
            self.expansao = (amplitude - self._prev_range) / self._prev_range
        self._prev_range = amplitude
        
        # Conta testes do topo e fundo (tolerância = 10% da amplitude)
        tol = max(amplitude * 0.10, 5)  # mínimo 5 pts
        self.testes_topo = sum(1 for p in ps if abs(p - topo) <= tol)
        self.testes_fundo = sum(1 for p in ps if abs(p - fundo) <= tol)
        
        self.range_topo = topo
        self.range_fundo = fundo
        
        # Estado
        margem = amplitude * 0.15
        if preco >= topo - margem:
            self.estado = 'topo'
        elif preco <= fundo + margem:
            self.estado = 'fundo'
        elif fundo + margem < preco < topo - margem:
            self.estado = 'dentro'
        else:
            self.estado = 'indefinido'
    
    def get_estado(self):
        return {
            'topo': round(self.range_topo, 1),
            'fundo': round(self.range_fundo, 1),
            'amplitude': round(self.range_topo - self.range_fundo, 1),
            'testes_topo': self.testes_topo,
            'testes_fundo': self.testes_fundo,
            'estado': self.estado,
            'expansao': round(self.expansao, 4),
            'n_amostras': len(self.precos),
        }


class AccumulationTracker:
    """Detecta acumulacao por corretora no range e direcao provavel do rompimento.
    
    Logica:
    - Se institucional esta acumulando no topo = provavel rompimento para cima
    - Se varejo esta comprando no topo + inst vendendo = reversao
    - Se institucional esta acumulando no fundo = provavel rompimento para baixo
    - Se varejo esta vendendo no fundo + inst comprando = reversao
    - WDO confirmando = sinal mais forte
    """
    def __init__(self, janela_segs=300):
        self.janela_segs = janela_segs
        # (ts, broker, lado, preco)
        self.flows = deque(maxlen=50000)
        # Acumulado por corretora
        self.saldo_corretora = defaultdict(lambda: {'c': 0, 'v': 0})
    
    def registrar(self, ts, broker, lado, preco, qtd):
        """Registra um trade de uma corretora. Mantém saldos incrementalmente."""
        if not broker or broker in ('None', ''):
            return
        self.flows.append((ts, broker, lado, preco, qtd))
        sd = self.saldo_corretora[broker]
        if lado == 'Comprador':
            sd['c'] += qtd
        elif lado == 'Vendedor':
            sd['v'] += qtd
        # agressor 'neutro' não conta em nenhum lado
    
    def _limpar_antigos(self, ts):
        """Remove trades fora da janela e DECREMENTA saldos incrementalmente.
        O(n) é apenas no número de elementos removidos (amortizado O(1) por trade)."""
        corte = ts - self.janela_segs
        while self.flows and self.flows[0][0] < corte:
            _, broker, lado, _, qtd = self.flows.popleft()
            sd = self.saldo_corretora.get(broker)
            if sd is None:
                continue
            if lado == 'Comprador':
                sd['c'] -= qtd
            elif lado == 'Vendedor':
                sd['v'] -= qtd
            # Remove corretora se ambos zeraram
            if sd['c'] <= 0 and sd['v'] <= 0:
                self.saldo_corretora.pop(broker, None)
    
    def detectar(self, ts, range_topo, range_fundo, preco_atual):
        """Detecta acumulacao e retorna direcao provavel.
        
        Retorna dict com:
        - inst_comprando: bool (institucional acumulando)
        - inst_vendendo: bool
        - varejo_comprando: bool
        - varejo_vendendo: bool
        - zona: 'topo' | 'fundo' | 'meio'
        - direcao_provavel: 'cima' | 'baixo' | 'neutro'
        - forca: 0-1
        """
        self._limpar_antigos(ts)
        
        if len(self.flows) < 20:
            return None
        
        amplitude = range_topo - range_fundo
        if amplitude < 10:
            return None
        
        # Classifica zona
        margem = amplitude * 0.20
        if preco_atual >= range_topo - margem:
            zona = 'topo'
        elif preco_atual <= range_fundo + margem:
            zona = 'fundo'
        else:
            zona = 'meio'
        
        # Saldo institucional e varejo
        inst_c = inst_v = var_c = var_v = 0
        for broker, sd in self.saldo_corretora.items():
            c, v = sd['c'], sd['v']
            tipo = classificar_corretora(broker)
            if tipo == 'inst':
                inst_c += c
                inst_v += v
            else:
                var_c += c
                var_v += v
        
        inst_net = inst_c - inst_v
        var_net = var_c - var_v
        
        inst_comprando = inst_net > 50
        inst_vendendo = inst_net < -50
        varejo_comprando = var_net > 50
        varejo_vendendo = var_net < -50
        
        # Logica de acumulacao
        direcao = 'neutro'
        forca = 0.0
        
        if zona == 'topo':
            # No topo: inst comprando = acumulacao para rompimento cima
            if inst_comprando and not varejo_comprando:
                direcao = 'cima'
                forca = min(1.0, abs(inst_net) / 200)
            # No topo: varejo comprando + inst vendendo = reversao para baixo
            elif varejo_comprando and inst_vendendo:
                direcao = 'baixo'
                forca = min(1.0, abs(var_net + abs(inst_net)) / 300)
            # No topo: todos comprando = possivel exaustao
            elif inst_comprando and varejo_comprando:
                direcao = 'baixo'  # exaustao
                forca = 0.3
        elif zona == 'fundo':
            # No fundo: inst vendendo = acumulacao para rompimento baixo
            if inst_vendendo and not varejo_vendendo:
                direcao = 'baixo'
                forca = min(1.0, abs(inst_net) / 200)
            # No fundo: varejo vendendo + inst comprando = reversao para cima
            elif varejo_vendendo and inst_comprando:
                direcao = 'cima'
                forca = min(1.0, abs(var_net + abs(inst_net)) / 300)
            # No fundo: todos vendendo = possivel exaustao
            elif inst_vendendo and varejo_vendendo:
                direcao = 'cima'  # exaustao
                forca = 0.3
        else:
            # Meio: inst forte de um lado
            if inst_comprando and forca == 0:
                direcao = 'cima'
                forca = min(0.5, abs(inst_net) / 400)
            elif inst_vendendo and forca == 0:
                direcao = 'baixo'
                forca = min(0.5, abs(inst_net) / 400)
        
        return {
            'inst_comprando': inst_comprando,
            'inst_vendendo': inst_vendendo,
            'varejo_comprando': varejo_comprando,
            'varejo_vendendo': varejo_vendendo,
            'inst_net': round(inst_net),
            'var_net': round(var_net),
            'zona': zona,
            'direcao_provavel': direcao,
            'forca': round(forca, 3),
            'n_trades': len(self.flows),
        }


# ============================================================
#   BookLevelFeatures removido — agora vem do features_lib
# ============================================================
# class BookLevelFeatures removido (usar from features_lib import BookLevelFeatures)

# ============================================================
#   CAMADA 4: CROSS ASSET ENGINE (WDO <-> WIN)
# ============================================================
class CrossAssetEngine:
    """Detecta liderança temporal WDO -> WIN, correlação rolling,
    divergência de fluxo, e resposta do WIN ao movimento do WDO.
    
    Features:
    - lag_ms: defasagem entre alteração WDO e resposta WIN
    - corr_aggr: correlação rolling de aggr_imb (janela 60s)
    - corr_imb_book: correlação rolling de book imbalance
    - divergencia: WDO movendo enquanto WIN parado (ou vice-versa)
    - wdo_leading: WDO antecipou movimento do WIN nos últimos N segs
    - resposta_win: reação do WIN ao último movimento do WDO
    """
    def __init__(self, janela_corr=60, max_lag_ms=2000):
        self.janela_corr = janela_corr
        self.max_lag_ms = max_lag_ms
        self.hist_win = deque(maxlen=1000)  # (ts_ms, preco, aggr_imb, imb_book)
        self.hist_wdo = deque(maxlen=1000)
        self._ultimo_wdo_preco = 0.0
        self._ultimo_wdo_ts = 0
        self._ultimo_win_preco = 0.0
        self._ultimo_win_ts = 0
        # v9.15: índice de preços do WIN para busca O(log n) em vez de O(n)
        self._win_precos = []  # (ts_ms, price) sorted by ts_ms (append-only, bisect ok)
        self._win_precos_ts = []  # parallel array of ts_ms for bisect
    
    def registrar(self, ativo, ts_ms, preco, aggr_imb, imb_book=0.0):
        """Registra tick de um ativo."""
        hist = self.hist_win if ativo == ATIVO_PRINCIPAL else self.hist_wdo
        hist.append((ts_ms, preco, aggr_imb, imb_book))
        if ativo == ATIVO_PRINCIPAL:
            self._ultimo_win_preco = preco
            self._ultimo_win_ts = ts_ms
            # Mantém índice de preços do WIN para busca O(log n)
            self._win_precos.append(preco)
            self._win_precos_ts.append(ts_ms)
            if len(self._win_precos) > 1000:
                self._win_precos.pop(0)
                self._win_precos_ts.pop(0)
        else:
            self._ultimo_wdo_preco = preco
            self._ultimo_wdo_ts = ts_ms
    
    def calcular(self):
        """Calcula features cross-asset."""
        if not self.hist_win or not self.hist_wdo:
            return {
                'lag_ms': 0, 'corr_aggr': 0.0, 'corr_imb_book': 0.0,
                'divergencia': 0.0, 'wdo_leading': 0.0,
                'resposta_win': 0.0, 'wdo_delta': 0.0,
            }
        
        lag_ms = self._calcular_lag()
        corr_aggr = self._correlacao_rolling('aggr')
        corr_imb = self._correlacao_rolling('imb')
        divergencia = self._calcular_divergencia()
        wdo_leading = self._wdo_leading_score()
        resposta = self._resposta_ao_wdo()
        
        wdo_delta = 0.0
        if len(self.hist_wdo) >= 2:
            t1, p1 = self.hist_wdo[-2][0], self.hist_wdo[-2][1]
            t2, p2 = self.hist_wdo[-1][0], self.hist_wdo[-1][1]
            dt = (t2 - t1) / 1000.0
            if dt > 0:
                wdo_delta = (p2 - p1) / dt
        
        return {
            'lag_ms': lag_ms,
            'corr_aggr': round(corr_aggr, 3),
            'corr_imb_book': round(corr_imb, 3),
            'divergencia': round(divergencia, 3),
            'wdo_leading': round(wdo_leading, 3),
            'resposta_win': round(resposta, 3),
            'wdo_delta': round(wdo_delta, 1),
        }
    
    def _calcular_lag(self):
        """Mede defasagem entre movimentos WDO e WIN nos últimos 5s.
        Usa bisect no índice do WIN para busca O(log n) em vez de O(n)."""
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        
        wdo_moves = []
        for i in range(1, len(self.hist_wdo)):
            t = self.hist_wdo[i][0]
            if t < cutoff:
                continue
            delta = self.hist_wdo[i][1] - self.hist_wdo[i-1][1]
            if abs(delta) >= 1:
                wdo_moves.append((t, delta))
        
        if not wdo_moves:
            return 0
        
        lags = []
        for wdo_t, wdo_delta in wdo_moves[-5:]:
            # Busca O(log n): primeiro tick do WIN após wdo_t
            idx = bisect.bisect_right(self._win_precos_ts, wdo_t)
            if idx < len(self._win_precos_ts):
                win_t = self._win_precos_ts[idx]
                win_p = self._win_precos[idx]
                win_prev = self._win_precos[idx - 1] if idx > 0 else 0.0
                win_delta = win_p - win_prev
                if abs(win_delta) >= 1 and (win_delta * wdo_delta > 0):
                    lag = win_t - wdo_t
                    if lag <= self.max_lag_ms:
                        lags.append(lag)
        
        return int(sum(lags) / len(lags)) if lags else 0
    
    def _get_prev_price(self, hist, ts_ms):
        """mantido para compatibilidade — novos callers usam _win_precos."""
        prev = 0.0
        for t, p, _, _ in hist:
            if t >= ts_ms:
                return prev
            prev = p
        return prev
    
    def _correlacao_rolling(self, campo):
        """Correlação de Pearson rolling entre WIN e WDO."""
        agora_ms = _tod_ms()
        cutoff = agora_ms - self.janela_corr * 1000
        
        bins_win = {}
        for t, p, aggr, imb in self.hist_win:
            if t < cutoff:
                continue
            b = t // 1000
            bins_win[b] = aggr if campo == 'aggr' else imb
        
        bins_wdo = {}
        for t, p, aggr, imb in self.hist_wdo:
            if t < cutoff:
                continue
            b = t // 1000
            bins_wdo[b] = aggr if campo == 'aggr' else imb
        
        common = sorted(set(bins_win) & set(bins_wdo))
        if len(common) < 10:
            return 0.0
        
        x = [bins_win[b] for b in common]
        y = [bins_wdo[b] for b in common]
        
        n = len(x)
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = (sum((xi - mx)**2 for xi in x) / n) ** 0.5
        sy = (sum((yi - my)**2 for yi in y) / n) ** 0.5
        
        if sx > 0 and sy > 0:
            return cov / (sx * sy)
        return 0.0
    
    def _calcular_divergencia(self):
        """WDO andando enquanto WIN parado = divergência."""
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        
        wdo_range = 0.0
        if self.hist_wdo:
            recentes = [p for t, p, _, _ in self.hist_wdo if t >= cutoff]
            if len(recentes) >= 2:
                wdo_range = max(recentes) - min(recentes)
        
        win_range = 0.0
        if self.hist_win:
            recentes = [p for t, p, _, _ in self.hist_win if t >= cutoff]
            if len(recentes) >= 2:
                win_range = max(recentes) - min(recentes)
        
        if wdo_range > 5 and win_range < 3:
            return -1.0
        elif win_range > 5 and wdo_range < 3:
            return 1.0
        elif wdo_range > 5 and win_range > 5:
            wdo_dir = self.hist_wdo[-1][1] - self.hist_wdo[max(0, len(self.hist_wdo)-10)][1]
            win_dir = self.hist_win[-1][1] - self.hist_win[max(0, len(self.hist_win)-10)][1]
            if wdo_dir * win_dir > 0:
                return 0.0
            else:
                return -0.5
        return 0.0
    
    def _wdo_leading_score(self):
        """WDO liderou o movimento do WIN nos últimos 5s?"""
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        
        wdo_move_t = 0
        wdo_move_delta = 0
        for i in range(len(self.hist_wdo)-1, 0, -1):
            t = self.hist_wdo[i][0]
            if t < cutoff:
                break
            delta = self.hist_wdo[i][1] - self.hist_wdo[i-1][1]
            if abs(delta) >= 2:
                wdo_move_t = t
                wdo_move_delta = delta
                break
        
        if wdo_move_t == 0:
            return 0.0
        
        for t, p, _, _ in self.hist_win:
            if t > wdo_move_t:
                win_prev = self._get_prev_price(self.hist_win, t)
                win_delta = p - win_prev
                if abs(win_delta) >= 1:
                    lag = t - wdo_move_t
                    if lag < 2000 and (win_delta * wdo_move_delta > 0):
                        return 1.0 - (lag / 2000.0)
                    return 0.0
        return -0.3
    
    def _resposta_ao_wdo(self):
        """Como o WIN reagiu ao último movimento do WDO?"""
        if len(self.hist_wdo) < 2 or len(self.hist_win) < 2:
            return 0.0
        
        wdo_delta = self.hist_wdo[-1][1] - self.hist_wdo[-2][1]
        if abs(wdo_delta) < 1:
            return 0.0
        
        agora_ms = _tod_ms()
        win_recentes = [(t, p) for t, p, _, _ in self.hist_win if t >= agora_ms - 2000]
        if len(win_recentes) < 2:
            return 0.0
        
        win_delta = win_recentes[-1][1] - win_recentes[0][1]
        
        if wdo_delta > 0:
            return min(1.0, win_delta / max(abs(wdo_delta), 1))
        else:
            return min(1.0, -win_delta / max(abs(wdo_delta), 1))


# ============================================================
#   ANÁLISE
# ============================================================
class Analise:
    PESOS_INICIAIS = {
        'preco_andando': 0.4, 'eficiencia': 0.25, 'aceleracao': 0.2,
        'persistencia': 0.15, 'inst_lidera': 0.2, 'varejo_contra': -0.15,
        'book_imb': 0.2, 'defesa': 0.15, 'absorcao_book': -0.25,
        'retirada': -0.2, 'reposicao': 0.2, 'thinning': -0.2,
        'layering': -0.1, 'delta_book': 0.15,
        'cross_asset': 0.15, 'cross_asset_preco': 0.1,
        'liquidez_removida': -0.4, 'stop_hunt': 0.5,
        'horario_inst': 0.3, 'horario_varejo': -0.3,
        'range': 0.3,
        'absorcao_preco': 0.4,
        'corretora_tt_book': 0.5,
        'acumulacao': 0.6,
        'ofi': 0.35,
        'ofi_ewma': 0.25,
        'book_level_spread': 0.2,
        'book_imb_l1': 0.20, 'book_imb_l10': 0.25, 'book_microprice': 0.15, 'book_hhi': 0.10,
        'micro_drift': 0.35,
        'imb_ponderado': 0.30,
        'slope_book': 0.25,
        'trade_metrics': 0.2,
        'cross_lag': 0.35,
        'cvd_div': 0.35,  # v9.8: divergência CVD×preço (exaustão de fluxo)
    }

    def __init__(self):
        self.buffer = defaultdict(list)
        self.seg_atual = 0
        self.features = {}
        self.sinais = {}
        self.historico = defaultdict(lambda: deque(maxlen=CONFIG["hist_segs_max"]))
        self.lock = threading.RLock()
        self.todos_negocios = deque(maxlen=CONFIG["trades_mem_max"])
        self.stats = defaultdict(lambda: {'n': 0, 'vc': 0, 'vv': 0, 'p0': 0.0, 'p1': 0.0})
        self.corr_neg = defaultdict(int)  # Capped at 5000 below
        self.corr_vol = defaultdict(int)  # Capped at 5000 below
        self.features_por_seg = OrderedDict()
        self.agressao_por_corretora = defaultdict(dict)
        self.posicao = None
        self.previsoes = []
        self.resultados = []
        self.pesos = dict(self.PESOS_INICIAIS)
        self.pesos_regime = {
            'tendencia_alta': dict(self.PESOS_INICIAIS),
            'tendencia_baixa': dict(self.PESOS_INICIAIS),
            'lateral': dict(self.PESOS_INICIAIS),
            'vol_alta': dict(self.PESOS_INICIAIS),
            'vol_baixa': dict(self.PESOS_INICIAIS),
            'tendencia_alta_vol_alta': dict(self.PESOS_INICIAIS),
            'tendencia_alta_vol_baixa': dict(self.PESOS_INICIAIS),
            'tendencia_baixa_vol_alta': dict(self.PESOS_INICIAIS),
            'tendencia_baixa_vol_baixa': dict(self.PESOS_INICIAIS),
            'lateral_vol_alta': dict(self.PESOS_INICIAIS),
            'lateral_vol_baixa': dict(self.PESOS_INICIAIS),
        }
        self.acuracia = {}
        self.feature_hits = defaultdict(lambda: {'acertos': 0, 'erros': 0})
        self.confianca_ewma = 0.0
        self.sinal_confirmado = 0
        self._score_confirmado = 0.0  # v9.27: magnitude do sinal confirmado (para reversao)
        self._sinal_streak = 0  # v9.27: segmentos consecutivos com mesmo sinal
        self._sinal_anterior_bruto = 0  # v9.27: ultimo sinal bruto
        self._lado_anterior = 0
        self.sinal_contador = 0
        self._ultimo_sinal_ts = time.time()
        self.confirmacao_necessaria = CONFIG["confirmacao_necessaria"]
        # v9.7: normalização z-score das contribuições do score (opt-in via config)
        self._normalizar_score = bool(CONFIG.get('normalizar_score', False))
        self._zscore_trackers = {}
        self._regime_cache = {}
        # v9.8: CVD + volatilidade + sessão
        self._cvd_extremos = {}
        self._ewma_ret2 = {}
        self._ultimo_preco_fim = {}
        
        self.trackers = defaultdict(lambda: {
            'aggr': PercentilTracker(CONFIG["janela_percentil_segs"], CONFIG["amostra_minima_percentil"]),
            'eff': PercentilTracker(CONFIG["janela_percentil_segs"], CONFIG["amostra_minima_percentil"]),
            'acel': PercentilTracker(CONFIG["janela_percentil_segs"], CONFIG["amostra_minima_percentil"]),
            'book_imb': PercentilTracker(CONFIG["janela_percentil_segs"], CONFIG["amostra_minima_percentil"]),
            'range': RangeTracker(),
            'acumulacao': AccumulationTracker(),
            'ofi': OFITracker(niveis=5),
            'book_level': BookLevelFeatures(),
        })
        self.cross_engine = CrossAssetEngine()
        
        self.dia_atual = date.today()
        self.trades_dia = 0
        self.perdas_consecutivas = 0
        self.pnl_dia = 0.0
        self.circuit_breaker_nivel = 0
        self._cb_ultimo_reset = time.time()
        
        self.book_snap_ant = {}
        self.book_events = defaultdict(OrderedDict)
        self.book_stats = {}
        self.scorer = None  # ML scorer (setado pelo App)
        self._book_persist = defaultdict(lambda: defaultdict(lambda: {
            'bid_seguidos': 0, 'ask_seguidos': 0,
            'bid_vol_ant': 0, 'ask_vol_ant': 0
        }))
        
        # v9.36: OHLC intraday por ativo (abertura, maxima, minima, fechamento)
        self.ohlc = defaultdict(lambda: {
            'abertura': None, 'maxima': None, 'minima': None, 'fechamento': None
        })
        self.session_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.base_dir = SAVE_DIR
        self._fp = None
        self._fp_dec = None
        self._io_lock = threading.Lock()
        self._buf_trades = []
        self._buf_decisoes = []
        # v9.9: rotação real por tamanho (100MB) + fsync periódico
        self._parte = {'_fp': 1, '_fp_dec': 1}
        self._fsync_counter_trades = 0
        self._fsync_counter_decisoes = 0
        self._fsync_a_cada = 20
        self.max_bytes_trade_file = 100 * 1024 * 1024
        
        self._posicao_checkpoint_path = Path(self.base_dir) / 'posicao_atual.json'
        self._carregar_posicao_checkpoint()
        self.carregar_aprendizado(self.base_dir)
        
        # v7: padrões
        self.padroes = PadroesMemoria(self.base_dir)
        # v9: sanity check de preco
        self._ultimo_preco_valido = {}
        self._anomalias_preco = defaultdict(int)
        
        # Boost de OFI em regimes
        self.pesos_regime['tendencia_alta']['ofi'] = 0.6
        self.pesos_regime['tendencia_alta']['ofi_ewma'] = 0.4
        self.pesos_regime['tendencia_baixa']['ofi'] = 0.6
        self.pesos_regime['tendencia_baixa']['ofi_ewma'] = 0.4
        self.pesos_regime['vol_alta']['ofi'] = 0.5
        self.pesos_regime['vol_alta']['ofi_ewma'] = 0.35
        self.pesos_regime['lateral']['ofi'] = 0.15
        self.pesos_regime['lateral']['ofi_ewma'] = 0.1
        
        # Camada 1-4 por regime
        for reg in self.pesos_regime:
            self.pesos_regime[reg]['book_level_spread'] = self.pesos['book_level_spread']
            self.pesos_regime[reg]['book_imb_l1'] = self.pesos['book_imb_l1']
            self.pesos_regime[reg]['book_imb_l10'] = self.pesos['book_imb_l10']
            self.pesos_regime[reg]['book_microprice'] = self.pesos['book_microprice']
            self.pesos_regime[reg]['book_hhi'] = self.pesos['book_hhi']
            self.pesos_regime[reg]['trade_metrics'] = self.pesos['trade_metrics']
            self.pesos_regime[reg]['cross_lag'] = self.pesos['cross_lag']
            self.pesos_regime[reg]['micro_drift'] = self.pesos['micro_drift']
            self.pesos_regime[reg]['imb_ponderado'] = self.pesos['imb_ponderado']
            self.pesos_regime[reg]['slope_book'] = self.pesos['slope_book']
        self.pesos_regime['tendencia_alta']['cross_lag'] = 0.5
        self.pesos_regime['tendencia_baixa']['cross_lag'] = 0.5
        self.pesos_regime['vol_alta']['book_imb_l1'] = 0.3
        self.pesos_regime['vol_alta']['book_imb_l10'] = 0.4
        self.pesos_regime['lateral']['book_imb_l1'] = 0.10
        self.pesos_regime['lateral']['book_imb_l10'] = 0.15


    def _carregar_posicao_checkpoint(self):
        if self._posicao_checkpoint_path.exists():
            try:
                data = json.loads(self._posicao_checkpoint_path.read_text(encoding='utf-8'))
                # C9: nao restaurar posicao de mais de 12 horas (stale checkpoint)
                aberta_em = data.get('aberta_em', 0)
                if aberta_em and (time.time() - aberta_em) > 12 * 3600:
                    log.info('[POS] Checkpoint stale (>12h) — ignorando posicao fantasma')
                    self._posicao_checkpoint_path.unlink(missing_ok=True)
                    return
                if data.get('aberta'):
                    self.posicao = {
                        'ativo': data['ativo'], 'lado': data['lado'],
                        'entrada': data['entrada'], 'preco_medio': data['entrada'],
                        'stop_preco': data.get('stop_preco'), 'tp': data.get('tp'),
                        'aberta_em': data.get('aberta_em', time.time()),
                        'motivos': data.get('motivos', []), 'contrib': data.get('contrib', []),
                        'prev_idx': len(self.previsoes),
                        'mfe': data.get('mfe', 0.0), 'mae': data.get('mae', 0.0),
                        'breakeven_ativado': data.get('breakeven_ativado', False),
                        'quantidade': data.get('quantidade', 1)
                    }
                    log.warning("Posição recuperada do checkpoint")
            except Exception as e:
                log.warning(f"[POS] falha ao carregar checkpoint: {e}")

    def _salvar_posicao_checkpoint(self):
        try:
            if self.posicao is None:
                if self._posicao_checkpoint_path.exists():
                    self._posicao_checkpoint_path.unlink()
            else:
                pos = self.posicao
                data = {
                    'aberta': True, 'ativo': pos['ativo'], 'lado': pos['lado'],
                    'entrada': pos['entrada'], 'stop_preco': pos.get('stop_preco'),
                    'tp': pos.get('tp'), 'aberta_em': pos.get('aberta_em'),
                    'motivos': pos.get('motivos', []), 'contrib': pos.get('contrib', []),
                    'mfe': pos.get('mfe', 0.0), 'mae': pos.get('mae', 0.0),
                    'breakeven_ativado': pos.get('breakeven_ativado', False),
                    'quantidade': pos.get('quantidade', 1)
                }
                self._posicao_checkpoint_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log.warning(f"[POS] falha ao salvar checkpoint: {e}")

    def _garantir_fp(self):
        with self._io_lock:
            if self._fp is None:
                try:
                    out = Path(self.base_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    self._fp = open(out / f'negocios_{self.session_ts}.jsonl', 'a', encoding='utf-8')
                except Exception as e:
                    log.warning(f"[IO] falha ao abrir negocios jsonl: {e}")
            if self._fp_dec is None:
                try:
                    out = Path(self.base_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    self._fp_dec = open(out / f'decisoes_{self.session_ts}.jsonl', 'a', encoding='utf-8')
                except Exception as e:
                    log.warning(f"[IO] falha ao abrir decisoes jsonl: {e}")
            return self._fp, self._fp_dec

    def _gravar_trade(self, neg):
        self._garantir_fp()  # v9.9: fix — nunca era chamado (arquivo nunca aberto)
        self._buf_trades.append(json.dumps(neg, ensure_ascii=False))
        if len(self._buf_trades) >= 200:
            self._flush_trades()

    def _rotacionar(self, attr, prefix):
        """Fecha o arquivo atual e abre a próxima parte (rotação por
        tamanho). O arquivo original mantém o nome sem sufixo; as
        partes seguem com _pN."""
        fp = getattr(self, attr)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass
        self._parte[attr] += 1
        out = Path(self.base_dir)
        nome = out / f'{prefix}_{self.session_ts}_p{self._parte[attr]:02d}.jsonl'
        setattr(self, attr, open(nome, 'a', encoding='utf-8'))

    def _flush_trades(self):  # Rotates at 100MB per file (v9.9: implementado)
        with self._io_lock:
            if self._buf_trades and self._fp is not None:
                try:
                    # v9.9: rotação por tamanho (o comentário antigo prometia,
                    # mas não existia nenhuma lógica)
                    if self._fp.tell() >= self.max_bytes_trade_file:
                        self._rotacionar('_fp', 'negocios')
                    self._fp.write('\n'.join(self._buf_trades) + '\n')
                    self._fp.flush()
                    # v9.9: fsync periódico (durabilidade sem pagar o custo
                    # de fsync a cada 200 trades)
                    self._fsync_counter_trades += 1
                    if self._fsync_counter_trades >= self._fsync_a_cada:
                        os.fsync(self._fp.fileno())
                        self._fsync_counter_trades = 0
                except Exception as e:
                    log.warning(f"[IO] falha ao gravar trades: {e}")
                self._buf_trades.clear()

    def _gravar_decisao(self, dec):
        self._garantir_fp()  # v9.9: fix — mesma causa do _gravar_trade
        self._buf_decisoes.append(json.dumps(dec, ensure_ascii=False, default=str))
        if len(self._buf_decisoes) >= 50:
            self._flush_decisoes()

    def _flush_decisoes(self):
        with self._io_lock:
            if self._buf_decisoes and self._fp_dec is not None:
                try:
                    # v9.9: rotação por tamanho
                    if self._fp_dec.tell() >= self.max_bytes_trade_file:
                        self._rotacionar('_fp_dec', 'decisoes')
                    self._fp_dec.write('\n'.join(self._buf_decisoes) + '\n')
                    self._fp_dec.flush()
                    self._fsync_counter_decisoes += 1
                    if self._fsync_counter_decisoes >= self._fsync_a_cada:
                        os.fsync(self._fp_dec.fileno())
                        self._fsync_counter_decisoes = 0
                except Exception as e:
                    log.warning(f"[IO] falha ao gravar decisoes: {e}")
                self._buf_decisoes.clear()

    def carregar_aprendizado(self, base_dir):
        p = Path(base_dir) / 'learning_state.json'
        if not p.exists():
            return
        try:
            st = json.loads(p.read_text(encoding='utf-8'))
            for k, v in st.get('pesos', {}).items():
                if k in self.pesos and isinstance(v, (int, float)):
                    self.pesos[k] = max(-1.0, min(1.0, float(v)))
            for k, v in st.get('feature_hits', {}).items():
                self.feature_hits[k] = {'acertos': int(v.get('acertos', 0)), 'erros': int(v.get('erros', 0))}
            self.resultados = list(st.get('resultados', []))[-500:]
            self.previsoes = list(st.get('previsoes', []))[-500:]
            self._recalc_acuracia()
            log.info(f"[LEARN] estado carregado: {len(self.resultados)} resultados")
        except Exception as e:
            log.warning(f"[LEARN] falha ao carregar learning_state.json: {e}")

    def salvar_aprendizado(self, base_dir):
        try:
            out = Path(base_dir)
            out.mkdir(parents=True, exist_ok=True)
            st = {
                'pesos': dict(self.pesos),
                'feature_hits': {k: dict(v) for k, v in self.feature_hits.items()},
                'acuracia': dict(self.acuracia),
                'resultados': self.resultados[-500:],
                'previsoes': self.previsoes[-500:],
                'salvo_em': datetime.now().isoformat(timespec='seconds')
            }
            (out / 'learning_state.json').write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception as e:
            log.warning(f"[LEARN] falha ao salvar learning_state.json: {e}")

    def alimentar_lote(self, lista_negocios, modo_replay=False):
        with self.lock:
            for neg in lista_negocios:
                sym, tms, preco, qtd, agr, comp, vend = neg
                # v9: sanity check de preco
                if not self._preco_plausivel(sym, preco):
                    continue
                seg = tms // 1000
                
                # Reset diário
                hoje = date.today()
                if hoje != self.dia_atual:
                    self.dia_atual = hoje
                    self.trades_dia = 0
                    self.perdas_consecutivas = 0
                    self.pnl_dia = 0.0
                    self.circuit_breaker_nivel = 0
                    self._cb_ultimo_reset = time.time()
                    self._ultimo_preco_valido.clear()
                    # v9.13: zera também preço/vol de referência da virada do dia
                    # (senão o primeiro retorno do dia mistura o fechamento de ontem
                    # e o realized_vol espúrio polui a abertura)
                    self._ultimo_preco_fim.clear()
                    self._ewma_ret2.clear()
                    self._cvd_extremos.clear()
                    # Rollover de dia: relógio T&T reinicia em 0 — zera segmento e buffer
                    self.seg_atual = 0
                    self.buffer.clear()
                    # v9.36: reset OHLC para o novo dia
                    self.ohlc.clear()
                
                # Mudança de segundo
                if not modo_replay:
                    if self.seg_atual > 0 and seg != self.seg_atual:
                        self._calcular(self.seg_atual)
                if seg > self.seg_atual:
                    self.seg_atual = seg
                    self.buffer.clear()
                
                # v9.36: atualizar OHLC intraday
                _ohlc = self.ohlc[sym]
                if _ohlc['abertura'] is None:
                    _ohlc['abertura'] = preco
                if _ohlc['maxima'] is None or preco > _ohlc['maxima']:
                    _ohlc['maxima'] = preco
                if _ohlc['minima'] is None or preco < _ohlc['minima']:
                    _ohlc['minima'] = preco
                _ohlc['fechamento'] = preco

                self.buffer[sym].append({'preco': preco, 'qtd': qtd, 'agressor': agr,
                                          'compradora': comp, 'vendedora': vend})
                
                # Alimentar AccumulationTracker
                tr = self.trackers[sym]
                ts_sec = tms // 1000
                broker_agr = comp if agr == 'Comprador' else vend
                if broker_agr and broker_agr not in ('None', ''):
                    tr['acumulacao'].registrar(ts_sec, broker_agr, agr, preco, qtd)
                
                st = self.stats[sym]
                st['n'] += 1
                if agr == 'Comprador':
                    st['vc'] += qtd
                elif agr == 'Vendedor':
                    st['vv'] += qtd
                if not st['p0']:
                    st['p0'] = preco
                st['p1'] = preco
                
                # Cross-asset: alimenta engine de liderança WDO→WIN (ts ms nativo)
                self.cross_engine.registrar(
                    sym, tms, preco,
                    1.0 if agr == 'Comprador' else (-1.0 if agr == 'Vendedor' else 0.0))
                
                if comp and comp not in ('None', ''):
                    sd = self.agressao_por_corretora[sym].setdefault(comp, {'c': 0, 'v': 0})
                    if agr == 'Comprador':
                        sd['c'] += qtd
                    else:
                        sd['v'] += qtd
                
                if vend and vend not in ('None', ''):
                    sd = self.agressao_por_corretora[sym].setdefault(vend, {'c': 0, 'v': 0})
                    if agr == 'Vendedor':
                        sd['v'] += qtd
                    else:
                        sd['c'] += qtd
                
                # Em modo replay, pula gravação em disco e append na memória
                if not modo_replay:
                    neg_reg = {'time_ms': tms, 'ativo': sym, 'preco': preco, 'qtd': qtd,
                               'agressor': agr, 'compradora': comp, 'vendedora': vend}
                    self.todos_negocios.append(neg_reg)
                    self._gravar_trade(neg_reg)
            
            if not modo_replay:
                self._flush_trades()

    def _calcular(self, seg, skip_avaliar=False):
        for ativo, negs in list(self.buffer.items()):
            if not negs:
                continue
            vc = sum(n['qtd'] for n in negs if n['agressor'] == 'Comprador')
            vv = sum(n['qtd'] for n in negs if n['agressor'] == 'Vendedor')
            vt = vc + vv
            n = len(negs)
            aggr = (vc - vv) / vt if vt > 0 else 0.0
            precos = [x['preco'] for x in negs if x['preco'] > 0]
            dp = (precos[-1] - precos[0]) if len(precos) >= 2 else 0.0
            eff = abs(dp) / vt if vt > 0 else 0.0
            fp = (sum(1 for i in range(1, n) if negs[i]['agressor'] == negs[i - 1]['agressor'])
                  / max(n - 1, 1))
            vc2 = defaultdict(int)
            for nc in negs:
                for lado in ('compradora', 'vendedora'):
                    c = nc[lado]
                    if c and c not in ('None', ''):
                        vc2[c] += nc['qtd']
            tc = sum(vc2.values())
            shares = [v / tc for v in vc2.values()] if tc > 0 else []
            hhi = sum(s * s for s in shares) if shares else 0.0
            
            f = {
                'time_ms': seg * 1000, 'ativo': ativo, 'n': n, 'vol_total': vt,
                'vol_compr': vc, 'vol_vend': vv, 'aggr_imb': aggr,
                'preco_ini': precos[0] if precos else 0, 'preco_fim': precos[-1] if precos else 0,
                'delta_preco': dp, 'price_eff': eff, 'fluxo_persist': fp, 'hhi': hhi,
                'top_corretoras': sorted(vc2.items(), key=lambda x: -x[1])[:6],
                # Camada 3: Trade metrics
                'avg_trade_size': round(vt / n, 1) if n > 0 else 0,
                'max_trade_size': max((x['qtd'] for x in negs), default=0),
                'trades_per_sec': n,
                'seq_pattern': self._calcular_sequencia(negs),
            }
            
            # Corretoras 10s (daily totals - simpler and more robust)
            cf = {}
            for corp, sd in self.agressao_por_corretora.get(ativo, {}).items():
                c = sd.get('c', 0)
                v = sd.get('v', 0)
                net = c - v
                if c + v > 10:
                    cf[corp] = {'net_10s': net, 'lado': 'C' if net > 0 else 'V' if net < 0 else '='}
            f['corretoras'] = cf
            
            # Aceleração
            hist_ant = list(self.historico.get(ativo, ()))
            if len(hist_ant) >= 3:
                rec = [h['aggr_imb'] for h in hist_ant[-3:]]
                ant3 = [h['aggr_imb'] for h in hist_ant[-6:-3]] if len(hist_ant) >= 6 else [h['aggr_imb'] for h in hist_ant[:3]]
                f['aceleracao'] = (sum(rec) / len(rec)) - (sum(ant3) / len(ant3))
            else:
                f['aceleracao'] = 0.0

            # ===== v9.8: CVD + divergência + vol + sessão =====
            # CVD (delta acumulado da sessão)
            st2 = self.stats.get(ativo)
            cvd = (st2['vc'] - st2['vv']) if st2 else 0
            f['cvd_total'] = cvd
            # Divergência CVD×preço (extrem — exaustão de fluxo)
            ex = self._cvd_extremos.setdefault(ativo, {'topo_p': 0.0, 'topo_cvd': None,
                                                       'fundo_p': None, 'fundo_cvd': None, 'div': 0})
            preco_f = f['preco_fim']
            if preco_f > 0:
                # v9.13: divergência compara contra o HIGH-WATER de CVD (cvd_max),
                # não contra o CVD do último topo — topo novo com CVD abaixo do
                # máximo histórico é divergência (exaustão), mesmo em sequência.
                if ex['topo_p'] == 0.0 or preco_f > ex['topo_p']:
                    cvd_ant = ex['topo_cvd']
                    ex['topo_p'] = preco_f
                    ex['topo_cvd'] = max(cvd, ex.get('cvd_max', cvd))
                    ex['cvd_max'] = max(ex.get('cvd_max', cvd), cvd)
                    if cvd_ant is not None:
                        ex['div'] = -1 if cvd < ex['cvd_max'] else 0
                if ex['fundo_p'] is None or preco_f < ex['fundo_p']:
                    cvd_ant = ex['fundo_cvd']
                    ex['fundo_p'] = preco_f
                    ex['fundo_cvd'] = min(cvd, ex.get('cvd_min', cvd)) if ex.get('cvd_min') is not None else cvd
                    ex['cvd_min'] = min(ex.get('cvd_min', cvd), cvd) if ex.get('cvd_min') is not None else cvd
                    if cvd_ant is not None:
                        ex['div'] = 1 if cvd > ex['cvd_min'] else 0
            f['cvd_div'] = ex['div']
            # Volatilidade realizada (EWMA de ret², em bps)
            prev_p = self._ultimo_preco_fim.get(ativo, 0.0)
            if prev_p > 0 and preco_f > 0:
                ret = preco_f / prev_p - 1.0
                self._ewma_ret2[ativo] = 0.9 * self._ewma_ret2.get(ativo, 0.0) + 0.1 * ret * ret
            self._ultimo_preco_fim[ativo] = preco_f
            f['realized_vol_bps'] = round(math.sqrt(self._ewma_ret2.get(ativo, 0.0)) * 10000, 2)
            # Range vol (high-low últimos 60s, em bps)
            hp = [h['preco_fim'] for h in hist_ant[-60:] if h.get('preco_fim', 0) > 0]
            if len(hp) >= 2:
                mid_p = (max(hp) + min(hp)) / 2
                f['range_vol_bps'] = round((max(hp) - min(hp)) / mid_p * 10000, 2) if mid_p > 0 else 0.0
            else:
                f['range_vol_bps'] = 0.0
            # Fase de sessão + dias até vencimento
            f['fase_sessao'] = fase_sessao(seg * 1000,
                abertura_fim=CONFIG.get('horario_abertura_fim', (10, 0)),
                almoco_inicio=CONFIG.get('horario_almoco_inicio', (12, 0)),
                almoco_fim=CONFIG.get('horario_almoco_fim', (13, 30)),
                fechamento=CONFIG.get('horario_fechamento', (16, 30)))
            f['dias_ate_venc'] = dias_ate_vencimento(ativo) or 0
            
            # ABSORÇÃO: volume executado / deslocamento do preço
            # Alta absorção = muito volume, pouco deslocamento = alguém está absorvendo
            if vt > 10 and abs(dp) > 0:
                f['absorcao_ratio'] = vt / abs(dp)
            elif vt > 10:
                f['absorcao_ratio'] = vt * 10  # volume alto, zero deslocamento = absorção máxima
            else:
                f['absorcao_ratio'] = 0
            
            # OFI
            ofi_tracker = self.trackers[ativo]['ofi']
            ofi_d = ofi_tracker.get_ofi()
            f['ofi_total'] = ofi_d['ofi_total']
            f['ofi_ewma'] = ofi_d['ofi_ewma']
            
            self.features[ativo] = f
            with self.lock:
                self.features_por_seg[(ativo, seg)] = f
                while len(self.features_por_seg) > CONFIG["features_seg_max"]:
                    self.features_por_seg.popitem(last=False)
            self.historico[ativo].append(f)
            
            # v7: padrões - extremos + stop-hunt
            bs_curr = self.book_stats.get(ativo, {})
            f['_book_level'] = bs_curr.get('book_level')
            self.padroes.registrar_extremo(ativo, f['preco_fim'], time.time())
            hunt = self.padroes.detectar_stop_hunt(ativo, f['preco_fim'], f['aggr_imb'], time.time(), hist_ant)
            if hunt:
                f['stop_hunt'] = hunt
            
            if not skip_avaliar:
                self._avaliar(ativo, f)
            
            # Alimenta trackers
            tr = self.trackers[ativo]
            ts_now = time.time()
            tr['aggr'].add(abs(f['aggr_imb']), ts_now)
            tr['eff'].add(f['price_eff'], ts_now)
            tr['acel'].add(abs(f['aceleracao']), ts_now)
            bs = self.book_stats.get(ativo)
            if bs:
                tr['book_imb'].add(abs(bs['imb']), ts_now)
            # Range
            if f['preco_fim'] > 0:
                tr['range'].atualizar(f['preco_fim'], ts_now)
            
            # agressao_por_corretora agora usa totais diarios (c/v), sem cleanup por segmento

    def _calcular_sequencia(self, negs):
        if len(negs) < 3:
            return 0.0
        seq = []
        for n in negs[-10:]:
            a = n['agressor']
            if a == 'Comprador':
                seq.append(1)
            elif a == 'Vendedor':
                seq.append(-1)
            # agressor 'neutro' ou qualquer outro valor: pula (não enviesa)
        if not seq:
            return 0.0
        last = seq[-1]
        streak = 0
        for s in reversed(seq):
            if s == last:
                streak += 1
            else:
                break
        return last * (streak / len(seq))
    
    def _avaliar(self, ativo, f):
        aggr = f['aggr_imb']
        eff = f['price_eff']
        dp = f['delta_preco']
        vol = f['vol_total']
        preco = f['preco_fim']
        acel = f.get('aceleracao', 0.0)
        hist = list(self.historico.get(ativo, []))
        segs = self.agressao_por_corretora.get(ativo, {})
        
        tr = self.trackers[ativo]
        limiar_aggr = tr['aggr'].percentil(CONFIG["percentil_aggr"], CONFIG["fallback_aggr_min"])
        limiar_eff = tr['eff'].percentil(CONFIG["percentil_eficiencia"], CONFIG["fallback_eficiencia_min"])
        limiar_acel = tr['acel'].percentil(CONFIG["percentil_aceleracao"], CONFIG["fallback_aceleracao_min"])
        limiar_book = tr['book_imb'].percentil(CONFIG["percentil_book_imb"], CONFIG["fallback_book_imb_min"])
        
        score = 0.0
        motivos = []
        contrib = []
        
        # Regime detectado ANTES do score — os pesos por regime são efetivos
        regime_info = self.detectar_regime(ativo)
        f['regime'] = regime_info.get('regime', 'lateral') if isinstance(regime_info, dict) else 'lateral'

        
        def add(key, mult, texto):
            nonlocal score
            regime = f.get('regime', 'lateral')
            peso_base = self.pesos_regime.get(regime, self.pesos).get(key, self.pesos.get(key, 0.0))
            if self._normalizar_score:
                # z-score da contribuição pela volatilidade recente da feature
                # (estacionaridade): z antes, atualiza depois (sem auto-influência)
                zt = self._zscore_trackers.get(key)
                if zt is None:
                    zt = EWMAZScore()
                    self._zscore_trackers[key] = zt
                mult = zt.z(mult)
                zt.atualizar(mult)
            score += peso_base * mult
            contrib.append((key, mult))
            motivos.append(texto)

        # v9.8: divergência CVD×preço — extremo sem confirmação do delta
        cvd_d = f.get('cvd_div', 0)
        if cvd_d == -1:
            add('cvd_div', -0.6, 'divergencia bearish (topo sem delta)')
        elif cvd_d == 1:
            add('cvd_div', 0.6, 'divergencia bullish (fundo com delta)')
        
        lado = 1 if aggr > 0 else -1
        sinal = 0
        
        # CORRELAÇÃO T&T + BOOK por corretora
        # Quem está agredindo E tem ordens pendentes = likely continuation
        bs = self.book_stats.get(ativo, {})
        if segs and bs:
            # Agregados do book por corretora
            book_brokers = {}
            for b, s in self._book_persist[ativo].items():
                if s.get('bid_vol_ant', 0) > 5:
                    book_brokers.setdefault(b, {'bid': 0, 'ask': 0})
                    book_brokers[b]['bid'] = s.get('bid_vol_ant', 0)
                if s.get('ask_vol_ant', 0) > 5:
                    book_brokers.setdefault(b, {'bid': 0, 'ask': 0})
                    book_brokers[b]['ask'] = s.get('ask_vol_ant', 0)
            
            for corp, sd in segs.items():
                c = sd.get('c', 0)
                v = sd.get('v', 0)
                net = c - v
                if abs(net) < 10 and c + v < 30:
                    continue
                tipo = classificar_corretora(corp)
                bk = book_brokers.get(corp, {})
                bid_vol = bk.get('bid', 0)
                ask_vol = bk.get('ask', 0)
                
                # Comprador agredindo + tem bid no book = acumulando
                if net > 20 and bid_vol > 10:
                    mult = min(1.0, net / 100) * min(1.0, bid_vol / 50)
                    add('corretora_tt_book', mult, f"{corp} agredindo+bid ({net:+.0f}/{bid_vol})")
                # Vendedor agredindo + tem ask no book = distribuindo
                elif net < -20 and ask_vol > 10:
                    mult = min(1.0, abs(net) / 100) * min(1.0, ask_vol / 50)
                    add('corretora_tt_book', -mult, f"{corp} agredindo+ask ({net:+.0f}/{ask_vol})")
                # Comprador agredindo mas SEM bid = exaustao
                elif net > 30 and bid_vol == 0:
                    add('corretora_tt_book', -0.3, f"{corp} sem bid ({net:+.0f})")
                # Vendedor agredindo mas SEM ask = exaustao
                elif net < -30 and ask_vol == 0:
                    add('corretora_tt_book', 0.3, f"{corp} sem ask ({net:+.0f})")
        
        # Features do v2 restauradas
        if abs(aggr) >= limiar_aggr:
            preco_andando = (dp > 0 and lado > 0) or (dp < 0 and lado < 0)
            if preco_andando and abs(dp) > 5:
                add('preco_andando', 0.3, f"preco +{dp:.0f}")
            elif preco_andando:
                add('preco_andando', 0.15, f"preco inicia ({dp:.0f})")
            elif not preco_andando and abs(dp) > 2:
                add('preco_andando', -0.3, f"preco CONTRA ({dp:.0f})")
        
        if vol > 10:
            if eff > limiar_eff:
                add('eficiencia', 1.0, f"eff {eff:.4f}")
            elif eff < CONFIG["fallback_absorcao_eficiencia_max"] and abs(aggr) > 0.3:
                add('eficiencia', -0.8, f"absorcao eff={eff:.4f}")
        
        if abs(acel) > limiar_acel and (acel > 0) == (aggr > 0):
            add('aceleracao', 1.0, f"acelera {acel:+.2f}")
        elif abs(acel) > limiar_acel and (acel > 0) != (aggr > 0):
            add('aceleracao', -0.75, f"desacelera {acel:+.2f}")
        
        # Persistência
        seguidos = 0
        for h in reversed(hist[-5:]):
            if (h['aggr_imb'] > 0) == (aggr > 0):
                seguidos += 1
            else:
                break
        if seguidos >= 4:
            add('persistencia', 1.0, f"{seguidos}s seguidos")
        elif seguidos >= 3:
            add('persistencia', 0.667, f"{seguidos}s seguidos")
        
        # Corretoras
        if segs:
            acum = {}
            ai = av = 0
            for corp, sd in segs.items():
                c = sd.get('c', 0)
                v = sd.get('v', 0)
                total = c - v
                if abs(total) > 20:
                    acum[corp] = total
                if classificar_corretora(corp) == 'inst':
                    ai += total
                else:
                    av += total
            if acum:
                lid = max(acum, key=lambda x: abs(acum[x]))
                ll = 'C' if acum[lid] > 0 else 'V'
                tl = classificar_corretora(lid)
                if tl == 'inst' and (ll == 'C') == (aggr > 0):
                    add('inst_lidera', 1.0, f"{lid} (inst)")
                elif tl == 'varejo' and (ll == 'C') != (aggr > 0):
                    add('varejo_contra', 1.0, f"{lid} (varejo) contra")
            if ai != 0 and av != 0:
                if (ai > 0) == (av > 0):
                    add('inst_lidera', 0.5, "inst+varejo ok")
                elif (ai > 0) == (aggr > 0):
                    add('inst_lidera', 0.5, "inst confirma")
        
        # BOOK (v2 restaurado)
        bs = self.book_stats.get(ativo)
        if bs:
            book_imb = bs['imb']
            if aggr > 0.2 and book_imb > limiar_book:
                add('book_imb', 1.0, f"book +{book_imb:.2f}")
            elif aggr < -0.2 and book_imb < -limiar_book:
                add('book_imb', 1.0, f"book {book_imb:.2f}")
            elif aggr > 0.2 and book_imb < -limiar_book:
                add('book_imb', -1.0, f"book CONTRA {book_imb:.2f}")
            elif aggr < -0.2 and book_imb > limiar_book:
                add('book_imb', -1.0, f"book CONTRA +{book_imb:.2f}")
            
            # Defesa persistente
            for d in bs.get('defesa_persistente', []):
                if aggr > 0.2 and d['lado'] == 'bid':
                    mult = min(1.67, 0.67 + d['seguidos'] * 0.33)
                    add('defesa', mult, f"{d['broker']} defende bid {d['seguidos']}s")
                elif aggr < -0.2 and d['lado'] == 'ask':
                    mult = min(1.67, 0.67 + d['seguidos'] * 0.33)
                    add('defesa', mult, f"{d['broker']} defende ask {d['seguidos']}s")
            
            # Absorção
            for a in bs.get('absorvedores', []):
                if abs(aggr) > 0.3 and a.get('seguidos', 0) >= 2:
                    add('absorcao_book', 1.0, f"{a['broker']} absorve {a['seguidos']}s")
                    break
            
            # Retirada/reposição/thinning/layering
            if aggr > 0.2 and bs.get('retiradas_bid', 0) > 2:
                add('retirada', 1.0, f"{bs['retiradas_bid']} bid saiu")
            elif aggr < -0.2 and bs.get('retiradas_ask', 0) > 2:
                add('retirada', 1.0, f"{bs['retiradas_ask']} ask saiu")
            
            if aggr > 0.2 and bs.get('reposicoes_bid', 0) > 2:
                add('reposicao', 1.0, f"{bs['reposicoes_bid']} bid repos")
            elif aggr < -0.2 and bs.get('reposicoes_ask', 0) > 2:
                add('reposicao', 1.0, f"{bs['reposicoes_ask']} ask repos")
            
            if aggr > 0.2 and bs.get('thinning_bid', 0) > 100:
                add('thinning', 1.0, f"thinning bid -{bs['thinning_bid']:.0f}")
            elif aggr < -0.2 and bs.get('thinning_ask', 0) > 100:
                add('thinning', 1.0, f"thinning ask -{bs['thinning_ask']:.0f}")
            
            layer = bs.get('layering', [])
            if len(layer) > 2:
                add('layering', 1.0, f"{len(layer)} layerings")
            
            if aggr > 0.2 and bs.get('delta_bid', 0) > 50:
                add('delta_book', 1.0, f"bid +{bs['delta_bid']:.0f}")
            elif aggr < -0.2 and bs.get('delta_ask', 0) > 50:
                add('delta_book', 1.0, f"ask +{bs['delta_ask']:.0f}")
        
        # === CAMADA 1: Book Level Features ===
        bl = bs.get('book_level') if bs else None
        if bl:
            if bl['spread'] < 5:
                add('book_level_spread', 0.5, 'spread ' + str(round(bl['spread'])) + ' tight')
            elif bl['spread'] > 20:
                add('book_level_spread', -0.5, 'spread ' + str(round(bl['spread'])) + ' wide')
            
            imb_l1 = bl['imbalance'].get('L1', 0)
            imb_l10 = bl['imbalance'].get('L10', 0)
            # Imbalance L1 (nível mais próximo)
            if aggr > 0.2 and imb_l1 > 0.2:
                add('book_imb_l1', 0.3, 'imb_L1 +' + str(round(imb_l1, 2)))
            elif aggr < -0.2 and imb_l1 < -0.2:
                add('book_imb_l1', 0.3, 'imb_L1 ' + str(round(imb_l1, 2)))
            elif aggr > 0.2 and imb_l1 < -0.3:
                add('book_imb_l1', -0.3, 'imb_L1 CONTRA ' + str(round(imb_l1, 2)))
            elif aggr < -0.2 and imb_l1 > 0.3:
                add('book_imb_l1', -0.3, 'imb_L1 CONTRA +' + str(round(imb_l1, 2)))
            
            # Imbalance L10 (profundidade)
            if abs(imb_l10) > 0.15 and (imb_l10 > 0) == (aggr > 0):
                add('book_imb_l10', 0.4, 'imb_L10 confirma ' + str(round(imb_l10, 2)))
            
            # Microprice vs mid
            micro_diff = bl['microprice'] - bl['mid']
            if abs(micro_diff) > 2:
                if (micro_diff > 0) == (aggr > 0):
                    add('book_microprice', 0.3, 'microprice +' + str(round(micro_diff, 1)))
                else:
                    add('book_microprice', -0.3, 'microprice CONTRA ' + str(round(micro_diff, 1)))
            
            # HHI (concentração do book)
            if bl['hhi_book'] > 0.15:
                add('book_hhi', 0.2, 'HHI ' + str(round(bl['hhi_book'], 3)))
            
            # === NOVAS FEATURES (Passo 3) ===
            # Microprice drift EWMA (pressao compradora/vendedora suavizada)
            md = bl.get('micro_drift_ewma', 0)
            if abs(md) > 0.5:  # mais de 0.5 bps de desvio
                if (md > 0) == (aggr > 0):
                    add('micro_drift', min(1.0, abs(md) / 3.0), 
                        'micro_drift +' + str(round(md, 2)) + 'bps')
                else:
                    add('micro_drift', -min(0.7, abs(md) / 3.0),
                        'micro_drift CONTRA ' + str(round(md, 2)) + 'bps')
            
            # Imbalance ponderado (mais peso nos niveis proximos)
            imb_p = bl.get('imb_ponderado', 0)
            if abs(imb_p) > 0.1:
                if (imb_p > 0) == (aggr > 0):
                    add('imb_ponderado', min(1.0, abs(imb_p) * 3.0),
                        'imb_pond +' + str(round(imb_p, 3)))
                else:
                    add('imb_ponderado', -min(0.7, abs(imb_p) * 3.0),
                        'imb_pond CONTRA ' + str(round(imb_p, 3)))
            
            # Slope do book (geometria da liquidez)
            slope_b = bl.get('slope_bid', 0)
            slope_a = bl.get('slope_ask', 0)
            # Book "parede" (slope alto) no lado do fluxo = defesa forte
            if aggr > 0.2 and slope_b > 0.3:
                add('slope_book', min(1.0, slope_b), 'slope_bid ' + str(round(slope_b, 2)) + ' parede')
            elif aggr < -0.2 and slope_a > 0.3:
                add('slope_book', min(1.0, slope_a), 'slope_ask ' + str(round(slope_a, 2)) + ' parede')
            # Book "rampa" (slope negativo) = liquidez fragil
            elif slope_b < -0.2 or slope_a < -0.2:
                add('slope_book', -0.3, 'book rampa (liquidez fragil)')
        
        # === CAMADA 3: Trade Metrics ===
        avg_sz = f.get('avg_trade_size', 0)
        max_sz = f.get('max_trade_size', 0)
        seq_p = f.get('seq_pattern', 0)
        trades_ps = f.get('trades_per_sec', 0)
        
        if avg_sz > 20 and abs(aggr) > 0.2:
            add('trade_metrics', 0.3, 'avg_sz ' + str(round(avg_sz)) + ' grande')
        if max_sz > avg_sz * 3 and avg_sz > 5:
            add('trade_metrics', -0.3, 'spike max ' + str(max_sz))
        if abs(seq_p) > 0.6:
            add('trade_metrics', 0.3, 'seq ' + str(round(seq_p, 2)))
        if trades_ps > 30:
            add('trade_metrics', 0.2, 'rapido ' + str(trades_ps) + '/s')
        
        # === CAMADA 4: Cross-Asset Engine ===
        if ativo == ATIVO_PRINCIPAL and ATIVO_CONTEXTO and ATIVO_CONTEXTO in self.features:
            ca = self.cross_engine.calcular()
            if ca['lag_ms'] > 0 and ca['lag_ms'] < 1000:
                add('cross_lag', 0.4, 'WDO lag ' + str(ca['lag_ms']) + 'ms')
            if abs(ca['corr_aggr']) > 0.3:
                mult = 0.5 if (ca['corr_aggr'] > 0) == (aggr > 0) else -0.5
                add('cross_lag', mult, 'corr_aggr ' + str(round(ca['corr_aggr'], 2)))
            if abs(ca['divergencia']) > 0.5:
                add('cross_lag', -0.4, 'diverg ' + str(round(ca['divergencia'], 2)))
            if ca['wdo_leading'] > 0.5:
                add('cross_lag', 0.3, 'WDO leading ' + str(round(ca['wdo_leading'], 2)))
            elif ca['wdo_leading'] < -0.2:
                add('cross_lag', -0.3, 'WDO nao seguiu')
            if abs(ca['resposta_win']) > 0.3:
                if (ca['resposta_win'] > 0) == (aggr > 0):
                    add('cross_lag', 0.3, 'WIN respondeu WDO')
        
        # CROSS-ASSET (v2 restaurado)
        if ativo == ATIVO_PRINCIPAL and ATIVO_CONTEXTO:
            f_ctx = self.features.get(ATIVO_CONTEXTO, {})
            if f_ctx and f_ctx.get('vol_total', 0) > 5:
                ctx_aggr = f_ctx.get('aggr_imb', 0)
                ctx_dp = f_ctx.get('delta_preco', 0)
                if aggr > 0.2 and ctx_aggr > 0.1:
                    add('cross_asset', 1.0, f"ctx +{ctx_aggr:.2f}")
                elif aggr < -0.2 and ctx_aggr < -0.1:
                    add('cross_asset', 1.0, f"ctx {ctx_aggr:.2f}")
                elif aggr > 0.2 and ctx_aggr < -0.1:
                    add('cross_asset', -0.667, f"ctx CONTRA {ctx_aggr:.2f}")
                elif aggr < -0.2 and ctx_aggr > 0.1:
                    add('cross_asset', -0.667, f"ctx CONTRA +{ctx_aggr:.2f}")
                
                if aggr > 0.2 and ctx_dp > 2:
                    add('cross_asset_preco', 1.0, f"ctx preco +{ctx_dp:.0f}")
                elif aggr < -0.2 and ctx_dp < -2:
                    add('cross_asset_preco', 1.0, f"ctx preco {ctx_dp:.0f}")
        
        # v7: Padrões - spoof alert
        if segs:
            lid = max(segs, key=lambda c: abs(sum(segs[c].values()))) if segs else None
            if lid:
                consistencia = self.padroes.assinatura_liquidez(lid)
                if consistencia > 0.4:
                    add('liquidez_removida', -consistencia * 1.5, f"{lid} liquidez removida ({consistencia:.0%})")
        
        # v7: Padrões - stop-hunt
        nivel_stop = self.padroes.nivel_stop_perto(preco, tolerancia_pts=15)
        if nivel_stop:
            forca = nivel_stop['forca']
            tipo = nivel_stop['tipo']
            if tipo == 'topo' and aggr > 0.2:
                add('stop_hunt', -forca * 1.5, f"stop-hunt topo {nivel_stop['nivel']}")
            elif tipo == 'fundo' and aggr < -0.2:
                add('stop_hunt', forca * 1.5, f"stop-hunt fundo {nivel_stop['nivel']}")
        
        # ABSORÇÃO: muito volume, pouco deslocamento
        absorcao = f.get('absorcao_ratio', 0)
        if absorcao > 0 and vol > 20:
            # Alta absorção + agressão forte = alguém está defendendo
            if absorcao > 50 and abs(aggr) > 0.3:
                add('absorcao_preco', 1.0, f"absorcao {absorcao:.0f}pts/vol")
            elif absorcao > 20 and abs(aggr) > 0.2:
                add('absorcao_preco', 0.5, f"absorcao leve {absorcao:.0f}")
            # Baixa absorcao + agressao forte = preco andando livre
            elif absorcao < 5 and abs(aggr) > 0.3 and abs(dp) > 5:
                add('absorcao_preco', -0.5, f"fluxo livre {absorcao:.0f}")
        
        # OFI (Order Flow Imbalance)
        ofi_total = f.get('ofi_total', 0)
        ofi_ewma_v = f.get('ofi_ewma', 0)
        if abs(ofi_total) > 20:
            if (ofi_total > 0 and aggr > 0.2) or (ofi_total < 0 and aggr < -0.2):
                mult = min(1.0, abs(ofi_total) / 200)
                add('ofi', mult, f"OFI {ofi_total:+.0f}")
            elif (ofi_total > 20 and aggr < -0.2) or (ofi_total < -20 and aggr > 0.2):
                add('ofi', -0.5, f"OFI divergente {ofi_total:+.0f}")
        if abs(ofi_ewma_v) > 15:
            if (ofi_ewma_v > 0 and aggr > 0) or (ofi_ewma_v < 0 and aggr < 0):
                mult = min(1.0, abs(ofi_ewma_v) / 150)
                add('ofi_ewma', mult, f"OFI_EWMA {ofi_ewma_v:+.0f}")
        
        # RANGE DE VARREDURA
        rng = self.trackers[ativo]['range']
        rng_estado = rng.get_estado()
        if rng_estado['amplitude'] > 10:
            f['range_estado'] = rng_estado['estado']
            f['range_topo'] = rng_estado['topo']
            f['range_fundo'] = rng_estado['fundo']
            f['range_amplitude'] = rng_estado['amplitude']
            f['range_testes_topo'] = rng_estado['testes_topo']
            f['range_testes_fundo'] = rng_estado['testes_fundo']
            f['range_expansao'] = rng_estado['expansao']
            
            # Preço no topo do range + agressão compradora = possível rompimento
            if rng_estado['estado'] == 'topo' and aggr > 0.2:
                if rng_estado['testes_topo'] >= 3:
                    add('range', 1.0, f"range topo {rng_estado['testes_topo']}x testes")
                else:
                    add('range', 0.3, f"range topo ({rng_estado['testes_topo']}x)")
            # Preço no fundo do range + agressão vendedora = possível rompimento
            elif rng_estado['estado'] == 'fundo' and aggr < -0.2:
                if rng_estado['testes_fundo'] >= 3:
                    add('range', 1.0, f"range fundo {rng_estado['testes_fundo']}x testes")
                else:
                    add('range', 0.3, f"range fundo ({rng_estado['testes_fundo']}x)")
            # Preço dentro do range = neutralizar
            elif rng_estado['estado'] == 'dentro':
                add('range', -0.3, f"range dentro ({rng_estado['amplitude']:.0f} pts)")
            # Range comprimindo = explosao vindo
            if rng_estado['expansao'] < -0.2:
                add('range', 0.5, f"range comprimindo ({rng_estado['expansao']:.0%})")
            
            # ACUMULACAO POR CORRETORA NO RANGE
            acum = self.trackers[ativo]['acumulacao']
            ts_now = f['time_ms'] // 1000
            ac_result = acum.detectar(ts_now, rng_estado['topo'], rng_estado['fundo'], preco)
            if ac_result:
                f['acumulacao_direcao'] = ac_result['direcao_provavel']
                f['acumulacao_forca'] = ac_result['forca']
                f['acumulacao_zona'] = ac_result['zona']
                f['acumulacao_inst_net'] = ac_result['inst_net']
                f['acumulacao_var_net'] = ac_result['var_net']
                f['acumulacao_n_trades'] = ac_result['n_trades']
                
                # Cross-asset WDO confirmando (correlacao INVERSA: WDO cai = WIN sobe)
                wdo_f = self.features.get(ATIVO_CONTEXTO, {})
                wdo_aggr = wdo_f.get('aggr_imb', 0)
                
                direcao = ac_result['direcao_provavel']
                forca = ac_result['forca']
                
                if direcao == 'cima':
                    # WIN sobe + WDO cai = confirmacao inversa
                    if wdo_aggr < -0.1:
                        add('acumulacao', forca * 1.2, f"acum C + WDO vendendo ({ac_result['inst_net']:+.0f})")
                    elif wdo_aggr > 0.1:
                        add('acumulacao', forca * 0.5, f"acum C mas WDO compra ({ac_result['inst_net']:+.0f})")
                    else:
                        add('acumulacao', forca * 0.8, f"acum C ({ac_result['inst_net']:+.0f})")
                elif direcao == 'baixo':
                    # WIN cai + WDO sobe = confirmacao inversa
                    if wdo_aggr > 0.1:
                        add('acumulacao', -forca * 1.2, f"acum V + WDO comprando ({ac_result['inst_net']:+.0f})")
                    elif wdo_aggr < -0.1:
                        add('acumulacao', -forca * 0.5, f"acum V mas WDO vende ({ac_result['inst_net']:+.0f})")
                    else:
                        add('acumulacao', -forca * 0.8, f"acum V ({ac_result['inst_net']:+.0f})")
        
        # Regime ANTES do threshold
        score_raw = score
        score, regime = self.ajustar_por_regime(ativo, score_raw, motivos, regime_info)
        
        # ML Score (combina heuristica + modelo)
        ml_prob = 0.5
        ml_sinal = 0
        if self.scorer and ativo in self.scorer.prob:
            ml_prob = self.scorer.prob[ativo]
            ml_threshold = CONFIG.get('ml_threshold', 0.6)
            if ml_prob >= ml_threshold:
                ml_sinal = 1  # compra
            elif ml_prob <= (1 - ml_threshold):
                ml_sinal = -1  # venda
            # Combina: ML tem 40% de peso, heuristica 60%
            ml_score = (ml_prob - 0.5) * 2  # normaliza para [-1, 1]
            score = 0.6 * score + 0.4 * ml_score * 3.0  # escala
            motivos.append(f'ML={ml_prob:.2f}')
        
        # Sinal: só opera na direção da agressão quando o score é positivo
        # (nunca inverte contra o fluxo — score negativo = neutro)
        if score > 0.5:  # v9.27: de 0.3 para 0.5 — reduz entradas ruidosas
            sinal = lado
        else:
            if sinal == 0:
                motivos = ['fluxo fraco']
        
        # Filtros de horário
        hor_ok, hor_motivo = self.horario_permitido()
        if not hor_ok and self.posicao is None:
            motivos.append(f'horario={hor_motivo}')
            sinal = 0
        
        # Estrategia por regime
        estrategia = CONFIG["estrategias"].get(regime, CONFIG["estrategias"]["lateral"])
        tp_mult = estrategia.get('tp_mult', 1.0)
        sl_mult = estrategia.get('sl_mult', 1.0)
        limiar_confirmacao_regime = estrategia.get('limiar_confirmacao', CONFIG["limiar_confirmacao"])
        cooldown_entre_trades_s_regime = estrategia.get('cooldown_entre_trades_s', CONFIG["cooldown_entre_trades_s"])
        max_holding_s_regime = estrategia.get('max_holding_s', CONFIG["tempo_max_posicao_s"])
        
        # TP/SL - EMA do range (media de 10 min) para estabilidade
        ranges_hist = []
        for i in range(60, min(len(hist), 600), 60):
            ph_i = [h['preco_fim'] for h in hist[-i:] if h['preco_fim'] > 0]
            if len(ph_i) >= 2:
                ranges_hist.append(max(ph_i) - min(ph_i))
        vol_p = sum(ranges_hist) / len(ranges_hist) if ranges_hist else abs(dp)
        if vol_p < 100:
            vol_p = 100
        # TP: 60% do range, SL: 40% do range (melhor R:R)
        tp = round(vol_p * 0.6 * tp_mult / 5) * 5
        sl = round(vol_p * 0.4 * sl_mult / 5) * 5
        # Minimos realistas: WIN precisa de SL largo para nao ser stopado pelo ruido
        min_sl = int(150 * sl_mult)
        min_tp = int(200 * tp_mult)
        if sl < min_sl:
            sl = min_sl
        if tp < min_tp:
            tp = min_tp
        
        conf = min(abs(score) / 3.0, 1.0)
        custo = custo_execucao(ativo)
        
        # Sizing dinamico: TP/SL proporcional a confianca
        # conf Alta (0.8-1.0): TP maior, SL menor (R:R favoravel)
        # conf Media (0.5-0.8): TP/SL padrao
        # conf Baixa (<0.5): TP menor, SL maior (protege capital)
        if conf >= 0.8:
            conf_tp_mult = 1.2   # +20% TP
            conf_sl_mult = 0.85  # -15% SL
        elif conf >= 0.5:
            conf_tp_mult = 1.0
            conf_sl_mult = 1.0
        else:
            conf_tp_mult = 0.8   # -20% TP
            conf_sl_mult = 1.15  # +15% SL
        tp = round(tp * conf_tp_mult / 5) * 5
        sl = round(sl * conf_sl_mult / 5) * 5
        # Reaplicar minimos apos ajuste de confianca
        if sl < min_sl:
            sl = min_sl
        if tp < min_tp:
            tp = min_tp
        # Custo de execucao: aplicado POR ÚLTIMO
        tp -= custo
        sl += custo
        
        # Atualizar confianca EWMA com decay em neutro
        alpha = 0.3
        if abs(score) < 0.1:
            self.confianca_ewma *= 0.85  # decay rapido em neutro
        else:
            self.confianca_ewma = (1 - alpha) * self.confianca_ewma + alpha * abs(score)
        
        # Gerenciar posição
        # v9.27: persistencia do sinal — conta segmentos consecutivos
        if sinal != 0 and sinal == self._sinal_anterior_bruto:
            self._sinal_streak += 1
        elif sinal != 0:
            self._sinal_streak = 1
        else:
            self._sinal_streak = 0
        self._sinal_anterior_bruto = sinal
        self._score_anterior = score  # v9.27: score atual para reversao
        acao = self.gerenciar_posicao(ativo, sinal, preco, tp, sl, motivos, contrib, regime=regime,
                                  limiar_confirmacao=limiar_confirmacao_regime,
                                  cooldown_entre_trades_s=cooldown_entre_trades_s_regime,
                                  max_holding_s=max_holding_s_regime)
        
        if acao.get('acao') == 'ABRIU':
            log.info(f"[POS] ABRIU {self.posicao['lado']} @ {preco:.0f} tp={tp} sl={sl}")
        elif acao.get('acao') == 'FECHOU':
            log.info(f"[POS] FECHOU {acao['motivo']} pnl={acao['pnl']:+.0f}")
        
        # Atualiza sinais
        if self.posicao and ativo == ATIVO_PRINCIPAL:
            pos = self.posicao
            self.sinais[ativo] = {
                'sinal': 1 if pos['lado'] == 'C' else -1,
                'confianca': round(conf, 3), 'score': round(score, 3),
                'motivos': pos.get('motivos', []) or ['neutro'],
                'lado_fluxo': 'C' if aggr > 0 else 'V', 'tp': tp, 'sl': sl,
                'ml_prob': round(ml_prob, 3),
            }
        else:
            self.sinais[ativo] = {
                'sinal': sinal, 'confianca': round(conf, 3), 'score': round(score, 3),
                'motivos': motivos or ['neutro'], 'lado_fluxo': 'C' if aggr > 0 else 'V',
                'tp': tp, 'sl': sl,
                'ml_prob': round(ml_prob, 3),
            }
        
        if ativo == ATIVO_PRINCIPAL:
            self._gravar_decisao({
                'time_ms': f['time_ms'], 'ativo': ativo, 'seg': f['time_ms'] // 1000,
                'aggr_imb': aggr, 'price_eff': eff, 'aceleracao': acel,
                'score': round(score, 3), 'sinal': sinal, 'contrib': contrib, 'motivos': motivos,
                'acao': acao.get('acao') if isinstance(acao, dict) else None,
            })
            self._salvar_posicao_checkpoint()

    def _suavizar_sinal(self, lado_bruto):
        """CORREÇÃO: sem código duplicado, neutro não incrementa contador."""
        if lado_bruto == 0:
            return self.sinal_confirmado
        
        if lado_bruto == self._lado_anterior:
            self.sinal_contador += 1
        else:
            self._lado_anterior = lado_bruto
            self.sinal_contador = 1
        
        conf_alvo = self.confirmacao_necessaria  # atualizado por regime em ajustar_por_regime
        if self.sinal_contador >= conf_alvo:
            self.sinal_confirmado = lado_bruto
            self._score_confirmado = getattr(self, "_score_anterior", 0.0)
        
        if lado_bruto == self.sinal_confirmado:
            return lado_bruto
        
        return self.sinal_confirmado if self.sinal_confirmado != 0 else 0

    def verificar_saidas_tempo_real(self):
        with self.lock:
            if self.posicao is None:
                return None
            preco = self._obter_ultimo_preco(self.posicao['ativo'])
            if preco <= 0:
                return None
            return self._checar_saidas(preco)

    def _preco_plausivel(self, sym, preco):
        """Sanity check: rejeita preco fora da faixa do ativo ou com salto
        absurdo vs. ultimo preco valido. Protege contra contaminacao
        cross-asset (ex.: WIN recebendo preco de WDO)."""
        if preco <= 0:
            return False
        # 1. Faixa absoluta por prefixo
        for prefixo, (lo, hi) in CONFIG.get("faixas_preco", {}).items():
            if sym.upper().startswith(prefixo):
                if not (lo <= preco <= hi):
                    self._anomalias_preco[sym] += 1
                    log.warning(f"[SANITY] {sym}: preco {preco:.0f} fora da faixa "
                                f"[{lo},{hi}] - REJEITADO (total: {self._anomalias_preco[sym]})")
                    return False
                break
        # 2. Salto percentual vs. ultimo preco valido
        ultimo = self._ultimo_preco_valido.get(sym)
        if ultimo and ultimo > 0:
            salto = abs(preco - ultimo) / ultimo
            if salto > CONFIG.get("max_salto_preco_pct", 0.15):
                self._anomalias_preco[sym] += 1
                log.warning(f"[SANITY] {sym}: salto {salto:.0%} ({ultimo:.0f} -> {preco:.0f}) "
                            f"- REJEITADO (total: {self._anomalias_preco[sym]})")
                return False
        self._ultimo_preco_valido[sym] = preco
        return True

    def _obter_ultimo_preco(self, ativo):
        f = self.features.get(ativo)
        if f and f.get('preco_fim', 0) > 0:
            return f['preco_fim']
        st = self.stats.get(ativo)
        if st and st.get('p1', 0) > 0:
            return st['p1']
        # Fallback: ultimo trade do buffer
        negs = self.buffer.get(ativo, [])
        if negs and negs[-1].get('preco', 0) > 0:
            return negs[-1]['preco']
        # Fallback: historico recente
        hist = self.historico.get(ativo, [])
        if hist:
            return hist[-1].get('preco_fim', 0)
        return 0.0

    def _checar_saidas(self, preco, max_holding_s=None):
        pos = self.posicao
        if pos is None:
            return None
        lado = pos['lado']
        raw_pnl = (preco - pos['preco_medio']) if lado == 'C' else (pos['preco_medio'] - preco)
        leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
        pos['mfe'] = max(pos.get('mfe', 0), raw_pnl)
        pos['mae'] = min(pos.get('mae', 0), raw_pnl)
        
        agora = datetime.now()
        if CONFIG["desligar_horarios_ruins"] and (agora.hour, agora.minute) >= CONFIG["horario_fechamento"]:
            return self._fechar_posicao(preco, motivo='FECHAMENTO_HORARIO')
        
        if (max_holding_s if max_holding_s is not None else CONFIG["tempo_max_posicao_s"]) > 0 and time.time() - pos['aberta_em'] >= (max_holding_s if max_holding_s is not None else CONFIG["tempo_max_posicao_s"]):
            return self._fechar_posicao(preco, motivo='TIMEOUT')
        
        # REVERSAO: so apos holding minimo, fora de lateral, e com confianca alta
        if CONFIG["reversao_fecha"] and self.sinal_confirmado != 0:
            holding = time.time() - pos['aberta_em']
            regime_info = self.detectar_regime(pos['ativo'])
            regime_nome = regime_info.get('regime', 'lateral') if isinstance(regime_info, dict) else regime_info
            if holding >= CONFIG["min_holding_reversao_s"] and regime_nome != 'lateral':
                sinal_lado = 1 if self.sinal_confirmado > 0 else -1
                pos_lado = 1 if pos['lado'] == 'C' else -1
                if sinal_lado != pos_lado and abs(self.confianca_ewma) >= CONFIG["confianca_min_reversao"] and abs(self._score_confirmado) > 0.4:
                    return self._fechar_posicao(preco, motivo='REVERSAO')
        
        # Breakeven
        if CONFIG["usar_breakeven"] and not pos.get('breakeven_ativado') and leveraged_pnl >= pos['tp'] * 0.5:
            pos['breakeven_ativado'] = True
            pos['stop_preco'] = pos['preco_medio']
        
        # Trailing
        if CONFIG["usar_trailing"] and leveraged_pnl >= pos['tp'] * 0.7:
            trail_dist = pos['tp'] * 0.3
            if lado == 'C':
                novo_stop = preco - trail_dist
                if novo_stop > pos.get('stop_preco', 0):
                    pos['stop_preco'] = novo_stop
            else:
                novo_stop = preco + trail_dist
                if novo_stop < pos.get('stop_preco', 9e18):
                    pos['stop_preco'] = novo_stop
        
        # TP
        if (lado == 'C' and preco >= pos['entrada'] + pos['tp']) or (lado == 'V' and preco <= pos['entrada'] - pos['tp']):
            return self._fechar_posicao(preco, motivo='TP')
        
        # SL
        if pos.get('stop_preco') and pos['stop_preco'] > 0:
            if lado == 'C' and preco <= pos['stop_preco']:
                return self._fechar_posicao(preco, motivo='SL')
            if lado == 'V' and preco >= pos['stop_preco']:
                return self._fechar_posicao(preco, motivo='SL')
        
        # Manter posição aberta se nenhuma condição de saída foi atendida
        return None

    def gerenciar_posicao(self, ativo, sinal_bruto, preco, tp, sl, motivos, contrib, regime=None,
                          limiar_confirmacao=None, cooldown_entre_trades_s=None, max_holding_s=None):
        if ativo != ATIVO_PRINCIPAL:
            return {'acao': 'CONTEXT_ONLY'}
        
        lado = self._suavizar_sinal(sinal_bruto)
        sinal_valido = (lado != 0 and abs(self.confianca_ewma) >= (limiar_confirmacao if limiar_confirmacao is not None else CONFIG["limiar_confirmacao"]))
        
        if self.posicao is not None:
            resultado = self._checar_saidas(preco, max_holding_s=max_holding_s)
            return resultado if resultado is not None else {'acao': 'MANTER'}
        
        if sinal_valido and preco > 0 and self._sinal_streak >= 2:  # v9.27: 2+ segmentos consecutivos
            # Cooldown: evita flip-flop (overtrading por reversao)
            # Conta a partir do FECHAMENTO, nao da abertura
            if self.resultados:
                ultimo_fechamento = self.resultados[-1].get('fechada_em', 0)
                if time.time() - ultimo_fechamento < (cooldown_entre_trades_s if cooldown_entre_trades_s is not None else CONFIG["cooldown_entre_trades_s"]):
                    return {'acao': 'COOLDOWN'}
            if not horario_permite_abrir():
                return {'acao': 'FORA_DE_HORARIO'}
            # Circuit breaker proporcional
            if self.circuit_breaker_nivel >= 3:
                return {'acao': 'CIRCUIT_BREAKER'}
            # Auto-recovery: se passou 30min sem perda, reduz nivel
            if self.circuit_breaker_nivel > 0 and time.time() - self._cb_ultimo_reset > 1800:
                self.circuit_breaker_nivel = max(0, self.circuit_breaker_nivel - 1)
                self._cb_ultimo_reset = time.time()
                log.info(f'[CB] Auto-recovery: nivel -> {self.circuit_breaker_nivel}')
            if self.trades_dia >= CONFIG["max_trades_dia"]:
                return {'acao': 'LIMITE_TRADES_DIA'}
            
            l = 'C' if lado > 0 else 'V'
            stop_preco = (preco - sl) if l == 'C' else (preco + sl)
            
            self.previsoes.append({'idx': len(self.previsoes), 'ativo': ativo, 'lado': l,
                                    'entrada': preco, 'tp': tp, 'sl': sl,
                                    'aberta_em': time.time()})
            # Volatility-targeted position sizing
            sl_points = abs(preco - stop_preco)  # Distance to stop loss in points
            target_risk = CONFIG["position_sizing"]["target_risk_per_trade"]  # Target risk per trade in points
            max_qty = CONFIG["position_sizing"]["max_position_size"]  # Maximum position size
            
            if sl_points > 0:
                base_qty = target_risk / sl_points
                quantidade = max(1, min(max_qty, round(base_qty)))
            else:
                quantidade = 1  # Fallback if SL is at entry (shouldn't happen)
            
            self.posicao = {
                'ativo': ativo, 'lado': l, 'entrada': preco, 'preco_medio': preco,
                'stop_preco': stop_preco, 'tp': tp,
                'aberta_em': time.time(), 'motivos': list(motivos), 'contrib': list(contrib),
                'prev_idx': len(self.previsoes) - 1,
                'mfe': 0.0, 'mae': 0.0, 'breakeven_ativado': False,
                'regime_abertura': regime or 'indefinido',
                'quantidade': quantidade  # Position size in contracts
            }
            self.trades_dia += 1
            self._salvar_posicao_checkpoint()
            return {'acao': 'ABRIU'}
        
        return {'acao': 'AGUARDE'}

    def _fechar_posicao(self, preco, motivo):
        pos = self.posicao
        if pos is None:
            return {'acao': 'SEM_POSICAO'}
        lado = pos['lado']
        raw_pnl = (preco - pos['preco_medio']) if lado == 'C' else (pos['preco_medio'] - preco)
        leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
        acertou = leveraged_pnl > 0
        
        self.resultados.append({
            'idx': pos.get('prev_idx', 0), 'preco_antes': pos['entrada'],
            'preco_depois': preco, 'acertou': acertou, 'delta': leveraged_pnl,
            'lado': lado, 'mfe': pos.get('mfe', 0), 'mae': pos.get('mae', 0), 'motivo': motivo,
            'ts': datetime.now().isoformat(timespec='seconds'),
            'fechada_em': time.time(),
        })
        
        self.aprender_mfe_mae(pos.get('contrib', []), acertou, pos.get('mfe', 0), pos.get('mae', 0), regime_abertura=pos.get('regime_abertura'))
        # PnL liquido (desconta custo_execucao)
        custo = custo_execucao(pos.get('ativo', ATIVO_PRINCIPAL))
        pnl_liquido = leveraged_pnl - custo
        self.pnl_dia += pnl_liquido
        self.perdas_consecutivas = 0 if acertou else self.perdas_consecutivas + 1
        
        # Circuit breaker proporcional — thresholds do CONFIG
        cb_n1_perdas = CONFIG.get('cb_nivel1_perdas', CONFIG["max_perdas_consecutivas"])
        cb_n1_pnl = CONFIG.get('cb_nivel1_pnl', CONFIG["max_drawdown_dia_pontos"])
        cb_n2_perdas = CONFIG.get('cb_nivel2_perdas', cb_n1_perdas + 2)
        cb_n2_pnl = CONFIG.get('cb_nivel2_pnl', cb_n1_pnl * 0.6)
        cb_n3_perdas = CONFIG.get('cb_nivel3_perdas', cb_n2_perdas + 2)
        # v9.13: defaults escalados corretammente — antes o nível 3 usava o
        # mesmo threshold do nível 1 (cb_n1_pnl), invertendo a cascata.
        cb_n3_pnl = CONFIG.get('cb_nivel3_pnl', cb_n2_pnl * 1.8)
        
        if self.perdas_consecutivas >= cb_n3_perdas or self.pnl_dia <= cb_n3_pnl:
            self.circuit_breaker_nivel = 3  # bloqueado no dia
            log.warning(f'[CB] Nivel 3 (bloqueado): {self.perdas_consecutivas} perdas, pnl={self.pnl_dia:.0f}')
        elif self.perdas_consecutivas >= cb_n2_perdas or self.pnl_dia <= cb_n2_pnl:
            self.circuit_breaker_nivel = 2  # forte
            log.warning(f'[CB] Nivel 2 (forte): {self.perdas_consecutivas} perdas, pnl={self.pnl_dia:.0f}')
        elif self.perdas_consecutivas >= cb_n1_perdas or self.pnl_dia <= cb_n1_pnl:
            self.circuit_breaker_nivel = max(self.circuit_breaker_nivel, 1)  # cautela (nunca baixa)
            log.warning(f'[CB] Nivel 1 (cautela): {self.perdas_consecutivas} perdas, pnl={self.pnl_dia:.0f}')
        if self.perdas_consecutivas == 0:
            self._cb_ultimo_reset = time.time()
        
        self.posicao = None
        self.confianca_ewma = 0.0
        self.sinal_confirmado = 0
        self._lado_anterior = 0
        self.sinal_contador = 0
        self._salvar_posicao_checkpoint()
        return {'acao': 'FECHOU', 'pnl': pnl, 'motivo': motivo}

    def aprender_mfe_mae(self, contrib, acertou, mfe, mae, regime_abertura=None):
        if not contrib:
            return
        
        # Decay + ajuste em pesos GLOBAIS e por REGIME
        decay = CONFIG["aprendizado_decay"]
        # Usa regime da ABERTURA (nao o atual, que pode ter mudado)
        regime_nome = regime_abertura or 'lateral'
        
        for key, _ in contrib:
            inicial = self.PESOS_INICIAIS.get(key, 0.0)
            floor = abs(inicial) * 0.3
            
            # Decay global
            atual = self.pesos.get(key, 0.0)
            self.pesos[key] = max(abs(atual) * decay, floor) * (1 if atual >= 0 else -1)
            
            # Decay por regime
            if regime_nome in self.pesos_regime:
                atual_r = self.pesos_regime[regime_nome].get(key, 0.0)
                self.pesos_regime[regime_nome][key] = max(abs(atual_r) * decay, floor) * (1 if atual_r >= 0 else -1)
        
        qualidade_trade = (mfe / max(abs(mae), 1.0)) if mae != 0 else 2.0
        
        for key, mult in contrib:
            h = self.feature_hits[key]
            amostras_previas = h['acertos'] + h['erros']
            fator_confianca = min(1.0, amostras_previas / CONFIG["aprendizado_min_amostras"]) if amostras_previas else 0.2
            peso_atual = self.pesos.get(key, 0.0)
            
            if acertou:
                alvo = 1.0 if mult >= 0 else -1.0
            else:
                alvo = -1.0 if mult >= 0 else 1.0
            
            ajuste = CONFIG["aprendizado_delta"] * min(qualidade_trade, 2.0) * fator_confianca
            novo_peso = peso_atual + (alvo - peso_atual) * ajuste
            self.pesos[key] = max(-1.0, min(1.0, novo_peso))
            h['acertos' if acertou else 'erros'] += 1
        
        self._recalc_acuracia()

    def _recalc_acuracia(self):
        for ft, h in self.feature_hits.items():
            total = h['acertos'] + h['erros']
            self.acuracia[ft] = h['acertos'] / total if total > 0 else 0

    def detectar_regime(self, ativo):
        """Regime bidimensional: direcao x volatilidade.
        Retorna dict {'regime': str, 'direcao': str, 'vol': str}.
        Regime composto = direcao_vol (ex: 'tendencia_alta_vol_alta').
        C5: resultado cacheado por 5s para evitar recalculo O(n) no lock.
        """
        agora = time.time()
        cached = self._regime_cache.get(ativo)
        if cached and (agora - cached[0]) < 5.0:
            return cached[1]
        hist = list(self.historico.get(ativo, []))
        if len(hist) < 10:
            return {'regime': 'indefinido', 'direcao': 'neutro', 'vol': 'normal'}
        ultimos = hist[-300:]
        precos = [h['preco_fim'] for h in ultimos if h['preco_fim'] > 0]
        aggrs = [h['aggr_imb'] for h in ultimos]
        
        if len(precos) < 5:
            return {'regime': 'indefinido', 'direcao': 'neutro', 'vol': 'normal'}
        
        delta = precos[-1] - precos[0]
        vol_realizada = max(precos) - min(precos) if len(precos) >= 2 else 0
        aggr_medio = sum(aggrs) / len(aggrs) if aggrs else 0
        
        # Dimensao 1: volatilidade
        if vol_realizada > 100:
            vol = 'alta'
        elif vol_realizada < 20:
            vol = 'baixa'
        else:
            vol = 'normal'
        
        # Dimensao 2: direcao
        if abs(delta) > 20 and abs(aggr_medio) > 0.15:
            direcao = 'alta' if delta > 0 else 'baixa'
        else:
            direcao = 'neutro'
        
        # Regime composto
        if direcao == 'neutro':
            if vol == 'alta':
                regime = 'vol_alta'
            elif vol == 'baixa':
                regime = 'vol_baixa'
            else:
                regime = 'lateral'
        else:
            regime = f'tendencia_{direcao}'
            if vol != 'normal':
                regime += f'_vol_{vol}'
        
        resultado = {'regime': regime, 'direcao': direcao, 'vol': vol}
        self._regime_cache[ativo] = (time.time(), resultado)
        return resultado

    def ajustar_por_regime(self, ativo, score, motivos, regime_info=None):
        regime_info = regime_info or self.detectar_regime(ativo)
        regime = regime_info.get('regime', 'lateral') if isinstance(regime_info, dict) else regime_info
        direcao = regime_info.get('direcao', 'neutro') if isinstance(regime_info, dict) else 'neutro'
        vol = regime_info.get('vol', 'normal') if isinstance(regime_info, dict) else 'normal'
        
        # Busca estrategia por regime composto, fallback para basico
        estrategia = CONFIG["estrategias"].get(regime)
        if estrategia is None:
            # Fallback: tenta sem sufixo de vol
            regime_base = f'tendencia_{direcao}' if direcao != 'neutro' else 'lateral'
            estrategia = CONFIG["estrategias"].get(regime_base, CONFIG["estrategias"]["lateral"])
        
        with self.lock:
            self.confirmacao_necessaria = estrategia.get('confirmacao', CONFIG["confirmacao_necessaria"])
        
        ajuste = 1.0
        if vol == 'alta':
            ajuste *= 0.8  # vol alta = mais ruido, reduz score
        if 'tendencia' in regime:
            ajuste *= 1.1  # tendencia = mais confianca
        motivos.append(f'regime={regime}')
        
        return score * ajuste, regime

    def horario_permitido(self):
        if not CONFIG["desligar_horarios_ruins"]:
            return True, 'ok'
        now = datetime.now()
        h, m = now.hour, now.minute
        t = h * 60 + m
        
        abertura_fim = CONFIG["horario_abertura_fim"][0] * 60 + CONFIG["horario_abertura_fim"][1]
        if t < abertura_fim:
            return False, 'abertura'
        
        almoco_inicio = CONFIG["horario_almoco_inicio"][0] * 60 + CONFIG["horario_almoco_inicio"][1]
        almoco_fim = CONFIG["horario_almoco_fim"][0] * 60 + CONFIG["horario_almoco_fim"][1]
        if almoco_inicio <= t <= almoco_fim:
            return False, 'almoco'
        
        fech_inicio = CONFIG["horario_fechamento"][0] * 60 + CONFIG["horario_fechamento"][1]
        if t >= fech_inicio:
            return False, 'fechamento'
        
        return True, 'ok'

    def calcular_metricas(self):
        if len(self.resultados) < 2:
            return {}
        
        import numpy as np
        pnls = np.array([r['delta'] for r in self.resultados])
        acertos = sum(1 for r in self.resultados if r['acertou'])
        total = len(self.resultados)
        acuracia = acertos / total
        
        ganhos = pnls[pnls > 0].sum()
        perdas = abs(pnls[pnls < 0].sum())
        profit_factor = ganhos / perdas if perdas > 0 else float('inf')
        
        media = np.mean(pnls)
        std = np.std(pnls)
        
        # CORREÇÃO: Sharpe por trade, não por segundo
        dias = len(set(r.get('ts', '')[:10] for r in self.resultados if r.get('ts')))
        trades_por_dia = max(len(pnls) / max(1, dias), 1)
        sharpe = (media / std * np.sqrt(252 * trades_por_dia)) if std > 0 else 0
        
        cumsum = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumsum)
        drawdown = running_max - cumsum
        max_dd = drawdown.max()
        
        return {
            'acuracia': round(acuracia, 4),
            'profit_factor': round(profit_factor, 2),
            'sharpe': round(sharpe, 2),
            'max_drawdown': round(float(max_dd), 1),
            'expectancy': round(float(media), 2),
            'total_trades': total,
            'ganhos': int(acertos),
            'perdas': total - acertos,
        }

    def alimentar_book(self, ativo, snap, bid_vol, ask_vol, ofi_data=None, estado=None):
        with self.lock:
            seg = self.seg_atual
            ant = self.book_snap_ant.get(ativo)
            persist = self._book_persist[ativo]
            
            for b in set(snap) | set(persist):
                s = snap.get(b, {})
                p = persist[b]
                if s.get('bid_vol', 0) > 5:
                    p['bid_seguidos'] = p['bid_seguidos'] + 1 if p['bid_vol_ant'] > 5 else 1
                else:
                    p['bid_seguidos'] = 0
                if s.get('ask_vol', 0) > 5:
                    p['ask_seguidos'] = p['ask_seguidos'] + 1 if p['ask_vol_ant'] > 5 else 1
                else:
                    p['ask_seguidos'] = 0
                p['bid_vol_ant'] = s.get('bid_vol', 0)
                p['ask_vol_ant'] = s.get('ask_vol', 0)
            
            # Camada 1: Book level features
            blf = self.trackers[ativo]['book_level']
            if estado is not None:
                book_snap = extrair_book_snapshot(estado)
                book_level_data = blf.calcular(book_snap, ativo, int(time.time() * 1000))
            else:
                book_level_data = None
            
            if ant:
                snap_ant, bv_ant, av_ant = ant
                result = comparar_books(snap_ant, snap, persist)
                total = bv_ant + av_ant
                imb = (bv_ant - av_ant) / total if total > 0 else 0
                delta_bid = bid_vol - bv_ant
                delta_ask = ask_vol - av_ant
                
                agressao = self.features.get(ativo, {}).get('aggr_imb', 0)
                absorvedores = []
                for b, s in snap.items():
                    if agressao > 0.2 and s['ask_vol'] > 10:
                        absorvedores.append({'broker': b, 'lado': 'ask', 'vol': s['ask_vol'],
                                              'seguidos': persist[b].get('ask_seguidos', 0)})
                    elif agressao < -0.2 and s['bid_vol'] > 10:
                        absorvedores.append({'broker': b, 'lado': 'bid', 'vol': s['bid_vol'],
                                              'seguidos': persist[b].get('bid_seguidos', 0)})
                
                self.book_stats[ativo] = {
                    'imb': imb, 'bid_vol': bid_vol, 'ask_vol': ask_vol,
                    'delta_bid': delta_bid, 'delta_ask': delta_ask,
                    'thinning_bid': result['thinning_bid'], 'thinning_ask': result['thinning_ask'],
                    'n_retiradas': len(result['retiradas']), 'n_reposicoes': len(result['reposicoes']),
                    'retiradas_bid': sum(1 for r in result['retiradas'] if r['lado'] == 'bid'),
                    'retiradas_ask': sum(1 for r in result['retiradas'] if r['lado'] == 'ask'),
                    'reposicoes_bid': sum(1 for r in result['reposicoes'] if r['lado'] == 'bid'),
                    'reposicoes_ask': sum(1 for r in result['reposicoes'] if r['lado'] == 'ask'),
                    'absorvedores': absorvedores[:10],
                    'defesa_persistente': result['defesa_persistente'],
                    'layering': result['layering'],
                    'book_level': book_level_data,
                }
                
                evts = [r for r in result['retiradas'] + result['reposicoes'] if r['broker'] != '_anon']
                if evts:
                    be = self.book_events[ativo]
                    be.setdefault(seg, []).extend(evts)
                    while len(be) > CONFIG["book_events_seg_max"]:
                        be.popitem(last=False)
                
                # v7: spoof detection
                spoofs = self.padroes.detectar_spoof(ativo, snap, time.time())
                if spoofs:
                    for sp in spoofs:
                        log.info(f"[PADROES] spoof: {sp['broker']} {sp['lado']} -{sp['vol_retirada']} vol")
            
            # MOVIDO PARA CÁ (fora do if ant) — garante snapshot na 1a chamada
            self.book_snap_ant[ativo] = (snap, bid_vol, ask_vol)

            # CORREÇÃO: limpa corretoras mortas do persist
            for b in list(persist):
                if persist[b]['bid_seguidos'] == 0 and persist[b]['ask_seguidos'] == 0:
                    if b not in snap:
                        del persist[b]

    # Getters thread-safe
    def get_posicao(self):
        with self.lock:
            if self.posicao is None:
                return None
            pos = self.posicao
            preco = self._obter_ultimo_preco(pos['ativo'])
            raw_pnl = (preco - pos['preco_medio']) if pos['lado'] == 'C' else (pos['preco_medio'] - preco)
            leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
            # SL como offset (distancia), igual TP
            sl_abs = pos.get('stop_preco', 0)
            sl_offset = abs(sl_abs - pos['preco_medio']) if sl_abs > 0 else pos['tp']
            return {
                'lado': pos['lado'], 'entrada': pos['entrada'], 'preco_atual': preco,
                'pnl': leveraged_pnl, 'tp': pos['tp'], 'sl': sl_offset,
                'mfe': pos.get('mfe', 0), 'mae': pos.get('mae', 0),
                'duracao_s': time.time() - pos['aberta_em'],
            }

    def get_features(self):
        with self.lock:
            feat = copy.deepcopy(self.features)
            for ativo, f in feat.items():
                if ativo.startswith('_'):
                    continue
                hist = list(self.historico.get(ativo, []))
                if hist:
                    ri = self.detectar_regime(ativo)
                    f['regime'] = ri.get('regime', 'lateral') if isinstance(ri, dict) else 'lateral'
                    f['regime_info'] = ri
                # v9.36: OHLC intraday
                if ativo in self.ohlc:
                    oh = self.ohlc[ativo]
                    f['abertura_dia'] = oh['abertura']
                    f['maxima_dia'] = oh['maxima']
                    f['minima_dia'] = oh['minima']
                    f['fechamento_dia'] = oh['fechamento']
            return feat

    def get_sinais(self):
        with self.lock:
            return dict(self.sinais)

    def get_historico(self, segundos=1800):
        """v9.31: serie por segundo para o dashboard (pre-enche graficos).
        Retorna {ativo: [{seg, preco, aggr, vol, cvd, ofi}, ...]} dos ultimos N segundos.
        Leitura read-only de features_por_seg sob o lock compartilhado."""
        with self.lock:
            segs = [s for (_a, s) in self.features_por_seg]
            ref = max(segs) if segs else 0
            corte = ref - segundos
            out = {}
            for (ativo, seg), f in self.features_por_seg.items():
                if seg < corte:
                    continue
                out.setdefault(ativo, []).append({
                    'seg': seg,
                    'preco': f.get('preco_fim', 0) or 0,
                    'aggr': f.get('aggr_imb', 0) or 0,
                    'vol': f.get('vol_total', 0) or 0,
                    'cvd': f.get('cvd_total', 0) or 0,
                    'ofi': f.get('ofi_ewma', 0) or 0,
                })
            return out

    def get_book_level(self):
        with self.lock:
            result = {}
            for ativo, bs in self.book_stats.items():
                bl = bs.get('book_level')
                ca_data = {}
                if ativo == ATIVO_PRINCIPAL and hasattr(self, 'cross_engine'):
                    ca_data = self.cross_engine.calcular()
                result[ativo] = {
                    'book_level': bl or {},
                    'cross_asset': ca_data,
                }
            return result
    
    def get_book_stats(self):
        with self.lock:
            return {k: dict(v) for k, v in self.book_stats.items()}

    def get_estatisticas(self):
        with self.lock:
            ac = sum(1 for r in self.resultados if r['acertou'])
            total = len(self.resultados)
            return {
                'total': total, 'acertos': ac,
                'acuracia': ac / total if total > 0 else 0,
                'pesos': dict(self.pesos),
                'acuracia_por_feature': dict(self.acuracia),
                'resultados': list(self.resultados[-20:])
            }

    def get_resumo(self, ativo):
        with self.lock:
            st = self.stats.get(ativo)
            if not st or st['n'] == 0:
                return {}
            vc, vv = st['vc'], st['vv']
            return {
                'total_negocios': st['n'], 'vol_comprador': vc, 'vol_vendedor': vv,
                'aggr_imb': (vc - vv) / (vc + vv) if (vc + vv) > 0 else 0,
                'preco_inicio': st['p0'], 'preco_fim': st['p1'],
                'delta_preco': st['p1'] - st['p0']
            }

    def get_memoria(self):
        with self.lock:
            return {
                'total_negocios': sum(s['n'] for s in self.stats.values()),
                'n_prevs': len(self.previsoes), 'n_resultados': len(self.resultados),
                'erros_globais': dict(ERROS_GLOBAIS),
                'circuit_breaker_nivel': self.circuit_breaker_nivel,
                'trades_dia': self.trades_dia, 'pnl_dia': round(self.pnl_dia, 2),
                'perdas_consecutivas': self.perdas_consecutivas,
                'confianca_ewma': round(self.confianca_ewma, 3),
                'sinal_confirmado': self.sinal_confirmado,
                'anomalias_preco': dict(self._anomalias_preco),
            }

    def get_saldo_corretoras(self, ativo=None):
        """Retorna comprado, vendido e saldo de cada corretora."""
        with self.lock:
            resultado = {}
            for sym, cmap in self.agressao_por_corretora.items():
                if ativo and sym != ativo:
                    continue
                saldos = {}
                for corp, sd in cmap.items():
                    c = sd.get('c', 0)
                    v = sd.get('v', 0)
                    total = c - v
                    if abs(total) > 5 or (c + v) > 50:
                        tipo = classificar_corretora(corp)
                        saldos[corp] = {
                            'comprado': round(c, 1),
                            'vendido': round(v, 1),
                            'saldo': round(total, 1),
                            'lado': 'C' if total > 0 else 'V',
                            'tipo': tipo,
                            'label': corp,
                        }
                # Ordena por saldo absoluto (maior primeiro)
                resultado[sym] = sorted(saldos.values(), key=lambda x: -abs(x['saldo']))
            return resultado

    def salvar_sessao(self, final=False):
        with self.lock:
            self._flush_trades()
            self._flush_decisoes()
            self.salvar_aprendizado(self.base_dir)
            self._salvar_posicao_checkpoint()
            # v7: padrões
            self.padroes.aplicar_decay()
            self.padroes.salvar()
            if final:
                with self._io_lock:
                    for fp in (self._fp, self._fp_dec):
                        if fp is not None:
                            try:
                                fp.close()
                            except Exception:
                                pass
                    self._fp = None
                    self._fp_dec = None

# ============================================================
#   DASHBOARD
# ============================================================

class Handler(BaseHTTPRequestHandler):
    app = None

    def do_GET(self):
        p = urlparse(self.path)
        params = parse_qs(p.query)
        if p.path == '/':
            # v9.31: dashboard profissional (arquivo separado, editavel sem restart).
            # Cache em memoria com invalidação por mtime: 0 I/O no hot path.
            try:
                import pathlib
                _dash = pathlib.Path(__file__).resolve().parent / 'dashboard_pro.html'
                _mtime = _dash.stat().st_mtime if _dash.exists() else -1
                if getattr(Handler, '_dash_mtime', None) != _mtime:
                    Handler._dash_html = _dash.read_text(encoding='utf-8') if _dash.exists() else None
                    Handler._dash_mtime = _mtime
                if Handler._dash_html:
                    self._html(Handler._dash_html)
                    return
            except Exception:
                pass
            self._html(self.app.html())
        elif p.path == '/legacy':
            self._html(self.app.html())
        elif p.path == '/api/book_level':
            self._json(self.app.analise.get_book_level())
        elif p.path == '/api/features':
            f = self.app.analise.get_features()
            f['_principal'] = ATIVO_PRINCIPAL
            f['_contexto'] = ATIVO_CONTEXTO
            self._json(f)
        elif p.path == '/api/sinais':
            self._json(self.app.analise.get_sinais())
        elif p.path == '/api/posicao':
            self._json(self.app.analise.get_posicao() or {'acao': 'SEM_POSICAO'})
        elif p.path == '/api/learning':
            self._json(self.app.analise.get_estatisticas())
        elif p.path == '/api/memoria':
            self._json(self.app.analise.get_memoria())
        elif p.path == '/api/book':
            self._json(self.app.analise.get_book_stats())
        elif p.path == '/api/metricas':
            self._json(self.app.analise.calcular_metricas())
        elif p.path == '/api/resumo':
            a = params.get('ativo', [ATIVO_PRINCIPAL])[0]
            self._json(self.app.analise.get_resumo(a))
        elif p.path == '/api/padroes':
            self._json(self.app.analise.padroes.get_resumo())
        elif p.path == '/api/rtd_health':
            self._json(self.app.get_rtd_health())
        elif p.path == '/api/saldo_corretoras':
            self._json(self.app.analise.get_saldo_corretoras())
        elif p.path == '/api/contexto':
            # v9.32: VWAP intraday + ajuste oficial + distâncias
            self._json(self.app.get_contexto_mercado())
        elif p.path == '/api/historico':
            self._json(self.app.analise.get_historico())
        elif p.path == '/api/all':
            self._json({
                'features': self.app.analise.get_features(),
                'sinais': self.app.analise.get_sinais(),
                'posicao': self.app.analise.get_posicao() or {},
                'learning': self.app.analise.get_estatisticas(),
                'memoria': self.app.analise.get_memoria(),
                'metricas': self.app.analise.calcular_metricas(),
                'saldo_corretoras': self.app.analise.get_saldo_corretoras(),
                'padroes': self.app.analise.padroes.get_resumo(),
            })
        elif p.path == '/health':
            uptime = time.time() - self.app.tempo_inicio if hasattr(self.app, 'tempo_inicio') else 0
            self._json({
                'status': 'ok' if self.app._conexao_ok else 'disconnected',
                'uptime_s': round(uptime, 1),
                'negocios_total': self.app.analise.stats.get(ATIVO_PRINCIPAL, {}).get('n', 0),
                'fila_tam': fila_eventos.qsize(),
            })
        else:
            self.send_error(404)

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, obj):
        try:
            payload = json.dumps(obj, default=str).encode()
        except Exception:
            payload = b'{}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass  # cliente desconectou antes de receber; ignora silenciosamente

    def log_message(self, *a):
        pass

    def handle_error(self, request, client_address):
        """Suprime tracebacks de ConnectionAbortedError no log."""
        import sys
        exc = sys.exc_info()[0]
        if exc in (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            return  # ignora silenciosamente
        super().handle_error(request, client_address)

class App:
    def __init__(self):
        self.analise = Analise()
        self.estados = {}
        self._srv = None
        self._book_map = {}
        self._tt_map = {}
        self._topic_map = {}
        self._conexao_ok = False
        self._ultima_reconexao = 0.0
        self.tempo_inicio = time.time()
        self.captura = CapturaEventosMS(SAVE_DIR)
        
        # ML Scorer (opcional) — setado em self.analise.scorer
        modelo_path = CONFIG.get('ml_modelo', '')
        # v9.32: tabela de ajuste oficial (opcional, alimentada pelo integrar_base.py)
        tabela_ajuste = None
        try:
            from datetime import date
            _hoje = date.today()
            _csv_ajuste = os.path.join(SAVE_DIR,
                                          f'ajuste_diario_{_hoje.year}{_hoje.month:02d}.csv')
            if os.path.exists(_csv_ajuste):
                tabela_ajuste = pd.read_csv(_csv_ajuste)
                log.info(f'[ML] Tabela de ajuste oficial: {_csv_ajuste} '
                          f'({len(tabela_ajuste)} linhas)')
        except Exception as e:
            log.warning(f'[ML] Falha ao carregar tabela de ajuste: {e}')

        if HAS_SCORER and modelo_path and os.path.exists(modelo_path):
            try:
                ativos = [CONFIG['ativo_principal']]
                if CONFIG.get('ativo_contexto'):
                    ativos.append(CONFIG['ativo_contexto'])
                self.analise.scorer = ScorerML(
                    modelo_path, ativos,
                    tabela_ajuste_oficial=tabela_ajuste,
                )
                log.info(f'[ML] Scorer carregado: {modelo_path}')
            except Exception as e:
                log.warning(f'[ML] Falha ao carregar modelo: {e}')
        elif not HAS_SCORER:
            log.info('[ML] scorer.py nao encontrado — usando apenas heuristica')
        elif not modelo_path or not os.path.exists(modelo_path):
            log.info('[ML] Sem modelo treinado — usando apenas heuristica')

    def html(self):
        return '''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>WIN - Motor RT Alphaz v7+</title>
<style>
* {margin:0;padding:0;box-sizing:border-box}
body {font-family:'Courier New',monospace;background:#0a0a0a;color:#fff;padding:10px}
.container {display:flex;gap:20px;height:100vh}
.esq {flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}
.dir {width:450px;overflow-y:auto;border-left:1px solid #333;padding-left:15px}
.acao {font-size:5vw;font-weight:900;letter-spacing:0.2em;padding:10px 30px;border-radius:15px;margin-bottom:10px;text-align:center}
.preco {font-size:2.5vw;margin-bottom:5px}
.niveis {display:flex;gap:20px;font-size:1.3em;margin:10px 0}
.niv {text-align:center} .niv .label {color:#888;font-size:0.6em} .niv .val {font-weight:bold}
.barras {display:flex;gap:20px;font-size:1em;margin:10px 0}
.bar {text-align:center} .bar .label {color:#888;font-size:0.7em} .bar .val {font-weight:bold;font-size:1.3em}
.conf {font-size:1.2em;margin:8px 0} .motivos {color:#aaa;font-size:0.8em;text-align:center;max-width:500px;margin:8px 0;line-height:1.4}
h2 {color:#00bcd4;font-size:1em;margin:10px 0 5px 0}
table {border-collapse:collapse;width:100%;font-size:0.8em}
th,td {border:1px solid #333;padding:3px 6px;text-align:right} th {background:#16213e;color:#00bcd4}
.acc {font-size:1.5em;text-align:center;margin:5px 0}
.pos-box {margin-top:10px;padding:10px;border-radius:10px}
.metric {margin:5px 0}
</style></head><body>
<div class="container"><div class="esq">
<div id="rtd_alert" style="display:none;background:#f44336;color:#fff;font-size:1.5em;font-weight:900;text-align:center;padding:8px;border-radius:8px;margin-bottom:10px"></div><div class="acao" id="acao">- AGUARDE</div>
<div class="preco" id="preco">0</div>
<div class="niveis">
<div class="niv"><div class="label">TP</div><div class="val" style="color:#4caf50" id="tp">0</div></div>
<div class="niv"><div class="label">SL</div><div class="val" style="color:#f44336" id="sl">0</div></div>
<div class="niv"><div class="label">RISCO</div><div class="val" style="color:#ff9800" id="risco">0 pts</div></div>
<div class="niv"><div class="label">RETORNO</div><div class="val" style="color:#4caf50" id="retorno">0 pts</div></div>
</div>
<div class="barras">
<div class="bar"><div class="label">AGGRESSAO</div><div class="val" id="aggr">0</div></div>
<div class="bar"><div class="label">EFICIENCIA</div><div class="val" id="eff">0</div></div>
<div class="bar"><div class="label">PERSISTENCIA</div><div class="val" id="persist">0%</div></div>
<div class="bar"><div class="label">HHI</div><div class="val" id="hhi">0</div></div>
<div class="bar"><div class="label">NEGOCIOS</div><div class="val" id="negs">0</div></div>
<div class="bar"><div class="label">RANGE</div><div class="val" id="rng">-</div></div>
<div class="bar"><div class="label">ACUM</div><div class="val" id="acum">-</div></div>
<div class="bar"><div class="label">ABSORCAO</div><div class="val" id="abs">-</div></div>
<div class="bar"><div class="label">OFI</div><div class="val" id="ofi_el">0</div></div>
<div class="bar"><div class="label">REGIME</div><div class="val" id="regime_el">-</div></div>
</div>
<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
<div class="bar" style="flex:1;min-width:120px"><div class="label">SPREAD</div><div class="val" id="spread_el">-</div></div>
<div class="bar" style="flex:1;min-width:120px"><div class="label">IMB_L1</div><div class="val" id="imb_l1_el">-</div></div>
<div class="bar" style="flex:1;min-width:120px"><div class="label">MICRO</div><div class="val" id="micro_el">-</div></div>
<div class="bar" style="flex:1;min-width:120px"><div class="label">HHI</div><div class="val" id="hhi_el">-</div></div>
</div>
<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
<div class="bar" style="flex:1;min-width:100px"><div class="label">AVG_SZ</div><div class="val" id="avg_sz_el">-</div></div>
<div class="bar" style="flex:1;min-width:100px"><div class="label">SEQ</div><div class="val" id="seq_el">-</div></div>
<div class="bar" style="flex:1;min-width:100px"><div class="label">VEL</div><div class="val" id="vel_el">-</div></div>
<div class="bar" style="flex:1;min-width:100px"><div class="label">LAG</div><div class="val" id="lag_el">-</div></div>
<div class="bar" style="flex:1;min-width:100px"><div class="label">CORR</div><div class="val" id="corr_el">-</div></div>
<div class="bar" style="flex:1;min-width:100px"><div class="label">DIV</div><div class="val" id="div_el">-</div></div>
</div>
<div class="conf" id="conf">Conf: 0% | Score: 0</div>
<div class="motivos" id="motivos">-</div>
<div class="pos-box" id="pos-box" style="display:none"></div>
<div style="display:flex;gap:20px;margin-top:15px">
<div style="flex:1"><h2>WIN</h2><div id="saldo_win" style="font-size:0.8em">-</div></div>
<div style="flex:1"><h2>WDO</h2><div id="saldo_wdo" style="font-size:0.8em">-</div></div>
</div>
</div><div class="dir">
<h2>APRENDIZADO</h2>
<div class="acc" id="acc">0/0 = 0%</div>
<table><tr><th>V/X</th><th>Lado</th><th>Entrou</th><th>Saiu</th><th>Delta</th></tr>
<tbody id="log"></tbody></table>
<h2>PESOS</h2>
<table><tr><th>Feature</th><th>Peso</th><th>Acc</th></tr>
<tbody id="pesos"></tbody></table>
<h2>METRICAS</h2>
<div id="metricas" style="font-size:0.9em">-</div>
<h2>MEMORIA</h2>
<div id="mem" style="font-size:0.8em">-</div>
<h2>CTX (contexto)</h2>
<div id="wdo" style="font-size:0.8em">-</div>
<h2>PADROES</h2>
<div id="padroes" style="font-size:0.8em">-</div>
</div></div>
<script>
var ATIVO_PRINC = null, ATIVO_CTX = null;
function fmt(v){return (v||0).toLocaleString('pt-BR',{maximumFractionDigits:0})}
function fetchJSON(url){return fetch(url).then(r=>r.json()).catch(()=>null)}
async function update(){
const [s,f,l,p,m,met] = await Promise.all([
fetchJSON('/api/sinais'), fetchJSON('/api/features'),
fetchJSON('/api/learning'), fetchJSON('/api/posicao'),
fetchJSON('/api/memoria'), fetchJSON('/api/metricas')
]);
if(f && !ATIVO_PRINC){
ATIVO_PRINC = f._principal || Object.keys(f).find(k=>k!=='_principal'&&k!=='_contexto') || null;
ATIVO_CTX = f._contexto || null;
}
const PR = ATIVO_PRINC;
if(s && PR) {
const w = s[PR] || {};
const si = w.sinal||0, co = w.confianca||0, sc = w.score||0;
const tp = w.tp||100, sl_v = w.sl||50, preco = (f||{})[PR] ? f[PR].preco_fim||0 : 0;
let cor, txt, icone;
const temPos = p && p.lado;
if(temPos){
 if(p.lado=='C'){cor='#1b5e20';txt='COMPRA ABERTA';icone='^'}
 else{cor='#b71c1c';txt='VENDA ABERTA';icone='v'}
} else if(co < 0.70){cor='#333';txt='AGUARDE';icone='-'}
else if(si>0){cor='#1b5e20';txt='COMPRA';icone='^'}
else if(si<0){cor='#b71c1c';txt='VENDA';icone='v'}
else{cor='#333';txt='AGUARDE';icone='-'}
document.getElementById('acao').style.background=cor;
document.getElementById('acao').style.color=si>0?'#4caf50':si<0?'#f44336':'#ff9800';
document.getElementById('acao').textContent=icone+' '+txt;
document.getElementById('preco').textContent=fmt(preco);
const tp_p = si>=0 ? preco+tp : preco-tp;
const sl_p = si>=0 ? preco-sl_v : preco+sl_v;
document.getElementById('tp').textContent=fmt(tp_p);
document.getElementById('sl').textContent=fmt(sl_p);
document.getElementById('risco').textContent=sl_v+' pts';
document.getElementById('retorno').textContent=tp+' pts';
const fw = f ? f[PR]||{} : {};
const ag = fw.aggr_imb||0;
document.getElementById('aggr').textContent=(ag>0?'+':'')+ag.toFixed(3);
document.getElementById('aggr').style.color=ag>0?'#4caf50':ag<0?'#f44336':'#888';
document.getElementById('eff').textContent=(fw.price_eff||0).toFixed(4);
document.getElementById('persist').textContent=((fw.fluxo_persist||0)*100).toFixed(0)+'%';
document.getElementById('hhi').textContent=(fw.hhi||0).toFixed(2);
document.getElementById('negs').textContent=fw.n||0;
const rng=fw.range_estado||'-';
const rngAmp=fw.range_amplitude||0;
const rngTT=fw.range_testes_topo||0;
const rngTF=fw.range_testes_fundo||0;
let rngTxt=rng;
if(rngAmp>0){rngTxt=rng+' ('+rngAmp.toFixed(0)+'pts) T:'+rngTT+' B:'+rngTF;}
const rngEl=document.getElementById('rng');
rngEl.textContent=rngTxt;
if(rng==='topo')rngEl.style.color='#ff9800';
else if(rng==='fundo')rngEl.style.color='#2196f3';
else if(rng==='dentro')rngEl.style.color='#4caf50';
else rngEl.style.color='#888';
// ACUMULACAO
const acumDir=fw.acumulacao_direcao||'-';
const acumF=fw.acumulacao_forca||0;
const acumZ=fw.acumulacao_zona||'-';
const acumIN=fw.acumulacao_inst_net||0;
const acumVN=fw.acumulacao_var_net||0;
const acumEl=document.getElementById('acum');
if(acumDir!=='neutro'&&acumDir!=='-'){
 acumEl.textContent=acumDir.toUpperCase()+' ('+acumF.toFixed(2)+') Z:'+acumZ+' I:'+acumIN+' V:'+acumVN;
 if(acumDir==='cima')acumEl.style.color='#4caf50';
 else acumEl.style.color='#f44336';
} else {
 acumEl.textContent='neutro';
 acumEl.style.color='#888';
}
const absR=fw.absorcao_ratio||0;
const absEl=document.getElementById('abs');
if(absR>0){
 absEl.textContent=absR.toFixed(1);
 if(absR>50)absEl.style.color='#f44336';
 else if(absR>20)absEl.style.color='#ff9800';
 else absEl.style.color='#4caf50';
}else{absEl.textContent='-';absEl.style.color='#888';}
// OFI
const ofiT=fw.ofi_total||0;
const ofiE=fw.ofi_ewma||0;
const ofiEl=document.getElementById('ofi_el');
ofiEl.textContent='T:'+ofiT.toFixed(0)+' E:'+ofiE.toFixed(0);
if(Math.abs(ofiT)>50)ofiEl.style.color='#f44336';
else if(Math.abs(ofiT)>20)ofiEl.style.color='#ff9800';
else ofiEl.style.color='#4caf50';
// REGIME
const regime=fw.regime||'-';
const regimeEl=document.getElementById('regime_el');
regimeEl.textContent=regime;
if(regime==='tendencia_alta'||regime==='tendencia_baixa')regimeEl.style.color='#4caf50';
else if(regime==='vol_alta')regimeEl.style.color='#f44336';
else if(regime==='lateral')regimeEl.style.color='#ff9800';
else if(regime==='vol_baixa')regimeEl.style.color='#888';
else regimeEl.style.color='#888';
const rrr = sl_v>0?(tp/sl_v):0;
const ml=(w.ml_prob||0.5);const mlStr=ml!=0.5?' | ML: '+(ml*100).toFixed(0)+'%':'';
document.getElementById('conf').textContent='Conf: '+(co*100).toFixed(0)+'% | Score: '+(sc>0?'+':'')+sc.toFixed(2)+' | R:R 1:'+rrr.toFixed(1)+mlStr;
document.getElementById('motivos').textContent=(w.motivos||[]).join(', ');
}
if(p && p.lado){
const pb = document.getElementById('pos-box');
pb.style.display='block';
pb.style.background=p.lado=='C'?'#1b5e2033':'#b71c1c33';
const plc = p.pnl>0?'#4caf50':p.pnl<0?'#f44336':'#ff9800';
const ll = p.lado=='C'?'COMPRA':'VENDA';
const tp2=p.entrada+(p.lado=='C'?p.tp:-p.tp);
const sl2=p.entrada+(p.lado=='C'?-p.sl:p.sl);
pb.innerHTML='<div style="color:'+plc+';font-weight:bold">'+ll+' @ '+fmt(p.preco_atual)+'</div>'
+'<div>P&L: <span style="color:'+plc+'">'+(p.pnl>0?'+':'')+fmt(p.pnl)+' pts</span></div>'
+'<div>TP: '+fmt(tp2)+' | SL: '+fmt(sl2)+'</div>'
+'<div>Tempo: '+Math.round(p.duracao_s)+'s</div>';
} else { document.getElementById('pos-box').style.display='none'; }
if(l){
document.getElementById('acc').textContent=l.acertos+'/'+l.total+' = '+(l.acuracia*100).toFixed(0)+'%';
const tb = document.getElementById('log');
tb.innerHTML='';
(l.resultados||[]).slice(-15).reverse().forEach(r=>{
const c=r.acertou?'#4caf50':'#f44336';
const ic=r.acertou?'V':'X';
const ld=r.lado||'V';
tb.innerHTML+='<tr><td style="color:'+c+'">'+ic+'</td><td>'+ld+'</td><td>'+fmt(r.preco_antes)+'</td><td>'+fmt(r.preco_depois)+'</td><td style="color:'+c+'">'+(r.delta>0?'+':'')+fmt(r.delta)+'</td></tr>';
});
const pb2 = document.getElementById('pesos');
pb2.innerHTML='';
Object.entries(l.pesos||{}).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).forEach(([k,v])=>{
const acc2=l.acuracia_por_feature?l.acuracia_por_feature[k]||0:0;
pb2.innerHTML+='<tr><td>'+k+'</td><td>'+v.toFixed(3)+'</td><td>'+(acc2*100).toFixed(0)+'%</td></tr>';
});
}
if(met){
document.getElementById('metricas').innerHTML=
'<div class="metric">Sharpe: '+(met.sharpe||0).toFixed(2)+'</div>'
+'<div class="metric">PF: '+(met.profit_factor||0).toFixed(2)+'</div>'
+'<div class="metric">MaxDD: '+(met.max_drawdown||0).toFixed(0)+' pts</div>'
+'<div class="metric">Expectancy: '+(met.expectancy||0).toFixed(1)+' pts</div>';
}
if(m){
document.getElementById('mem').textContent='Neg: '+fmt(m.total_negocios)+' | Trades dia: '+m.trades_dia+' | PnL: '+(m.pnl_dia||0).toFixed(0);
if(m.circuit_breaker_nivel > 0) document.getElementById('mem').textContent+=' | CB NIVEL ' + m.circuit_breaker_nivel;
}
if(f && ATIVO_CTX && f[ATIVO_CTX]){
const wd=f[ATIVO_CTX];
document.getElementById('wdo').textContent=ATIVO_CTX+' | aggr: '+(wd.aggr_imb||0).toFixed(2)+' preco: '+fmt(wd.preco_fim);
}
// Camada 1: Book Level
if(f && f[ATIVO_PRINC]){
var fp=f[ATIVO_PRINC];
var bl=fp._book_level||{};
document.getElementById('spread_el').textContent=bl.spread!=null?bl.spread:'-';
document.getElementById('imb_l1_el').textContent=bl.imbalance&&bl.imbalance.L1!=null?(bl.imbalance.L1>0?'+':'')+bl.imbalance.L1.toFixed(2):'-';
document.getElementById('micro_el').textContent=bl.microprice!=null?bl.microprice:'-';
document.getElementById('hhi_el').textContent=bl.hhi_book!=null?bl.hhi_book.toFixed(3):'-';
document.getElementById('avg_sz_el').textContent=fp.avg_trade_size||'-';
document.getElementById('seq_el').textContent=fp.seq_pattern!=null?fp.seq_pattern.toFixed(2):'-';
document.getElementById('vel_el').textContent=fp.trades_per_sec||'-';
}
// Camada 4
var bl_data=await fetchJSON('/api/book_level');
if(bl_data&&bl_data[ATIVO_PRINC]){
var ca=bl_data[ATIVO_PRINC].cross_asset||{};
document.getElementById('lag_el').textContent=ca.lag_ms?ca.lag_ms+'ms':'-';
document.getElementById('corr_el').textContent=ca.corr_aggr!=null?(ca.corr_aggr>0?'+':'')+ca.corr_aggr.toFixed(2):'-';
document.getElementById('div_el').textContent=ca.divergencia!=null?(ca.divergencia>0?'+':'')+ca.divergencia.toFixed(2):'-';
}
const pd = await fetchJSON('/api/padroes');
const sc = await fetchJSON('/api/saldo_corretoras');
if(pd){
let h='';
if(pd.top_spoofers&&pd.top_spoofers.length){
 h+='<b>Liquidez removida:</b> ';
 pd.top_spoofers.forEach(s=>{h+=s.broker+' ('+s.spoofs+'x, '+Math.round(s.conf*100)+'%) '});
 h+='<br>';
}
if(pd.niveis_stop&&pd.niveis_stop.length){
 h+='<b>Stops:</b> ';
 pd.niveis_stop.forEach(n=>{h+=n.nivel+' ('+n.tipo+', '+n.vezes_testado+'x) '});
 h+='<br>';
}
if(pd.hunts_ultimos_10min&&pd.hunts_ultimos_10min.length){
 h+='<b>Hunts (10min):</b> ';
 pd.hunts_ultimos_10min.forEach(hh=>{h+=hh.tipo+' @ '+hh.nivel+' '});
}
if(!h) h='Nenhum padrao detectado';
document.getElementById('padroes').innerHTML=h;
}
if(sc){
const th='<tr><th>Corretora</th><th>Comp</th><th>Vend</th><th>Saldo</th></tr>';
let winHtml='<table style="width:100%;font-size:0.75em">'+th;
let wdoHtml='<table style="width:100%;font-size:0.75em">'+th;
for(const [sym, lista] of Object.entries(sc)){
 const isWin = sym.indexOf('WIN')>=0;
 lista.forEach(c=>{
  const cor=c.saldo>0?'#4caf50':c.saldo<0?'#f44336':'#888';
  const row='<tr><td>'+c.label+'</td><td style="color:#4caf50">'+c.comprado+'</td><td style="color:#f44336">'+c.vendido+'</td><td style="color:'+cor+'">'+(c.saldo>0?'+':'')+c.saldo+'</td></tr>';
  if(isWin) winHtml+=row; else wdoHtml+=row;
 });
}
winHtml+='</table>';wdoHtml+='</table>';
document.getElementById('saldo_win').innerHTML=winHtml;
document.getElementById('saldo_wdo').innerHTML=wdoHtml;
}
// RTD Health check
var rh=await fetchJSON('/api/rtd_health');
// v9.32: Contexto de mercado (ajuste oficial + VWAP intraday)
var ctx=await fetchJSON('/api/contexto');
var rtdAlert=document.getElementById('rtd_alert');
if(rh && !rh._conexao_ok){
 rtdAlert.textContent='\u26a0 RTD DESCONECTADO - Verifique o ProfitChart';
 rtdAlert.style.display='block';
 rtdAlert.style.background='#f44336';
} else if(rh && rh._pre_abertura){
 rtdAlert.textContent='\u23f0 Pre-abertura (8:45-9:00) - Aguardando mercado...';
 rtdAlert.style.display='block';
 rtdAlert.style.background='#ff9800';
} else if(rh){
 var temSemDados=false; var msgs=[];
 for(var k in rh){if(k.startsWith('_'))continue; var v=rh[k]; if(v.sem_dados){temSemDados=true; msgs.push(k+': sem dados '+v.tempo_sem_trade+'s');}}
 if(temSemDados){
  rtdAlert.textContent='\u26a0 SEM DADOS RTD: '+msgs.join(' | ')+' - ProfitChart nao esta transmitindo';
  rtdAlert.style.display='block';
  rtdAlert.style.background='#f44336';
 } else {
  rtdAlert.style.display='none';
 } }
}
setInterval(update, 1000);
update();
</script>
</body></html>'''

    def _reconectar(self):
        agora = time.time()
        # Backoff simples: nao tenta reconectar mais de uma vez a cada 10s
        if agora - getattr(self, '_ultima_reconexao', 0) < 10:
            return False
        self._ultima_reconexao = agora
        # Circuit breaker: o COM nao se recupera in-process (o ServerStart
        # aninhado deixa de entregar dados e o loop entra em [COM-WATCHDOG]
        # Hang -> Reconecta sem capturar dados). Apos N tentativas em pouco
        # tempo, encerramos o processo para o watchdog reiniciar LIMPO
        # (re-init do COM do zero) — unica forma confiavel de restaurar dados.
        if not hasattr(self, '_reconexoes_recentes'):
            self._reconexoes_recentes = []
        self._reconexoes_recentes = [t for t in self._reconexoes_recentes if agora - t < 180]
        self._reconexoes_recentes.append(agora)
        # v9.35: so exit por reconexoes frequentes se ja recebeu dados antes.
        # Antes do 1o trade, reconexoes sao normais (RTD pode estar arrumando).
        _ja_dados = any(e.neg_detectados > 0 for e in self.estados.values())
        if len(self._reconexoes_recentes) > 5 and _ja_dados:
            log.error('[COM] %d reconexoes em <180s apos receber dados — encerrando para reinicio limpo',
                      len(self._reconexoes_recentes))
            sys.exit(1)
        elif len(self._reconexoes_recentes) > 5 and not _ja_dados:
            log.info('[COM] %d reconexoes em <180s mas ainda sem dados — nao exitando (RTD pode nao estar pronto)' %
                     len(self._reconexoes_recentes))
        log.warning("Tentando reconectar ao RTD...")
        try:
            if self._srv:
                try:
                    self._srv.ServerTerminate()
                except Exception:
                    pass
                self._srv = None
            self._srv, self._book_map, self._tt_map = conectar_e_descobrir()
            if not self._book_map and not self._tt_map:
                log.error("Sem janelas RTD. ProfitChart aberto?")
                self._conexao_ok = False
                return False
            self._sync_estados()
            self._topic_map = assinar_topicos(self._srv, self._book_map, self._tt_map)
            self._conexao_ok = True
            # Reseta saude para nao disparar o watchdog de "sem dados" em cascata
            self.ultimo_neg_tempo = time.time()
            self.ultimo_book_tempo = time.time()
            self._loop_beat = time.time()
            self._com_stuck = False
            log.info("Reconexão bem-sucedida")
            return True
        except Exception as e:
            log.error("Falha na reconexão: %s", e)
            self._conexao_ok = False
            return False

    def _sync_estados(self):
        for sym in set(self._book_map.values()) | set(self._tt_map.values()):
            if sym not in self.estados:
                self.estados[sym] = EstadoAtivo(sym)

    def run(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        self.analise.carregar_aprendizado(SAVE_DIR)
        
        def persistence_worker():
            while not shutdown.is_set():
                self.analise._flush_trades()
                self.analise._flush_decisoes()
                time.sleep(0.5)
        threading.Thread(target=persistence_worker, daemon=True).start()
        
        try:
            self._srv, self._book_map, self._tt_map = conectar_e_descobrir()
            if not self._book_map and not self._tt_map:
                log.error("Sem janelas RTD. ProfitChart aberto?")
                return
            self._sync_estados()
            self._topic_map = assinar_topicos(self._srv, self._book_map, self._tt_map)
            self._conexao_ok = True
        except Exception as e:
            log.error("Falha na conexão inicial: %s", e)
            self._conexao_ok = False
        
        Handler.app = self
        server = ThreadingHTTPServer((CONFIG["web_host"], CONFIG["web_port"]), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        
        log.info(f"[OK] Dashboard v6: http://{CONFIG['web_host']}:{CONFIG['web_port']}/")
        self._loop()

    def _loop(self):
        import comtypes.client
        ultimo_save = time.time()
        falhas = 0
        ultimo_preco_check = 0
        ultimo_backlog_log = 0
        ultimo_captura_log = 0
        ultimo_scorer_log = 0
        # B1/B2: COM watchdog via thread separada — detecta hang real
        self._loop_beat = time.time()
        self._com_stuck = False
        _com_timeout = 15.0  # segundos sem resposta = COM travou
        def _com_watchdog():
            """Thread separada: se _loop_beat nao atualiza em _com_timeout s, seta flag."""
            while not shutdown.is_set():
                time.sleep(5.0)
                elapsed = time.time() - self._loop_beat
                if elapsed > _com_timeout and not self._com_stuck:
                    self._com_stuck = True
                    log.error('[COM-WATCHDOG] Thread detectou hang: %.1fs sem resposta — flag setada', elapsed)
                    ERROS_GLOBAIS['com_watchdog'] = ERROS_GLOBAIS.get('com_watchdog', 0) + 1
        threading.Thread(target=_com_watchdog, daemon=True, name='com-watchdog').start()
        while not shutdown.is_set():
            try:
                comtypes.client.PumpEvents(0.005)
                self._loop_beat = time.time()
                time.sleep(0.001)

                if self._conexao_ok and self._srv:
                    try:
                        data = mw._refresh(self._srv)
                        falhas = 0
                        novos = processar_dados(self._topic_map, data, self.estados)
                        if novos:
                            try:
                                fila_eventos.put_nowait(novos)
                            except queue.Full:
                                ERROS_GLOBAIS['fila_eventos_cheia'] += 1

                            if self.captura:
                                self.captura.registrar_negocios(novos)
                    except Exception:
                        falhas += 1
                        ERROS_GLOBAIS['refresh_rtd'] += 1
                        if falhas >= CONFIG["max_refresh_falhas"]:
                            log.warning("Muitas falhas, reconectando...")
                            self._reconectar()
                            falhas = 0
                    # v9.9: ritmo adaptativo — com dados na fila o ciclo gira
                    # rápido (50Hz+); com mercado parado dorme mais (evita
                    # hot-loop). Remove o gargalo de 20Hz do sleep fixo.
                    if novos or fila_eventos.qsize() > 0:
                        time.sleep(0.002)
                    else:
                        time.sleep(0.05)
                # B1/B2: check da flag do watchdog thread
                if self._com_stuck:
                    log.error('[COM-WATCHDOG] Hang detectado pela thread — reconectando')
                    self._com_stuck = False
                    self._reconectar()
                    self._loop_beat = time.time()
                    falhas = 0
                if not self._conexao_ok:
                    if time.time() - self._ultima_reconexao > 5:
                        self._ultima_reconexao = time.time()
                        self._reconectar()
                    time.sleep(0.1)

                # Worker processa fila
                try:
                    while True:
                        novos = fila_eventos.get_nowait()
                        self.analise.alimentar_lote(novos)
                        # Alimentar ML scorer (v9.13: passa COMP + VEND — antes
                        # só 6 dos 7 campos chegavam e o scorer morria)
                        if self.analise.scorer:
                            agora_epoch_ms = int(time.time() * 1000)
                            agora_tod_ms = _tod_ms()
                            offset_epoch = agora_epoch_ms - agora_tod_ms
                            for neg in novos:
                                tms_epoch = offset_epoch + neg[1]  # TOD -> epoch
                                self.analise.scorer.evento(neg[0], tms_epoch, neg[2], neg[3], neg[4], neg[5], neg[6])
                except queue.Empty:
                    pass

                # v9.19: observabilidade do ML scorer - falhas nunca
                # ficam silenciosas (bug P0-5: scorer morto por dias)
                if self.analise.scorer and self.analise.scorer.fallos:
                    if time.time() - ultimo_scorer_log > 300:
                        ultimo_scorer_log = time.time()
                        est = self.analise.scorer.estado_salud()
                        log.error('[ML] scorer com %d falhas (ultimo erro: %s) - prob caindo para 0.5', est['fallos'], est['ultimo_error'])

                # v9.9: rede de segurança — backlog grande = processador atrás
                # do leitor RTD (perda iminente de contexto se persistir)
                qsz = fila_eventos.qsize()
                if qsz > 2000 and time.time() - ultimo_backlog_log > 30:
                    ultimo_backlog_log = time.time()
                    log.warning(f"[PIPE] backlog da fila: {qsz} lotes — processamento atrás do RTD")

                # v9.10: saúde da captura — log periódico dos rejeitados
                # (a captura pode parar/descartar silenciosamente; os contadores
                # de blindagem só têm valor se alguém olha para eles)
                if time.time() - ultimo_captura_log > 600:
                    ultimo_captura_log = time.time()
                    if self.captura:
                        rej = self.captura.stats()
                        if any(rej.values()):
                            log.warning(f"[CAPTURA] rejeitados acumulados: {rej}")
                        else:
                            log.info("[CAPTURA] saudável (0 rejeitados)")

                # Snapshot do book (comparação com anterior)
                agora = time.time()
                for sym, estado in self.estados.items():
                    # Só tira snapshot se book mudou OU keepalive (30s)
                    book_mudou = any(estado.book_bid[i] for i in range(CONFIG["book_split"])) or \
                                 any(estado.book_ask[i] for i in range(CONFIG["book_split"]))
                    tempo_sem_snap = agora - estado.book_ultimo_t
                    is_keepalive = (not book_mudou) and tempo_sem_snap >= 30.0
                    if not book_mudou and not is_keepalive:
                        continue
                    if book_mudou and tempo_sem_snap < 0.25:
                        continue

                    snap, bv, av = snapshot_book(estado)
                    # Atualiza OFI (via features_lib — mesma conta do treino)
                    ofi_tracker = self.analise.trackers[sym]['ofi']
                    bid_levels_ofi, ask_levels_ofi = extrair_niveis_book(estado, ofi_tracker.niveis)
                    ofi_tracker.atualizar(bid_levels_ofi, ask_levels_ofi)
                    # Extrair TODOS os niveis do book para captura (250 por lado)
                    all_bid_levels, all_ask_levels = extrair_niveis_book(estado, CONFIG['book_split'])
                    # Hash incremental: soma vol*preco por lado (muito mais rapido que tuple(sorted))
                    chave = (bv, av, tuple((b, s.get('bid_vol',0), s.get('ask_vol',0)) for b, s in snap.items()))
                    if chave != estado.book_ultimo_snap or is_keepalive:
                        estado.book_ultimo_snap = chave
                        estado.book_ultimo_t = agora
                        estado.ultimo_book_tempo = agora
                        self.analise.alimentar_book(sym, snap, bv, av, ofi_tracker.get_ofi(), estado=estado)
                        # Alimentar ML scorer com book
                        if self.analise.scorer:
                            book_snap = extrair_book_snapshot(estado)
                            self.analise.scorer.book(sym, int(agora * 1000), book_snap)
                    if self.captura:
                        levels = {
                            'bid_preco': [b[0] for b in all_bid_levels] if all_bid_levels else [],
                            'bid_vol': [b[1] for b in all_bid_levels] if all_bid_levels else [],
                            'ask_preco': [a[0] for a in all_ask_levels] if all_ask_levels else [],
                            'ask_vol': [a[1] for a in all_ask_levels] if all_ask_levels else [],
                            'ofi': ofi_tracker.get_ofi(),
                        }
                        self.captura.registrar_book(sym, int(agora * 1000), snap, bv, av, levels=levels)

                # Verificação de saídas em tempo real
                if agora - ultimo_preco_check >= 0.25:
                    ultimo_preco_check = agora
                    result = self.analise.verificar_saidas_tempo_real()
                    if result and result.get('acao') == 'FECHOU':
                        log.info("Posição fechada: %s", result)
                # B3: Reconexao periodica — verifica ultimo trade por ativo (nao acumulativo)
                if self._conexao_ok and self._srv:
                    agora_check = time.time()
                    # v9.35: deteccao baseada em DADOS, nao em horario.
                    # So reconecta se ja RECEBEU pelo menos 1 trade antes.
                    # Antes do 1o trade, o RTD pode simplesmente nao estar pronto
                    # (pregao abre 9:00, 9:02, 9:15 — nao sabemos).
                    _ja_recebeu_dados = any(
                        _est.neg_detectados > 0 for _est in self.estados.values()
                    )
                    if _ja_recebeu_dados:
                        sem_dados = False
                        for _sym, _est in self.estados.items():
                            _dt = agora_check - _est.ultimo_neg_tempo
                            if _dt > 300:  # 5min sem dados depois de ja ter recebido
                                sem_dados = True
                                break
                        if sem_dados and agora_check - ultimo_save > 30:
                            log.warning('[WATCHDOG] Ja recebeu dados mas parou ha 5min — reconectando...')
                            self._reconectar()
                    else:
                        # Ainda nao recebeu nenhum trade — aguardar pacientemente
                        if not getattr(self, '_logou_aguardando', False):
                            log.info('[WATCHDOG] Aguardando primeiro trade do RTD...')
                            self._logou_aguardando = True

                # Salvamento periodico
                if time.time() - ultimo_save >= CONFIG["save_intervalo"]:
                    ultimo_save = time.time()
                    self.analise.salvar_sessao()
                    if self.captura:
                        self.captura.flush()
            except Exception as _e:
                log.exception(f"[LOOP] Erro nao tratado: {_e}")
                ERROS_GLOBAIS['loop_crash'] += 1
                time.sleep(1)

    def get_rtd_health(self):
        """Verifica se RTD esta transmitindo dados."""
        agora = time.time()
        now = datetime.now()
        hm = (now.hour, now.minute)
        # Pre-abertura: 8:45-9:00 (mercado nao comecou, sem trades e normal)
        pre_abertura = (8, 45) <= hm < (9, 0)
        resultado = {}
        for sym, estado in self.estados.items():
            tempo_sem_neg = agora - estado.ultimo_neg_tempo
            tempo_sem_book = agora - estado.ultimo_book_tempo
            # Em pre-abertura, so alerta se book tambem parou (>30s)
            if pre_abertura:
                sem_dados = tempo_sem_book > 30  # book atualiza mesmo em leilao
            else:
                sem_dados = tempo_sem_neg > 15 and tempo_sem_book > 15
            resultado[sym] = {
                'tempo_sem_trade': round(tempo_sem_neg),
                'tempo_sem_book': round(tempo_sem_book),
                'sem_dados': sem_dados,
                'neg_detectados': estado.neg_detectados,
            }
        resultado['_conexao_ok'] = self._conexao_ok
        resultado['_pre_abertura'] = pre_abertura
        return resultado

    def get_contexto_mercado(self):
        """v9.32: contexto de mercado para o dashboard.

        Retorna para cada ativo:
          - vwap: VWAP intraday causal
          - dist_vwap_pts: preco - vwap
          - acima_vwap / abaixo_vwap: flags
          - cruzou_vwap: evento binario
          - vol_total: volume acumulado
          - ajuste_anterior_oficial: ajuste de D-1
          - dist_ajuste_oficial_pts: preco - ajuste
          - dist_ajuste_oficial_norm: dist / vol
          - acima_ajuste_oficial / abaixo_ajuste_oficial
        """
        resultado = {}
        for sym in list(self.estados.keys()):
            entry = {}
            # VWAP do scorer
            if (self.analise.scorer and
                    hasattr(self.analise.scorer, 'vwaps') and
                    sym in self.analise.scorer.vwaps):
                entry.update(self.analise.scorer.vwaps[sym].snapshot())
            # Ajuste anterior oficial do scorer
            if (self.analise.scorer and
                    hasattr(self.analise.scorer, 'ajuste_anterior_oficial')):
                adj = self.analise.scorer.ajuste_anterior_oficial.get(sym)
                entry['ajuste_anterior_oficial'] = adj
                # calcular distâncias se tivermos preco
                if adj is not None and not (isinstance(adj, float) and math.isnan(adj)):
                    preco_ult = self.estados[sym].preco_ultimo if hasattr(
                        self.estados[sym], 'preco_ultimo') else 0
                    if preco_ult > 0:
                        entry['dist_ajuste_oficial_pts'] = preco_ult - adj
                        vol_ref = entry.get('vol_total', 0)
                        if vol_ref > 0:
                            entry['dist_ajuste_oficial_norm'] = (
                                preco_ult - adj) / vol_ref
                        entry['acima_ajuste_oficial'] = float(preco_ult > adj)
                        entry['abaixo_ajuste_oficial'] = float(preco_ult < adj)
            # Preço atual
            if hasattr(self.estados[sym], 'preco_ultimo'):
                entry['preco_ultimo'] = self.estados[sym].preco_ultimo
            resultado[sym] = entry
        return resultado

    def parar(self):
        shutdown.set()
        self.analise.salvar_sessao(final=True)
        if self.captura:
            self.captura.fechar()

if __name__ == "__main__":
    app = App()
    signal.signal(signal.SIGINT, lambda s, f: app.parar())
    signal.signal(signal.SIGTERM, lambda s, f: app.parar())
    app.run()