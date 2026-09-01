# -*- coding: utf-8 -*-
"""
adapters/dashboard/handlers.py — Handlers de endpoints do dashboard.

Cada handler é um método estático que recebe a instância do DashboardAPI
e retorna o dict/HTML a ser serializado como JSON.

Endpoints tratados:
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

import time
import json
import pathlib
import logging

log = logging.getLogger(__name__)


class DashboardHandlers:
    """Handlers estáticos para cada endpoint do dashboard."""

    @staticmethod
    def handle_root(handler):
        """Serve dashboard_pro.html - sempre re-le para pegar atualizacoes."""
        try:
            _dash = pathlib.Path(__file__).resolve().parent / 'dashboard_pro.html'
            if _dash.exists():
                return _dash.read_text(encoding='utf-8'), 'text/html'
        except Exception:
            pass
        return handler.app.html() if hasattr(handler.app, 'html') else 'dashboard not found', 'text/html'

    @staticmethod
    def handle_api_status(handler, params=None):
        if handler.state:
            return handler.state.payload()
        # Fallback: usar app diretamente (nova arquitetura)
        if handler.app:
            try:
                app = handler.app
                return {
                    'ativos': list(app.market_state.stats.keys()) if hasattr(app, 'market_state') else [],
                    'posicao': app.get_posicao() or {},
                    'features': app.get_features(),
                    'sinais': app.get_sinais(),
                    'metricas': app.calcular_metricas(),
                    'learning': app.get_estatisticas(),
                    'capture_health': app.get_capture_health(),
                    'rtd_health': app.get_rtd_health(),
                }
            except Exception as e:
                return {'error': str(e)}
        return {"error": "State not initialized"}

    @staticmethod
    def handle_api_features(handler, params=None):
        f = handler.app.get_features()
        try:
            from config import ATIVO_PRINCIPAL, ATIVO_CONTEXTO
            f['_principal'] = ATIVO_PRINCIPAL
            f['_contexto'] = ATIVO_CONTEXTO
        except ImportError:
            pass
        return f

    @staticmethod
    def handle_api_sinais(handler, params=None):
        return handler.app.get_sinais()

    @staticmethod
    def handle_api_posicao(handler, params=None):
        return handler.app.get_posicao() or {'acao': 'SEM_POSICAO'}

    @staticmethod
    def handle_api_learning(handler, params=None):
        return handler.app.get_estatisticas()

    @staticmethod
    def handle_api_memoria(handler, params=None):
        return handler.app.get_memoria()

    @staticmethod
    def handle_api_book(handler, params=None):
        return handler.app.get_book_stats()

    @staticmethod
    def handle_api_book_level(handler, params=None):
        return handler.app.get_book_level()

    @staticmethod
    def handle_api_metricas(handler, params=None):
        return handler.app.calcular_metricas()

    @staticmethod
    def handle_api_resumo(handler, params=None):
        try:
            from config import ATIVO_PRINCIPAL
        except ImportError:
            ATIVO_PRINCIPAL = 'WINV26'
        a = (params or {}).get('ativo', [ATIVO_PRINCIPAL])[0]
        return handler.app.get_resumo(a)

    @staticmethod
    def handle_api_padroes(handler, params=None):
        return handler.app.market_state.padroes.get_resumo()

    @staticmethod
    def handle_api_rtd_health(handler, params=None):
        return handler.app.get_rtd_health()

    @staticmethod
    def handle_api_capture_health(handler, params=None):
        return handler.app.get_capture_health()

    @staticmethod
    def handle_api_saldo_corretoras(handler, params=None):
        return handler.app.get_saldo_corretoras()

    @staticmethod
    def handle_api_contexto(handler, params=None):
        return handler.app.get_contexto_mercado()

    @staticmethod
    def handle_api_ml_health(handler, params=None):
        result = {}
        if handler.app.scorer:
            result = handler.app.scorer.estado_salud()
            # v12.0: Adicionar features de regime ao estado do scorer
            if hasattr(handler.app.scorer, 'regime'):
                result['regime'] = handler.app.scorer.regime
        # v11.10: Adicionar dados de calibração
        if hasattr(handler.app.signal, 'calibration') and handler.app.signal.calibration:
            sep = handler.app.signal.calibration
            cal_metrics = sep.calibrator.get_metrics() if hasattr(sep, 'calibrator') else {}
            result['calibration'] = cal_metrics
            # v11.13: ECE ao vivo para o dashboard
            result['ml_quality'] = {
                'ece': cal_metrics.get('ece', 0),
                'brier': cal_metrics.get('brier_score', 0),
                'n_predictions': cal_metrics.get('n_predictions', 0),
                'thresholds_by_regime': cal_metrics.get('thresholds_by_regime', {}),
                'ml_weight': 0.3 if cal_metrics.get('ece', 0) > 0.15 else (0.7 if cal_metrics.get('ece', 0) < 0.05 else 0.5),
            }
        return result if result else {"error": "Scorer not initialized"}

    @staticmethod
    def handle_api_historico(handler, params=None):
        return handler.app.get_historico()

    @staticmethod
    def handle_api_regime(handler, params=None):
        """v12.1: Retorna features de regime + ATR + volume relativo ao vivo."""
        result = {}
        if hasattr(handler.app, 'scorer') and handler.app.scorer:
            scorer = handler.app.scorer
            
            # Regime features
            if hasattr(scorer, 'regime'):
                for ativo, tracker in scorer.regime.items():
                    snap = tracker.snapshot()
                    result[ativo] = snap
            
            # ATR features (v12.1: adicionar ao dashboard)
            if hasattr(scorer, '_atr_values'):
                for ativo, atr_val in scorer._atr_values.items():
                    if ativo not in result:
                        result[ativo] = {}
                    result[ativo]['atr_14'] = atr_val
                    result[ativo]['atr_14_norm'] = atr_val / max(scorer._atr_prev.get(ativo, 0), 1.0)
            
            # Volume relativo (v12.1: adicionar ao dashboard)
            vol_rel_attr = getattr(scorer, 'vol_rel', None) or getattr(scorer, 'vrels', None)
            if vol_rel_attr:
                for ativo, tracker in vol_rel_attr.items():
                    if ativo not in result:
                        result[ativo] = {}
                    result[ativo].update(tracker.snapshot())
            
            # VWAP inclinação (v12.1: adicionar ao dashboard)
            if hasattr(scorer, '_vwap_history'):
                for ativo, hist in scorer._vwap_history.items():
                    if ativo not in result:
                        result[ativo] = {}
                    if len(hist) >= 600:
                        result[ativo]['vwap_inclinacao_1m'] = (hist[-1] - hist[-600]) / max(hist[-600], 1.0)
                    if len(hist) >= 3000:
                        result[ativo]['vwap_inclinacao_5m'] = (hist[-1] - hist[-3000]) / max(hist[-3000], 1.0)
        
        return result if result else {'error': 'Tracker não disponível'}

    @staticmethod
    def handle_api_decisoes(handler, params=None):
        decisoes = handler.app.journal.listar(limite=50) if hasattr(handler.app, 'journal') else []
        return [d.to_dict() for d in decisoes]

    @staticmethod
    def handle_api_decisoes_id(handler, did):
        entry = handler.app.journal.buscar(id=did) if hasattr(handler.app, 'journal') else None
        return entry.to_dict() if entry else {'error': 'not found'}

    @staticmethod
    def handle_api_all(handler, params=None):
        """Snapshot agregado com cache 1x/s para reduzir lock contention."""
        now = time.time()
        app = handler.app
        if handler._snapshot is None or (now - handler._snapshot_ts) > 1.0:
            handler._snapshot = {
                'features': app.get_features(),
                'sinais': app.get_sinais(),
                'posicao': app.get_posicao() or {},
                'learning': app.get_estatisticas(),
                'memoria': app.get_memoria(),
                'metricas': app.calcular_metricas(),
                'saldo_corretoras': app.get_saldo_corretoras(),
                'padroes': app.market_state.padroes.get_resumo(),
                'ml_health': app.scorer.estado_salud() if app.scorer else {},
                'book_level': app.get_book_level() if hasattr(app, 'get_book_level') else {},
                'rtd_health': app.get_rtd_health() if hasattr(app, 'get_rtd_health') else {},
                'capture_health': app.get_capture_health() if hasattr(app, 'get_capture_health') else {},
            }
            # Merge regime features (atr_14, volume_relativo, vwap_inclinacao)
            try:
                regime_data = DashboardHandlers.handle_api_regime(handler, params)
                if isinstance(regime_data, dict) and not regime_data.get('error'):
                    for ativo, rfeat in regime_data.items():
                        if ativo in handler._snapshot.get('features', {}):
                            handler._snapshot['features'][ativo].update(rfeat)
            except Exception:
                pass
            handler._snapshot_ts = now
        return handler._snapshot

    @staticmethod
    def handle_health(handler, params=None):
        uptime = time.time() - getattr(handler.app, 'tempo_inicio', time.time())
        try:
            from config import ATIVO_PRINCIPAL
        except ImportError:
            ATIVO_PRINCIPAL = 'WINV26'
        return {
            'status': 'ok' if getattr(handler.app, '_conexao_ok', False) else 'disconnected',
            'uptime_s': round(uptime, 1),
            'latencia_loop_ms': round(getattr(handler.app, 'latencia_atual_ms', 0), 2),
            'eventos_total': getattr(handler.app, 'eventos_processados', 0),
            'negocios_total': handler.app.market_state.stats.get(ATIVO_PRINCIPAL, {}).get('n', 0),
        }

    @staticmethod
    def handle_legacy(handler, params=None):
        return handler.app.html() if hasattr(handler.app, 'html') else 'legacy mode disabled'
