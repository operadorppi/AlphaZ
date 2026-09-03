# -*- coding: utf-8 -*-
"""
core/observability.py — Sistema de métricas e observabilidade (FASE 19).

STATUS: NÃO INTEGRADO AO MOTOR (FASE 19 P1)
-------------------------------------------
Este módulo existe e é testado, mas NÃO está conectado ao motor de trading.
As funções existem mas não são chamadas em core/app.py, core/risk_engine.py,
ou em qualquer outro lugar do código de produção.

Decisão: Manter como código disponível para futuro uso (opção b).
- Os testes passam (tests/test_observability.py)
- A API está definida e documentada
- A integração pode ser feita quando for prioridade

Para integrar no futuro, adicionar chamadas em:
- core/app.py: loop principal (métricas de captura/processamento)
- core/risk_engine.py: decisões de allow/block (métricas de risco)
- core/signal_engine.py: scoring (métricas de latência ML)
"""

from __future__ import annotations
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """Ponto de dado de métrica."""
    nome: str
    valor: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class TimerSample:
    """Amostra de timer."""
    nome: str
    duracao_ms: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """
    Coletor de métricas para o sistema de trading.
    
    Thread-safe com suporte a timers e contadores.
    """
    
    def __init__(self, namespace: str = "trading"):
        self.namespace = namespace
        self._lock = threading.Lock()
        
        # Contadores
        self._counters: Dict[str, int] = defaultdict(int)
        
        # Gauges (valores atuais)
        self._gauges: Dict[str, float] = defaultdict(float)
        
        # Timers (latências)
        self._timers: Dict[str, List[float]] = defaultdict(list)
        self._timer_starts: Dict[str, float] = {}
        
        # Histórico de samples
        self._histogram: Dict[str, List[float]] = defaultdict(list)
        
        # Metadados do modelo
        self._model_info: Dict[str, Any] = {}
        
        # Schema de features
        self._feature_schema: Dict[str, str] = {}
        
        # Timestamp do último evento
        self._last_event_ts: float = 0
        
    def _full_name(self, name: str) -> str:
        """Retorna nome completo da métrica."""
        if name.startswith(self.namespace + "."):
            return name
        return f"{self.namespace}.{name}"
    
    # ================================================================
    # CAPTURA
    # ================================================================
    
    def capture_event_received(self, simbolo: str = ""):
        """Incrementa contador de eventos recebidos."""
        self.increment("capture.events_received", tags={"simbolo": simbolo})
        
    def capture_event_processed(self, simbolo: str = ""):
        """Incrementa contador de eventos processados."""
        self.increment("capture.events_processed", tags={"simbolo": simbolo})
        
    def capture_event_dropped(self, motivo: str = "", simbolo: str = ""):
        """Incrementa contador de eventos dropados."""
        self.increment("capture.events_dropped", tags={"motivo": motivo, "simbolo": simbolo})
        
    def capture_event_duplicate(self, simbolo: str = ""):
        """Incrementa contador de eventos duplicados."""
        self.increment("capture.events_duplicate", tags={"simbolo": simbolo})
        
    def capture_event_out_of_order(self, simbolo: str = ""):
        """Incrementa contador de eventos fora de ordem."""
        self.increment("capture.events_out_of_order", tags={"simbolo": simbolo})
        
    def capture_book_snapshot(self, simbolo: str = ""):
        """Incrementa contador de book snapshots."""
        self.increment("dados.book_snapshots", tags={"simbolo": simbolo})
        
    def capture_trade(self, simbolo: str = ""):
        """Incrementa contador de trades."""
        self.increment("dados.trades", tags={"simbolo": simbolo})
        
    def capture_tnt_rlp(self, simbolo: str = ""):
        """Incrementa contador de T&T RLP."""
        self.increment("dados.tnt_rlp", tags={"simbolo": simbolo})
        
    def capture_gap_detected(self, tipo: str = ""):
        """Incrementa contador de gaps."""
        self.increment("dados.gaps", tags={"tipo": tipo})
        
    def capture_timestamp_anomaly(self, tipo: str = ""):
        """Incrementa contador de anomalias de timestamp."""
        self.increment("dados.timestamp_anomalies", tags={"tipo": tipo})
        
    # ================================================================
    # LATÊNCIA
    # ================================================================
    
    def latency_start(self, categoria: str):
        """Inicia medição de latência."""
        self._timer_starts[categoria] = time.time()
        
    def latency_stop(self, categoria: str):
        """Para medição de latência e registra."""
        if categoria in self._timer_starts:
            duracao_ms = (time.time() - self._timer_starts[categoria]) * 1000
            self.record_timer(categoria, duracao_ms)
            del self._timer_starts[categoria]
            
    def record_timer(self, nome: str, duracao_ms: float):
        """Registra amostra de timer."""
        with self._lock:
            key = self._full_name(nome)
            self._timers[key].append(duracao_ms)
            # Mantém apenas ultimas 1000 amostras
            if len(self._timers[key]) > 1000:
                self._timers[key] = self._timers[key][-1000:]
                
    def capture_event_to_processing(self, simbolo: str = ""):
        """Mede latencia de evento para processing."""
        self.latency_start("event_to_processing")
        # Para ser chamado apos o processamento
        self.latency_stop("event_to_processing")
        
    def capture_feature_latency(self, simbolo: str = ""):
        """Mede latencia de computacao de features."""
        self.latency_start("feature_latency")
        self.latency_stop("feature_latency")
        
    def capture_ml_latency(self, simbolo: str = ""):
        """Mede latencia de inferencia ML."""
        self.latency_start("ml_latency")
        self.latency_stop("ml_latency")
        
    def capture_decision_latency(self, simbolo: str = ""):
        """Mede latencia de tomada de decisao."""
        self.latency_start("decision_latency")
        self.latency_stop("decision_latency")
        
    # ================================================================
    # ML
    # ================================================================
    
    def ml_model_loaded(self, versao: str = "", modelo_path: str = ""):
        """Marca modelo como carregado."""
        self._model_info["loaded"] = True
        self._model_info["version"] = versao
        self._model_info["path"] = modelo_path
        self._model_info["timestamp"] = datetime.now().isoformat()
        self.increment("ml.model_loaded")
        
    def ml_model_unloaded(self):
        """Marca modelo como nao carregado."""
        self._model_info["loaded"] = False
        self.increment("ml.model_unloaded")
        
    def ml_set_feature_schema(self, schema: Dict[str, str]):
        """Registra schema de features."""
        self._feature_schema = schema
        self.increment("ml.feature_schema_registered", valor=len(schema))
        
    def ml_inference_error(self, erro: str = ""):
        """Incrementa contador de erros de inferencia."""
        self.increment("ml.inference_errors", tags={"erro": erro})
        
    def ml_fallback_used(self, motivo: str = ""):
        """Incrementa contador de uso de fallback."""
        self.increment("ml.fallback_count", tags={"motivo": motivo})
        
    # ================================================================
    # RISCO
    # ================================================================
    
    def risk_block(self, motivo: str, simbolo: str = ""):
        """Incrementa contador de blocos de risco."""
        self.increment("risk.blocks", tags={"motivo": motivo, "simbolo": simbolo})
        
    def risk_stale_block(self, simbolo: str = ""):
        """Incrementa contador de blocos por dados obsoletos."""
        self.increment("risk.stale_blocks", tags={"simbolo": simbolo})
        
    def risk_drawdown_block(self, simbolo: str = ""):
        """Incrementa contador de blocos por drawdown."""
        self.increment("risk.drawdown_blocks", tags={"simbolo": simbolo})
        
    def risk_exposure_block(self, simbolo: str = ""):
        """Incrementa contador de blocos por exposicao."""
        self.increment("risk.exposure_blocks", tags={"simbolo": simbolo})
        
    # ================================================================
    # GAUGES
    # ================================================================
    
    def gauge_set(self, nome: str, valor: float, tags: Dict[str, str] = None):
        """Define valor de gauge."""
        with self._lock:
            key = self._full_name(nome)
            self._gauges[key] = valor
            if tags:
                self._gauges[f"{key}_tags"] = str(tags)
                
    def gauge_increment(self, nome: str, delta: float = 1.0, tags: Dict[str, str] = None):
        """Incrementa gauge."""
        with self._lock:
            key = self._full_name(nome)
            self._gauges[key] += delta
            
    def gauge_decrement(self, nome: str, delta: float = 1.0, tags: Dict[str, str] = None):
        """Decrementa gauge."""
        with self._lock:
            key = self._full_name(nome)
            self._gauges[key] -= delta
            
    # ================================================================
    # OPERACOES BASICAS
    # ================================================================
    
    def increment(self, nome: str, valor: int = 1, tags: Dict[str, str] = None):
        """Incrementa contador."""
        with self._lock:
            key = self._full_name(nome)
            self._counters[key] += valor
            if tags:
                # Adiciona tag ao contador
                tag_key = f"{key}_tags"
                if tag_key not in self._counters:
                    self._counters[tag_key] = {}
                self._counters[tag_key].update(tags)
                
    def histogram_record(self, nome: str, valor: float):
        """Registra valor no histograma."""
        with self._lock:
            key = self._full_name(nome)
            self._histogram[key].append(valor)
            if len(self._histogram[key]) > 1000:
                self._histogram[key] = self._histogram[key][-1000:]
                
    # ================================================================
    # SNAPSHOT E REPORTING
    # ================================================================
    
    def snapshot(self) -> Dict[str, Any]:
        """Retorna snapshot de todas as métricas."""
        with self._lock:
            return {
                "timestamp": datetime.now().isoformat(),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers": {k: self._calc_stats(v) for k, v in self._timers.items()},
                "histograms": {k: self._calc_stats(v) for k, v in self._histogram.items()},
                "model_info": dict(self._model_info),
                "feature_schema": dict(self._feature_schema),
            }
    
    def _calc_stats(self, valores: List[float]) -> Dict[str, float]:
        """Calcula estatisticas de uma lista de valores."""
        if not valores:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
        
        import numpy as np
        arr = np.array(valores)
        return {
            "count": len(valores),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "avg": float(arr.mean()),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        }
    
    def reset(self):
        """Reseta todas as métricas."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timers.clear()
            self._histogram.clear()
            self._timer_starts.clear()
            
    def get_counter(self, nome: str) -> int:
        """Retorna valor atual de um counter."""
        with self._lock:
            return self._counters.get(self._full_name(nome), 0)
    
    def get_gauge(self, nome: str) -> float:
        """Retorna valor atual de um gauge."""
        with self._lock:
            return self._gauges.get(self._full_name(nome), 0.0)
    
    def get_timer_avg(self, nome: str) -> float:
        """Retorna media de um timer."""
        with self._lock:
            valores = self._timers.get(self._full_name(nome), [])
            if not valores:
                return 0.0
            return sum(valores) / len(valores)


# Instancia global para facilitar uso
_default_collector = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Retorna coletor de metricas padrao."""
    return _default_collector


def reset_metrics():
    """Reseta metricas padrao."""
    _default_collector.reset()
