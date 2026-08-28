# -*- coding: utf-8 -*-
"""
core/app.py — Orquestrador principal com loop RTD completo.

v10.0 — Migração completa do loop RTD de motor_rt_alphaz.py.

Estrutura:
  App
  ├── MarketState        (estado de mercado)
  ├── SignalEngine       (features + scoring)
  ├── PositionManager    (abrir/fechar posições)
  ├── RiskManager        (circuit breaker, TP/SL, horário)
  ├── RegimeDetector     (regime de mercado)
  ├── Learning           (pesos, MFE/MAE)
  ├── Persistence        (trades, decisões, checkpoints)
  ├── Metrics            (acuracia, PF, Sharpe)
  ├── EventClock          (relógio mestre)
  ├── FileStorage        (captura bruta JSONL)
  ├── ScorerML           (ML, opcional)
  └── Loop RTD            (PumpEvents, RefreshData, reconexão)
"""

import os
import sys
import time
import math
import queue
import signal
import logging
import threading
from collections import defaultdict
from datetime import datetime, date

log = logging.getLogger(__name__)
from adapters.base import MarketDataSource
# v10.2: Helper centralizado para resolver o shadow import de config.py na raiz
def _load_root_config():
    import importlib.util
    from pathlib import Path
    root_path = Path(__file__).resolve().parent.parent
    config_py = root_path / "config.py"
    if config_py.exists():
        spec = importlib.util.spec_from_file_location("root_config", str(config_py))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None

from adapters.com_watchdog import COMHeartbeatMonitor
from adapters.file_storage import flush_buffers_with_retry
from core.contracts import MarketEvent, TradeEvent, BookSnapshot, Signal
from core.market_state import (
    MarketState, EstadoAtivo, extrair_book_snapshot, comparar_books,
    extrair_niveis_book, snapshot_book, check_staleness
)
from core.persistence import Persistence
from core.metrics import Metrics
from core.event_clock import EventClock
from core.regime_detector import RegimeDetector
from core.learning import Learning
from core.risk_manager import RiskManager
from core.position_manager import PositionManager
from core.signal_engine import SignalEngine
from features.feature_engine import FeatureEngine
from core.utils import fnum, fint, sstr, parse_hms_ms, tod_ms
from adapters.dashboard_server import DashboardServer

from adapters.file_storage import FileStorage, CapturaEventosMS

ERROS_GLOBAIS = defaultdict(int)


