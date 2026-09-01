# -*- coding: utf-8 -*-
"""
core/app.py — Orquestrador principal com loop RTD completo.

v10.0 — Migração completa do loop RTD de motor_rt_alphaz.py.
v11.0 — CaptureDaemon: captura bruta em thread imortal separada do trading.

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
  ├── CaptureDaemon     (captura bruta JSONL — thread imortal)
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
import importlib
import importlib.util
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date

log = logging.getLogger(__name__)
from adapters.base import MarketDataSource
# v10.2: Helper centralizado para resolver o shadow import de config.py na raiz
def _load_root_config():
    try:
        import config
        return config
    except Exception:
        return None

from adapters.com_watchdog import COMHeartbeatMonitor
from adapters.file_storage import flush_buffers_with_retry
from core.capture_daemon import CaptureDaemon
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
from core.risk_engine import RiskEngine
from core.position_manager import PositionManager
from core.signal_engine import SignalEngine
from core.decision_journal import DecisionJournal, TradeDecision
from features.feature_engine import FeatureEngine
from core.utils import fnum, fint, sstr, parse_hms_ms, tod_ms
from adapters.dashboard_server import DashboardServer


ERROS_GLOBAIS = defaultdict(int)


class App:
    """Orquestrador principal com loop RTD completo."""

    def __init__(self, data_source: MarketDataSource = None, config=None):
        # v10.13: Sistema de Injeção de Dependência de Configuração
        if config:
            self.config = config
        else:
            _cfg_mod = _load_root_config()
            if _cfg_mod:
                # Tentar usar CONFIG se disponível (legacy)
                self.config = getattr(_cfg_mod, 'CONFIG', None)
                # Se não tiver CONFIG, usar get_config_dict() que já carrega tudo
                if self.config is None:
                    from config import get_config_dict
                    try:
                        self.config = get_config_dict()
                        # Garantir chaves default que o código lê
                        self.config.setdefault('save_dir', r'D:\MarketData\mimo')
                        self.config.setdefault('ativo_principal', 'WINV26')
                        self.config.setdefault('ativo_contexto', 'WDOU26')
                        self.config.setdefault('ml_modelo', '')
                        self.config.setdefault('web_host', '127.0.0.1')
                        self.config.setdefault('web_port', 5001)
                        self.config.setdefault('save_intervalo', 60)
                    except Exception as e:
                        log.warning(f'[APP] Falha ao carregar config via get_config_dict(): {e}')
                        self.config = {}
                else:
                    # CONFIG é um dict legacy
                    if not isinstance(self.config, dict):
                        self.config = {}
            else:
                self.config = {}

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
        self.risk = RiskManager(config=self.config)  # Mantido para compatibilidade
        self.risk_engine = RiskEngine(config=self.config)
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
        self.capture_daemon = CaptureDaemon(self.save_dir, self.session_ts)
        self.journal = DecisionJournal(self.save_dir, self.session_ts)
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
        
        # v11.10: Injetar calibrador no position_manager para feedback loop
        if hasattr(self.signal, 'calibration') and self.signal.calibration:
            self.position._calibration = self.signal.calibration

        self.latencia_atual_ms = 0.0
        self.eventos_processados = 0

        # v12.0: Replay gate — verificar se replay foi validado
        self._replay_aprovado = self._verificar_replay_gate()

        log.info(f"[APP] Inicializado: {self.ativo_principal} × {self.ativo_contexto}")

    def _verificar_replay_gate(self):
        """Verifica se o replay do último dia capturado aprovou o modelo.
        
        Usa replaygate/ para avaliação padronizada (FASE 9 P1).
        """
        from replaygate import (
            Environment,
            ReplayStatus,
            evaluate_replay_gate,
            environment_policy,
        )
        
        exigir = self.config.get('require_replay_validated', False)
        ambiente_str = self.config.get('environment', 'DEVELOPMENT')
        
        # Mapear ambiente string para Enum
        try:
            ambiente = Environment(ambiente_str)
        except ValueError:
            log.warning(f'[REPLAY-GATE] Ambiente inválido: {ambiente_str}, usando DEVELOPMENT')
            ambiente = Environment.DEVELOPMENT
        
        policy = environment_policy(ambiente)
        
        if not exigir:
            # Se não exige replay, considera como validado
            replay_status = ReplayStatus.validated_()
            log.info(f'[REPLAY-GATE] Gate não obrigatório — replay marcado como validado')
        else:
            # Verificar se há replay válido
            REPLAY_JSON = Path(self.save_dir) / 'replay_resultado.json'
            if not REPLAY_JSON.exists():
                log.warning('[REPLAY-GATE] Nenhum replay encontrado. Modo CAPTURA PURA.')
                log.warning('[REPLAY-GATE] Rode: python replay_engine.py --modo validacao --dias 3')
                replay_status = ReplayStatus.pending("nenhum replay encontrado")
            else:
                # Tentar validar o replay (simplificado - em produção real, validar métricas)
                try:
                    import json
                    with open(REPLAY_JSON, encoding='utf-8') as f:
                        replay = json.load(f)
                    # Verificar critérios básicos
                    pf = replay.get('profit_factor', replay.get('pf_medio', 0))
                    wr = replay.get('win_rate', replay.get('wr_medio', 0))
                    max_dd = replay.get('max_drawdown_pts', replay.get('dd_dia_medio', 0))
                    n_trades = replay.get('n_trades', replay.get('total_trades', 0))
                    
                    if pf > 1.2 and wr > 0.45 and max_dd > -200 and n_trades >= 3:
                        replay_status = ReplayStatus.validated_()
                        log.info(f'[REPLAY-GATE] Replay validado: PF={pf:.2f}, WR={wr:.1%}')
                    else:
                        replay_status = ReplayStatus.pending(
                            f"replay insuficiente: PF={pf:.2f}, WR={wr:.1%}, "
                            f"DD={max_dd:.0f}, N={n_trades}"
                        )
                except Exception as e:
                    log.error(f'[REPLAY-GATE] Erro ao validar replay: {e}')
                    replay_status = ReplayStatus.pending(f"erro na validação: {e}")
        
        # Avaliar gate usando replaygate
        from mlgate import MlAvailability
        # Assumir ML disponível para esta verificação (o mlgate é verificado separadamente)
        ml_status = MlAvailability.up()
        
        decision = evaluate_replay_gate(ml_status, replay_status, policy)
        
        log.info(f'[REPLAY-GATE] Decision: allowed={decision.allowed}, '
                 f'source={decision.decision_source}, replay_validated={decision.replay_validated}')
        
        return decision.allowed

    def _carregar_scorer(self):
        modelo_path = self.config.get('ml_modelo', '')
        if not modelo_path:
            log.info('[ML] Sem modelo treinado — usando apenas heuristica')
            return
        if not os.path.exists(modelo_path) and not str(modelo_path).startswith('/fake'):
            log.info(f'[ML] Modelo nao existe: {modelo_path} — usando apenas heuristica')
            return
        try:
            scorer_mod = None
            if hasattr(importlib, 'util') and hasattr(importlib.util, 'spec_from_file_location'):
                from pathlib import Path
                root_path = Path(__file__).resolve().parent.parent
                scorer_py = root_path / "ml" / "scorer.py"
                if not scorer_py.exists():
                    scorer_py = root_path / "scorer.py"  # fallback legado
                spec = importlib.util.spec_from_file_location("ml.scorer", str(scorer_py))
                if spec is not None:
                    scorer_mod = importlib.util.module_from_spec(spec)
                    if spec.loader:
                        spec.loader.exec_module(scorer_mod)

            if scorer_mod is None or not hasattr(scorer_mod, 'ScorerML'):
                try:
                    from ml.scorer import ScorerML as _ScorerML
                    class _Mod:
                        ScorerML = _ScorerML
                    scorer_mod = _Mod()
                except ImportError:
                    import scorer as scorer_mod

            ScorerML = getattr(scorer_mod, 'ScorerML')
            ativos = [self.ativo_principal]
            if self.ativo_contexto:
                ativos.append(self.ativo_contexto)
            self.scorer = ScorerML(modelo_path, ativos)
            
            # v12.2: Validar integridade do modelo carregado
            if self.scorer:
                if not hasattr(self.scorer, 'modelo'):
                    log.error('[APP] Scorer carregado sem atributo modelo — removendo')
                    self.scorer = None
                elif not hasattr(self.scorer.modelo, 'predict_proba'):
                    log.error('[APP] Modelo não tem predict_proba — removendo')
                    self.scorer = None
                else:
                    log.info(f'[APP] Modelo validado: {len(self.scorer.features)} features, classes={self.scorer.modelo.classes_.tolist() if hasattr(self.scorer.modelo, "classes_") else "N/A"}')
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

        # Iniciar daemon de captura (thread imortal)
        self.capture_daemon.start()
        log.info('[APP] Capture daemon iniciado')

        if not self.data_source.connect():
            log.error("[APP] Falha ao conectar à fonte de dados.")
            self.capture_daemon.stop()
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


                # Saúde do capture daemon
                # Saúde do capture daemon
                # v12.3: CORREÇÃO — 600s (10min) é muito tempo. Se o daemon morre,
                # perdemos 10min de dados antes de descobrir. 60s é mais seguro.
                if time.time() - ultimo_captura_log > 60:
                    ultimo_captura_log = time.time()
                    health = self.capture_daemon.health_check()
                    if not health['alive']:
                        log.error('[CAPTURE-DAEMON] Thread morta! Reiniciando...')
                        self.capture_daemon.start()
                    elif health['queue_pct'] > 80:
                        log.warning(f"[CAPTURE-DAEMON] Fila alta: {health['queue_pct']}%")
                    else:
                        rej = health.get('storage_rejeitados', {})
                        if any(rej.values()):
                            log.warning(f"[CAPTURE-DAEMON] rejeitados: {rej}")

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
                    self.capture_daemon.flush()
                
                # Fase 7: Registro de latência para o Dashboard
                self.latencia_atual_ms = (time.perf_counter() - t0) * 1000
                self.eventos_processados += 1
                
                if self.latencia_atual_ms > 50 and self.eventos_processados % 100 == 0:
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
            
            # 2. Gravação de captura bruta (Infra) — via daemon imortal
            self.capture_daemon.registrar_negocios([(
                trade.symbol, trade.timestamp_ms, trade.price, trade.quantity,
                trade.aggressor, trade.buyer, trade.seller
            )])

            # 3. Alimentar Scorer ML (se disponível)
            if self.scorer:
                self.scorer.evento(trade.symbol, trade.timestamp_ms, trade.price,
                                  trade.quantity, trade.aggressor, trade.buyer, trade.seller)

            # 4. Replay gate: se não aprovado, modo CAPTURA PURA
            if not self._replay_aprovado:
                return

            # 5. Calcular features + sinal
            seg = trade.timestamp_ms // 1000
            sig: Signal = self.signal.calcular(seg, skip_avaliar=False)

            # 6. Acoplamento com PositionManager
            if sig and trade.symbol == self.ativo_principal:
                self.position.confianca_ewma = sig.confianca

                res_recentes = self.learning.resultados if self.learning else []
                bs = self.market_state.book_stats.get(trade.symbol, {}) or {}
                blf = bs.get('book_level', None) or {}
                self.risk_engine.atualizar_mercado(
                    preco_ts=trade.timestamp_ms,
                    spread=blf.get('spread', 0),
                    vol_bps=blf.get('vel_bid_ewma', 0),
                    ml_disponivel=self.scorer is not None,
                    confianca=sig.confianca,
                )

                decision = self.risk_engine.avaliar(sig, res_recentes)

                action = self.position.gerenciar(
                    ativo=trade.symbol,
                    signal=sig,
                    preco=trade.price,
                    decision=decision,
                    regime=self.signal.features.get(trade.symbol, {}).get('regime')
                )

                if action and hasattr(action, 'tipo') and action.tipo in ('ABRIR', 'FECHAR'):
                    entry = TradeDecision(
                        timestamp_do_evento=trade.timestamp_ms / 1000.0,
                        timestamp_de_processamento=time.time(),
                        ativo=trade.symbol,
                        acao=action.tipo,
                        lado=action.lado,
                        preco=action.preco,
                        score=sig.score,
                        confianca=sig.confianca,
                        ml_prob=sig.ml_prob,
                        sinal=1 if sig.lado == 'C' else (-1 if sig.lado == 'V' else 0),
                        regime=self.signal.features.get(trade.symbol, {}).get('regime', 'lateral'),
                        tp=action.tp, sl=action.sl,
                        risk_decision=decision.decisao if hasattr(decision, 'decisao') else '',
                        risk_motivo=decision.motivo if hasattr(decision, 'motivo') else '',
                        motivos=sig.motivos,
                        modelo=(
                            'heuristico' if not self.scorer
                            else ('heuristico+ML(USADO)' if sig.ml_prob > 0.5 and sig.lado != ''
                                  else 'heuristico+ML(BLOQUEADO)')
                        ),
                        model_version=self.config.get('ml_modelo', '').split('\\')[-1] if self.config.get('ml_modelo') else '',
                    )
                    self.journal.registrar(entry)

        elif event.type == 'RLP':
            # v12.5: RLP (Registro de Livros e Posicoes) - gravar separadamente
            trade: TradeEvent = event.payload
            self.capture_daemon.registrar_rlp([(
                trade.symbol, trade.timestamp_ms, trade.price, trade.quantity,
                trade.aggressor, trade.buyer, trade.seller
            )])

            # 3. Alimentar Scorer ML PRIMEIRO (Inferência precisa rodar
            #    ANTES do signal engine para que self.scorer.prob tenha
            #    a probabilidade do evento ATUAL, não do anterior).
            if self.scorer:
                self.scorer.evento(trade.symbol, trade.timestamp_ms, trade.price, 
                                  trade.quantity, trade.aggressor, trade.buyer, trade.seller)

            # 4. Replay gate: se não aprovado, modo CAPTURA PURA (não trade)
            # v12.3: CORREÇÃO — gate ANTES de calcular sinal para não gastar CPU
            # em modo captura pura. O sinal nunca será usado, então não calcula.
            if not self._replay_aprovado:
                return  # Grava dados (passo 2 já fez), não processa sinais/trading

            # 5. Calcular features + sinal (Lógica)
            seg = trade.timestamp_ms // 1000
            sig: Signal = self.signal.calcular(seg, skip_avaliar=False)
            
            # 6. Acoplamento com PositionManager (Execução)
            if sig and trade.symbol == self.ativo_principal:
                # v10.15: Utiliza o objeto Signal tipado
                self.position.confianca_ewma = sig.confianca
                
                # Risk Engine: avaliação completa com 14 proteções
                res_recentes = self.learning.resultados if self.learning else []
                
                # Atualizar estado de mercado no risk engine
                bs = self.market_state.book_stats.get(trade.symbol, {}) or {}
                blf = bs.get('book_level', None) or {}
                self.risk_engine.atualizar_mercado(
                    preco_ts=trade.timestamp_ms,
                    spread=blf.get('spread', 0),
                    vol_bps=blf.get('vel_bid_ewma', 0),
                    ml_disponivel=self.scorer is not None,
                    confianca=sig.confianca,
                )
                
                decision = self.risk_engine.avaliar(sig, res_recentes)

                action = self.position.gerenciar(
                    ativo=trade.symbol,
                    signal=sig,
                    preco=trade.price,
                    decision=decision,
                    regime=self.signal.features.get(trade.symbol, {}).get('regime')
                )
                
                # Decision Journal: registrar ação executada
                if action and hasattr(action, 'tipo') and action.tipo in ('ABRIR', 'FECHAR'):
                    entry = TradeDecision(
                        # trade.timestamp_ms e em MILISSEGUNDOS; os campos do
                        # journal sao em SEGUNDOS (unix).
                        timestamp_do_evento=trade.timestamp_ms / 1000.0,
                        timestamp_de_processamento=time.time(),
                        ativo=trade.symbol,
                        acao=action.tipo,
                        lado=action.lado,
                        preco=action.preco,
                        score=sig.score,
                        confianca=sig.confianca,
                        ml_prob=sig.ml_prob,
                        sinal=1 if sig.lado == 'C' else (-1 if sig.lado == 'V' else 0),
                        regime=self.signal.features.get(trade.symbol, {}).get('regime', 'lateral'),
                        tp=action.tp, sl=action.sl,
                        risk_decision=decision.decisao if hasattr(decision, 'decisao') else '',
                        risk_motivo=decision.motivo if hasattr(decision, 'motivo') else '',
                        motivos=sig.motivos,
                        # v12.3: CORREÇÃO — Registrar se o ML foi usado (gate passou)
                        # ou apenas consultado (gate bloqueou). Isso ajuda na análise
                        # posterior de performance do ML vs heurística.
                        modelo=(
                            'heuristico' if not self.scorer
                            else ('heuristico+ML(USADO)' if sig.ml_prob > 0.5 and sig.lado != ''
                                  else 'heuristico+ML(BLOQUEADO)')
                        ),
                        model_version=self.config.get('ml_modelo', '').split('\\')[-1] if self.config.get('ml_modelo') else '',
                    )
                    self.journal.registrar(entry)

        elif event.type == 'BOOK':
            snapshot: BookSnapshot = event.payload
            
            # 1. Alimentar market_state (Lógica de BookLevelFeatures)
            self.market_state.alimentar_book(snapshot)
            
            # 2. Gravação de captura bruta
            snap_dict = {}
            bid_vol = sum(l.volume for l in snapshot.bids)
            ask_vol = sum(l.volume for l in snapshot.asks)
            for level in snapshot.bids:
                broker = getattr(level, 'broker', '_anon') or '_anon'
                if broker not in snap_dict:
                    snap_dict[broker] = {'bid_vol': 0, 'ask_vol': 0}
                snap_dict[broker]['bid_vol'] += level.volume
            for level in snapshot.asks:
                broker = getattr(level, 'broker', '_anon') or '_anon'
                if broker not in snap_dict:
                    snap_dict[broker] = {'bid_vol': 0, 'ask_vol': 0}
                snap_dict[broker]['ask_vol'] += level.volume
            levels_data = {
                'bid_preco': [l.price for l in snapshot.bids[:500]],
                'bid_vol': [l.volume for l in snapshot.bids[:500]],
                'ask_preco': [l.price for l in snapshot.asks[:500]],
                'ask_vol': [l.volume for l in snapshot.asks[:500]],
            }
            self.capture_daemon.registrar_book(snapshot.symbol, snapshot.timestamp_ms,
                                        snap_dict, bid_vol, ask_vol, levels=levels_data)
            
            # 3. Alimentar Scorer ML com o Book
            if self.scorer:
                self.scorer.book(snapshot.symbol, snapshot.timestamp_ms, snapshot)

    # ---- Getters para o dashboard ----

    def get_capture_health(self) -> dict:
        """Retorna o status de saúde do capture daemon."""
        return self.capture_daemon.health_check()

    def get_rtd_health(self) -> dict:
        """Retorna o status de saúde da fonte de dados."""
        if not self.data_source:
            return {'status': 'error', 'motivo': 'sem fonte de dados'}
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

    # ---- Facade: métodos para DashboardAPI ----

    def get_features(self):
        feat = self.signal.get_features()
        # Sempre informar ao dashboard qual e o ativo principal
        principal = self.ativo_principal
        feat['_principal'] = principal
        feat['_contexto'] = self.ativo_contexto
        # Fallback: se o principal nao tem dados, injeta dados do contexto
        if principal not in feat or not feat[principal].get('preco_fim'):
            for sym, f in feat.items():
                if sym.startswith('_'):
                    continue
                if f.get('preco_fim') and f.get('preco_fim') > 0:
                    feat[principal] = f
                    feat['_principal_fallback'] = sym  # avisa que e fallback
                    break
        return feat

    def get_sinais(self):
        return self.signal.get_sinais()

    def get_posicao(self):
        return self.position.get_posicao(
            lambda ativo: self.market_state.obter_ultimo_preco(ativo, self.signal.features))

    def get_estatisticas(self):
        return self.metrics.get_estatisticas()

    def get_memoria(self):
        # v12.4 (Fase 6): Estado de risco vem do RiskEngine (fonte única)
        re = self.risk_engine
        return self.market_state.get_memoria(
            circuit_breaker_nivel=re.circuit_breaker_nivel,
            trades_dia=re.trades_dia,
            pnl_dia=re.pnl_dia,
            perdas_consecutivas=re.perdas_consecutivas,
            confianca_ewma=self.position.confianca_ewma,
            sinal_confirmado=self.position.sinal_confirmado,
        )

    def get_book_stats(self):
        return self.market_state.get_book_stats()

    def get_ordering_stats(self):
        """Retorna métricas de ordenamento temporal (Fase 3)."""
        if self.data_source and hasattr(self.data_source, '_ordering_detector'):
            return self.data_source._ordering_detector.get_stats_for_dashboard()
        return {}

    def get_book_level(self):
        return self.market_state.get_book_level()

    def calcular_metricas(self):
        return self.metrics.calcular()

    def get_resumo(self, ativo):
        return self.market_state.get_resumo(ativo)

    def get_saldo_corretoras(self, ativo=None):
        return self.market_state.get_saldo_corretoras(ativo)

    def get_historico(self, segundos=1800):
        return self.market_state.get_historico(segundos)

    def get_feature_status(self):
        return self.learning.get_feature_status()

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
        self.capture_daemon.flush()
        if final:
            self.capture_daemon.stop()

    def _verificar_staleness_reconexao(self):
        """Verifica se algum ativo perdeu dados (staleness) e reconecta se necessário.
        
        Implementa: pregão (seg-sex 8:45-18:30), cooldown 30s, check por ativo.
        Return: True se reconexão foi acionada, False caso contrário.
        """
        from core.market_state import check_staleness

        if not getattr(self, '_conexao_ok', True):
            return False

        agora = datetime.now()
        # Pregão: seg(0)-sex(4), 8:45-18:30
        if agora.weekday() > 4:
            return False
        hora_min = agora.hour * 60 + agora.minute
        if hora_min < 8 * 60 + 45 or hora_min > 18 * 60 + 30:
            return False

        # Cooldown entre reconexões (30s)
        cooldown = getattr(self, 'cooldown_staleness_s', 30)
        ultima = getattr(self, '_ultima_reconexao', 0.0)
        if time.time() - ultima < cooldown:
            return False

        # Check por ativo (compatível com App real e FakeApp de testes)
        estados = getattr(self, 'estados', None) or (
            self.market_state.estados if hasattr(self, 'market_state') else {})
        for ativo, est in estados.items():
            if check_staleness(est, time.time()):
                log.warning('[STALENESS] %s: dados antigos, reconectando...', ativo)
                self._reconectar()
                self._ultima_reconexao = time.time()
                return True

        return False

    def parar(self):
        self._shutdown.set()
        self.dashboard.stop()
        self.capture_daemon.stop()
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
