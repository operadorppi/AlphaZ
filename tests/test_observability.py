# -*- coding: utf-8 -*-
"""
tests/test_observability.py — Testes para o sistema de observabilidade (FASE 19).

Categorias:
- test_capture_metrics: métricas de captura
- test_latency_metrics: métricas de latência
- test_data_metrics: métricas de dados
- test_ml_metrics: métricas de ML
- test_risk_metrics: métricas de risco
- test_snapshot_and_stats: snapshot e estatísticas

Usage:
    python -m pytest tests/test_observability.py -v
"""

import sys
import time
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Adiciona o root ao path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

logging.basicConfig(level=logging.WARNING)


class TestCaptureMetrics:
    """Testes para métricas de captura."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_events_received(self):
        """Deve incrementar events_received."""
        self.collector.capture_event_received("WINV26")
        self.collector.capture_event_received("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.capture.events_received"] == 2
        
    def test_events_processed(self):
        """Deve incrementar events_processed."""
        self.collector.capture_event_processed("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.capture.events_processed"] == 1
        
    def test_events_dropped(self):
        """Deve incrementar events_dropped com motivo."""
        self.collector.capture_event_dropped(motivo="timeout", simbolo="WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.capture.events_dropped"] == 1
        
    def test_events_duplicate(self):
        """Deve incrementar events_duplicate."""
        self.collector.capture_event_duplicate("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.capture.events_duplicate"] == 1
        
    def test_events_out_of_order(self):
        """Deve incrementar events_out_of_order."""
        self.collector.capture_event_out_of_order("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.capture.events_out_of_order"] == 1
        
    def test_book_snapshots(self):
        """Deve incrementar book_snapshots."""
        self.collector.capture_book_snapshot("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.dados.book_snapshots"] == 1
        
    def test_trades(self):
        """Deve incrementar trades."""
        self.collector.capture_trade("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.dados.trades"] == 1
        
    def test_gaps(self):
        """Deve incrementar gaps."""
        self.collector.capture_gap_detected("book")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.dados.gaps"] == 1
        
    def test_timestamp_anomalies(self):
        """Deve incrementar timestamp_anomalies."""
        self.collector.capture_timestamp_anomaly("regressive")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.dados.timestamp_anomalies"] == 1


class TestLatencyMetrics:
    """Testes para métricas de latência."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_event_to_processing(self):
        """Deve medir latencia event_to_processing."""
        self.collector.latency_start("event_to_processing")
        time.sleep(0.01)  # 10ms
        self.collector.latency_stop("event_to_processing")
        
        snapshot = self.collector.snapshot()
        stats = snapshot["timers"].get("trading.event_to_processing", {})
        assert stats["count"] == 1
        assert stats["avg"] >= 10  # Pelo menos 10ms
        
    def test_feature_latency(self):
        """Deve medir latencia de features."""
        self.collector.capture_feature_latency("WINV26")
        
        snapshot = self.collector.snapshot()
        stats = snapshot["timers"].get("trading.feature_latency", {})
        assert stats["count"] == 1
        
    def test_ml_latency(self):
        """Deve medir latencia de ML."""
        self.collector.capture_ml_latency("WINV26")
        
        snapshot = self.collector.snapshot()
        stats = snapshot["timers"].get("trading.ml_latency", {})
        assert stats["count"] == 1
        
    def test_decision_latency(self):
        """Deve medir latencia de decisao."""
        self.collector.capture_decision_latency("WINV26")
        
        snapshot = self.collector.snapshot()
        stats = snapshot["timers"].get("trading.decision_latency", {})
        assert stats["count"] == 1
        
    def test_multiple_latency_samples(self):
        """Deve acumular multiplas amostras de latencia."""
        for _ in range(5):
            self.collector.latency_start("test_timer")
            time.sleep(0.001)
            self.collector.latency_stop("test_timer")
        
        snapshot = self.collector.snapshot()
        stats = snapshot["timers"].get("trading.test_timer", {})
        assert stats["count"] == 5
        
    def test_timer_not_started_returns_zero(self):
        """Timer nao iniciado deve retornar zero."""
        stats = self.collector.get_timer_avg("nonexistent")
        assert stats == 0.0