class App:
    """Orquestrador principal com loop RTD completo."""

    def __init__(self, data_source: MarketDataSource, config=None):
        # v10.13: Sistema de Injeção de Dependência de Configuração
        if config:
            self.config = config
        else:
            _cfg_mod = _load_root_config()
            self.config = _cfg_mod.get_config_dict() if _cfg_mod else {}

        self.data_source = data_source
        self.save_dir = self.config.get('save_dir', 'D:\\MarketData\\mimo')
        self.ativo_principal = self.config.get('ativo_principal', 'WINV26')
        self.ativo_contexto = self.config.get('ativo_contexto', 'WDOU26')
        self.session_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.tempo_inicio = time.time()
        self.revision = 0
        self._shutdown = threading.Event()

        # ---- Composição de módulos core/ ----
        self.clock = EventClock()
        self.market_state = MarketState(config=self.config, base_dir=self.save_dir)
        self.persistence = Persistence(self.save_dir, self.session_ts)
        self.learning = Learning(config=self.config)
        self.regime = RegimeDetector(config=self.config)
        self.risk = RiskManager(config=self.config)
        self.feature_engine = FeatureEngine(self.market_state, config=self.config)
        self.position = PositionManager(
            self.risk, self.persistence, self.learning,
            config=self.config, ativo_principal=self.ativo_principal
        )
        self.signal = SignalEngine(
            self.market_state, self.learning, self.regime,
            self.feature_engine, risk=self.risk,
            config=self.config, ativo_principal=self.ativo_principal,
            ativo_contexto=self.ativo_contexto
        )
        self.metrics = Metrics(
            resultados=self.learning.resultados,
            previsoes=self.learning.previsoes,
            pesos=self.learning.pesos,
            feature_hits=self.learning.feature_hits,
            acuracia=self.learning.acuracia,
        )
        self.captura = FileStorage(self.save_dir, self.session_ts)
        self.dashboard = DashboardServer(
            self,
            host=self.config.get('web_host', '127.0.0.1'),
            port=self.config.get('web_port', 5001)
        )

        # Restaurar checkpoint de posição
        pos = self.persistence.carregar_checkpoint()
        if pos:
            self.position.posicao = pos

        # Carregar aprendizado
        self.learning.carregar(self.save_dir)

        # ML Scorer (opcional)
        self.scorer = None
        self._carregar_scorer()

        self.latencia_atual_ms = 0.0
        self.eventos_processados = 0
        log.info(f"[APP] Inicializado: {self.ativo_principal} × {self.ativo_contexto}")

    def _carregar_scorer(self):
        modelo_path = self.config.get('ml_modelo', '')
        if not modelo_path or not os.path.exists(modelo_path):
            log.info('[ML] Sem modelo treinado — usando apenas heuristica')
            return
        try:
            import scorer
            ScorerML = scorer.ScorerML
            ativos = [self.ativo_principal]
            if self.ativo_contexto:
                ativos.append(self.ativo_contexto)
            self.scorer = ScorerML(modelo_path, ativos)
            self.signal.scorer = self.scorer
            log.info(f'[ML] Scorer carregado: {modelo_path}')
        except Exception as e:
            log.warning(f'[ML] Falha ao carregar modelo: {e}')

    # ---- Loop principal ----

    def run(self):
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

        def persistence_worker():
            while not self._shutdown.is_set():
                self.persistence.flush()
                time.sleep(0.5)
        threading.Thread(target=persistence_worker, daemon=True).start()

        if not self.data_source.connect():
            log.error("[APP] Falha ao conectar à fonte de dados.")
            return

        self.dashboard.start()

        self._loop()

    def _loop(self):
        ultimo_save = time.time()
        ultimo_preco_check = 0
        ultimo_captura_log = 0
        ultimo_scorer_log = 0

        # Consome stream agnóstica de eventos (Fase 5)
        for event in self.data_source.events():
            if self._shutdown.is_set():
                break
            
            self.revision += 1
            t0 = time.perf_counter()
            
            try:
                self._handle_market_event(event)
                self._loop_beat = time.time()

                agora = time.time()

                # Observabilidade do scorer
                if self.scorer and getattr(self.scorer, 'fallos', 0):
                    if time.time() - ultimo_scorer_log > 300:
                        ultimo_scorer_log = time.time()
                        est = self.scorer.estado_salud()
                        log.error('[ML] scorer %d falhas (erro: %s)', est['fallos'], est.get('ultimo_error'))


                # Saúde da captura
                if time.time() - ultimo_captura_log > 600:
                    ultimo_captura_log = time.time()
                    if self.captura:
                        rej = self.captura.stats()
                        if any(rej.values()):
                            log.warning(f"[CAPTURA] rejeitados: {rej}")
                        else:
                            log.info("[CAPTURA] saudável (0 rejeitados)")

                # Verificar saídas (intervalo de 250ms)
                if agora - ultimo_preco_check >= 0.25:
                    ultimo_preco_check = agora
                    result = self.position.checar_saidas(
                        self.market_state.obter_ultimo_preco(self.ativo_principal, self.signal.features))
                    if result:
                        log.info("Posição fechada: %s", result)

                # Salvamento periódico
                if time.time() - ultimo_save >= self.config.get('save_intervalo', 60):
                    ultimo_save = time.time()
                    self.salvar_sessao()
                    if self.captura:
                        self.captura.flush()
                
                # Fase 7: Registro de latência para o Dashboard
                self.latencia_atual_ms = (time.perf_counter() - t0) * 1000
                self.eventos_processados += 1
                
                if self.latencia_atual_ms > 50: 
                    log.warning(f"[LATENCIA] Loop lento: {self.latencia_atual_ms:.2f}ms para evento {event.type}")

            except Exception as _e:
                log.exception(f"[LOOP] Erro: {_e}")
                ERROS_GLOBAIS['loop_crash'] += 1
                time.sleep(1)

    def _handle_market_event(self, event: MarketEvent):
        """Processa um evento normalizado através do pipeline de trading (Causal)."""
        if event.type == 'TRADE':
            trade: TradeEvent = event.payload
            # 1. Alimentar market_state (Domínio)
            self.market_state.alimentar_negocio(trade)
            
            # 2. Gravação de captura bruta (Infra)
            self.captura.registrar_negocios([(
                trade.symbol, trade.timestamp_ms, trade.price, trade.quantity,
                trade.aggressor, trade.buyer, trade.seller
            )])

            # 3. Calcular features (Lógica)
            seg = trade.timestamp_ms // 1000
            sig: Signal = self.signal.calcular(seg, skip_avaliar=False)
            
            # 4. Acoplamento com PositionManager (Execução)
            if sig and trade.symbol == self.ativo_principal:
                # v10.15: Utiliza o objeto Signal tipado
                self.position.confianca_ewma = sig.confianca
                
                # v10.21: Injeção de decisão de risco no fluxo de execução
                res_recentes = self.learning.resultados if self.learning else []
                decision = self.risk.pode_abrir(sig, res_recentes)

                self.position.gerenciar(
                    ativo=trade.symbol,
                    signal=sig,
                    preco=trade.price,
                    decision=decision,
                    regime=self.signal.features.get(trade.symbol, {}).get('regime')
                )

            # 4. Alimentar Scorer ML (Inferência)
            if self.scorer:
                self.scorer.evento(trade.symbol, trade.timestamp_ms, trade.price, 
                                  trade.quantity, trade.aggressor, trade.buyer, trade.seller)

        elif event.type == 'BOOK':
            snapshot: BookSnapshot = event.payload
            
            # 1. Alimentar market_state (Lógica de BookLevelFeatures)
            self.market_state.alimentar_book(snapshot)
            
            # 2. Gravação de captura bruta (Opcional no paralelo)
            # self.captura.registrar_book(snapshot.symbol, snapshot.timestamp_ms, ...)
            
            # 3. Alimentar Scorer ML com o Book
            if self.scorer:
                self.scorer.book(snapshot.symbol, snapshot.timestamp_ms, snapshot)

    # ---- Getters para o dashboard ----

    def get_rtd_health(self) -> dict:
        """Retorna o status de saúde da fonte de dados."""
        if not self.data_source:
            return {'status': 'error', 'motivo': 'sem fonte de dados'}
        # Refatorar: App deve pedir o status ao data_source agora
        return self.data_source.get_health()

    def get_contexto_mercado(self):
        resultado = {}
        # Usa os símbolos ativos no MarketState
        for sym in list(self.market_state.stats.keys()):
            entry = {}
            if self.scorer and hasattr(self.scorer, 'vwaps') and sym in self.scorer.vwaps:
                entry.update(self.scorer.vwaps[sym].snapshot())
            if self.scorer and hasattr(self.scorer, 'ajuste_anterior_oficial'):
                adj = self.scorer.ajuste_anterior_oficial.get(sym)
                entry['ajuste_anterior_oficial'] = adj
                if adj is not None and not (isinstance(adj, float) and (adj != adj)):
                    preco_ult = self.market_state.obter_ultimo_preco(sym)
                    if preco_ult > 0:
                        entry['dist_ajuste_oficial_pts'] = preco_ult - adj
                        entry['acima_ajuste_oficial'] = float(preco_ult > adj)
                        entry['abaixo_ajuste_oficial'] = float(preco_ult < adj)
            entry['preco_ultimo'] = self.market_state.obter_ultimo_preco(sym)
            resultado[sym] = entry
        return resultado

    # ---- Propriedades para compatibilidade com DashboardAPI ----

    @property
    def analise(self):
        """Compatibilidade: delega para self (DashboardAPI usa app.analise.*)."""
        return _AnaliseShim(self)

    def html(self):
        """HTML legado (fallback se dashboard_pro.html não existir)."""
        return '<html><body><h1>Motor v10.0</h1><p>Dashboard em dashboard_pro.html</p></body></html>'

    # ---- Shutdown ----

    def salvar_sessao(self, final=False):
        self.persistence.salvar_sessao(
            final=final,
            salvar_aprendizado=self.learning.salvar,
            padroes=self.market_state.padroes,
        )
        if self.captura:
            self.captura.flush()
            if final:
                self.captura.fechar()

    def parar(self):
        self._shutdown.set()
        self.dashboard.stop()
        self.salvar_sessao(final=True)
        log.info('[APP] Shutdown completo')