class TestDataMetrics:
    """Testes para metricas de dados."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_tnt_rlp(self):
        """Deve incrementar T&T RLP."""
        self.collector.capture_tnt_rlp("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.dados.tnt_rlp"] == 1


class TestMLMetrics:
    """Testes para metricas de ML."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_model_loaded(self):
        """Deve marcar modelo como carregado."""
        self.collector.ml_model_loaded(versao="v5", modelo_path="/path/modelo.pkl")
        
        snapshot = self.collector.snapshot()
        assert snapshot["model_info"]["loaded"] == True
        assert snapshot["model_info"]["version"] == "v5"
        assert snapshot["model_info"]["path"] == "/path/modelo.pkl"
        
    def test_model_unloaded(self):
        """Deve marcar modelo como nao carregado."""
        self.collector.ml_model_loaded()
        self.collector.ml_model_unloaded()
        
        snapshot = self.collector.snapshot()
        assert snapshot["model_info"]["loaded"] == False
        
    def test_feature_schema(self):
        """Deve registrar schema de features."""
        schema = {"feature1": "float", "feature2": "int"}
        self.collector.ml_set_feature_schema(schema)
        
        snapshot = self.collector.snapshot()
        assert snapshot["feature_schema"] == schema
        
    def test_inference_error(self):
        """Deve incrementar inference_errors."""
        self.collector.ml_inference_error(erro="timeout")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.ml.inference_errors"] == 1
        
    def test_fallback_used(self):
        """Deve incrementar fallback_count."""
        self.collector.ml_fallback_used(motivo="ml_down")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.ml.fallback_count"] == 1


class TestRiskMetrics:
    """Testes para metricas de risco."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_risk_block(self):
        """Deve incrementar risk_blocks."""
        self.collector.risk_block(motivo="spread_excessive", simbolo="WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.risk.blocks"] == 1
        
    def test_stale_block(self):
        """Deve incrementar stale_blocks."""
        self.collector.risk_stale_block("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.risk.stale_blocks"] == 1
        
    def test_drawdown_block(self):
        """Deve incrementar drawdown_blocks."""
        self.collector.risk_drawdown_block("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.risk.drawdown_blocks"] == 1
        
    def test_exposure_block(self):
        """Deve incrementar exposure_blocks."""
        self.collector.risk_exposure_block("WINV26")
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"]["trading.risk.exposure_blocks"] == 1


class TestSnapshotAndStats:
    """Testes para snapshot e estatisticas."""
    
    def setup_method(self):
        from core.observability import MetricsCollector
        self.collector = MetricsCollector()
        
    def test_snapshot_contains_all_sections(self):
        """Snapshot deve conter todas as secoes."""
        snapshot = self.collector.snapshot()
        
        assert "counters" in snapshot
        assert "gauges" in snapshot
        assert "timers" in snapshot
        assert "histograms" in snapshot
        assert "model_info" in snapshot
        assert "feature_schema" in snapshot
        
    def test_empty_snapshot(self):
        """Snapshot vazio deve ter valores zero."""
        snapshot = self.collector.snapshot()
        
        assert snapshot["counters"] == {}
        assert snapshot["gauges"] == {}
        assert snapshot["timers"] == {}
        
    def test_reset(self):
        """Reset deve limpar todas as metricas."""
        self.collector.increment("test.counter", 10)
        self.collector.gauge_set("test.gauge", 100)
        self.collector.reset()
        
        snapshot = self.collector.snapshot()
        assert snapshot["counters"] == {}
        assert snapshot["gauges"] == {}
        
    def test_histogram_stats(self):
        """Histograma deve calcular estatisticas corretas."""
        for valor in [10, 20, 30, 40, 50]:
            self.collector.histogram_record("test.histogram", valor)
        
        snapshot = self.collector.snapshot()
        stats = snapshot["histograms"]["trading.test.histogram"]
        
        assert stats["count"] == 5
        assert stats["min"] == 10
        assert stats["max"] == 50
        assert stats["avg"] == 30
        
    def test_gauge_operations(self):
        """Operacoes de gauge devem funcionar."""
        self.collector.gauge_set("test.gauge", 100)
        assert self.collector.get_gauge("test.gauge") == 100
        
        self.collector.gauge_increment("test.gauge", 50)
        assert self.collector.get_gauge("test.gauge") == 150
        
        self.collector.gauge_decrement("test.gauge", 30)
        assert self.collector.get_gauge("test.gauge") == 120
        
    def test_counter_get(self):
        """Deve obter valor de counter."""
        self.collector.increment("test.counter", 5)
        assert self.collector.get_counter("test.counter") == 5
        
    def test_default_collector(self):
        """Collector padrao deve ser acessivel."""
        from core.observability import get_metrics, reset_metrics, MetricsCollector
        
        metrics = get_metrics()
        assert isinstance(metrics, MetricsCollector)
        
        reset_metrics()
        snapshot = metrics.snapshot()
        assert snapshot["counters"] == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