class _AnaliseShim:
    """Shim para compatibilidade com DashboardAPI que usa app.analise.*."""

    def __init__(self, app):
        self._app = app

    @property
    def features(self):
        return self._app.signal.features

    @property
    def sinais(self):
        return self._app.signal.sinais

    @property
    def posicao(self):
        return self._app.position.posicao

    @property
    def stats(self):
        return self._app.market_state.stats

    @property
    def trackers(self):
        return self._app.market_state.trackers

    @property
    def padroes(self):
        return self._app.market_state.padroes

    @property
    def scorer(self):
        return self._app.scorer

    def get_features(self):
        return self._app.signal.get_features()

    def get_sinais(self):
        return self._app.signal.get_sinais()

    def get_posicao(self):
        return self._app.position.get_posicao(
            lambda ativo: self._app.market_state.obter_ultimo_preco(ativo, self._app.signal.features))

    def get_estatisticas(self):
        return self._app.metrics.get_estatisticas()

    def get_memoria(self):
        return self._app.market_state.get_memoria(
            circuit_breaker_nivel=self._app.risk.circuit_breaker_nivel,
            trades_dia=self._app.risk.trades_dia,
            pnl_dia=self._app.risk.pnl_dia,
            perdas_consecutivas=self._app.risk.perdas_consecutivas,
            confianca_ewma=self._app.position.confianca_ewma,
            sinal_confirmado=self._app.position.sinal_confirmado,
        )

    def get_book_stats(self):
        return self._app.market_state.get_book_stats()

    def get_book_level(self):
        return self._app.market_state.get_book_level()

    def calcular_metricas(self):
        return self._app.metrics.calcular()

    def get_resumo(self, ativo):
        return self._app.market_state.get_resumo(ativo)

    def get_saldo_corretoras(self, ativo=None):
        return self._app.market_state.get_saldo_corretoras(ativo)

    def get_feature_status(self):
        return self._app.learning.get_feature_status()

    def get_historico(self, segundos=1800):
        return self._app.market_state.get_historico(segundos)


# ============================
# Funções auxiliares de staleness (para compatibilidade de testes)
# ============================
def _sem_dados_por_ativo(est, agora, pre_abertura=False):
    """Verifica se um ativo está sem dados (negócios e book parados).

    Args:
        est: estado do ativo (ultimo_neg_tempo, ultimo_book_tempo, neg_detectados)
        agora: timestamp atual
        pre_abertura: True se estamos no pré-leilão (só book conta)

    Returns:
        True se sem dados por > 30s (pregão) ou > 30s book (pré-abertura)
    """
    tempo_sem_neg = agora - est.ultimo_neg_tempo
    tempo_sem_book = agora - est.ultimo_book_tempo

    if pre_abertura:
        # No pré-leilão só o book importa
        return tempo_sem_book > 30

    # Pregão: book parado > 30s OU ambos parados > 15s
    sem_book = tempo_sem_book > 30
    sem_neg = tempo_sem_neg > 15 and tempo_sem_book > 15
    return sem_book or sem_neg
