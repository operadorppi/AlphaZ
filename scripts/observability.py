#!/usr/bin/env python3
"""
observability.py — Structured Logging + Prometheus Metrics
Centralized observability module for the trading system.
"""
import sys
import time
import logging
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Dict, Optional

import structlog
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest
from prometheus_client.core import REGISTRY

# ═══════════════════════════════════════════════════════════════════
# PROMETHEUS METRICS
# ═══════════════════════════════════════════════════════════════════

# Custom registry to avoid conflicts if multiple instances
registry = CollectorRegistry(auto_describe=True)

# ── Counters ───────────────────────────────────────────────────────
trades_total = Counter(
    'trades_total',
    'Total number of trades processed',
    ['symbol', 'side', 'outcome'],
    registry=registry
)

orders_total = Counter(
    'orders_total',
    'Total number of orders sent',
    ['symbol', 'side', 'status'],
    registry=registry
)

errors_total = Counter(
    'errors_total',
    'Total number of errors',
    ['component', 'error_type'],
    registry=registry
)

rtd_events_total = Counter(
    'rtd_events_total',
    'Total RTD events received',
    ['symbol', 'event_type'],
    registry=registry
)

rtd_reconnects_total = Counter(
    'rtd_reconnects_total',
    'Total RTD reconnections',
    registry=registry
)

model_predictions_total = Counter(
    'model_predictions_total',
    'Total model predictions',
    ['symbol', 'action'],
    registry=registry
)

scorer_fallbacks_total = Counter(
    'scorer_fallbacks_total',
    'Total scorer fallbacks (ML → heuristic)',
    registry=registry
)

# ── Histograms ─────────────────────────────────────────────────────
trade_latency_ms = Histogram(
    'trade_latency_ms',
    'Trade processing latency in milliseconds',
    ['symbol'],
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=registry
)

model_inference_ms = Histogram(
    'model_inference_ms',
    'Model inference latency in milliseconds',
    buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500, 1000),
    registry=registry
)

rtd_event_latency_ms = Histogram(
    'rtd_event_latency_ms',
    'RTD event processing latency in milliseconds',
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),
    registry=registry
)

feature_computation_ms = Histogram(
    'feature_computation_ms',
    'Feature computation latency in milliseconds',
    ['feature_type'],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 25, 50, 100),
    registry=registry
)

# ── Gauges ─────────────────────────────────────────────────────────
active_positions = Gauge(
    'active_positions',
    'Number of currently open positions',
    registry=registry
)

open_orders = Gauge(
    'open_orders',
    'Number of open orders',
    registry=registry
)

rtd_connection_status = Gauge(
    'rtd_connection_status',
    'RTD connection status (1=connected, 0=disconnected)',
    registry=registry
)

model_loaded = Gauge(
    'model_loaded',
    'Whether ML model is loaded (1=yes, 0=no)',
    registry=registry
)

queue_depth = Gauge(
    'queue_depth',
    'Current queue depth',
    ['queue_name'],
    registry=registry
)

memory_usage_bytes = Gauge(
    'memory_usage_bytes',
    'Process memory usage in bytes',
    registry=registry
)

cpu_usage_percent = Gauge(
    'cpu_usage_percent',
    'Process CPU usage percentage',
    registry=registry
)

# ── Custom Registry Access ─────────────────────────────────────────
def get_metrics_registry() -> CollectorRegistry:
    """Return the custom metrics registry."""
    return registry

def get_metrics_output() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest(registry)

# ═══════════════════════════════════════════════════════════════════
# STRUCTURED LOGGING
# ═══════════════════════════════════════════════════════════════════

def configure_structured_logging(
    level: str = "INFO",
    json_output: bool = False,
    add_timestamp: bool = True,
) -> structlog.BoundLogger:
    """
    Configure structlog for structured logging.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_output: If True, output JSON lines; else console-friendly
        add_timestamp: Add timestamp to each log entry
    
    Returns:
        Configured structlog logger
    """
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper()),
        stream=sys.stdout,
    )
    
    # Configure structlog processors
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]
    
    if add_timestamp:
        processors.append(structlog.processors.TimeStamper(fmt="%H:%M:%S.%f"))
    
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    return structlog.get_logger()

# Create default logger
logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════
# METRICS HELPERS
# ═══════════════════════════════════════════════════════════════════

class MetricsContext:
    """Context manager for timing operations and recording metrics."""
    
    def __init__(
        self,
        histogram: Histogram,
        labels: Optional[Dict[str, str]] = None,
        success_counter: Optional[Counter] = None,
        error_counter: Optional[Counter] = None,
        success_labels: Optional[Dict[str, str]] = None,
        error_labels: Optional[Dict[str, str]] = None,
    ):
        self.histogram = histogram
        self.labels = labels or {}
        self.success_counter = success_counter
        self.error_counter = error_counter
        self.success_labels = success_labels or {}
        self.error_labels = error_labels or {}
        self.start_time = None
        self.success = False
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.perf_counter() - self.start_time) * 1000
        
        # Record histogram
        self.histogram.labels(**self.labels).observe(elapsed_ms)
        
        # Record success/error counter
        if exc_type is None:
            self.success = True
            if self.success_counter:
                self.success_counter.labels(**self.success_labels).inc()
        else:
            if self.error_counter:
                self.error_counter.labels(**self.error_labels).inc()
        
        return False  # Don't suppress exceptions

def time_histogram(histogram: Histogram, labels: Optional[Dict[str, str]] = None):
    """Decorator to time a function and record to histogram."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                histogram.labels(**(labels or {})).observe(elapsed_ms)
        return wrapper
    return decorator

def record_trade(symbol: str, side: str, outcome: str):
    """Record a trade outcome."""
    trades_total.labels(symbol=symbol, side=side, outcome=outcome).inc()

def record_order(symbol: str, side: str, status: str):
    """Record an order submission."""
    orders_total.labels(symbol=symbol, side=side, status=status).inc()

def record_error(component: str, error_type: str):
    """Record an error occurrence."""
    errors_total.labels(component=component, error_type=error_type).inc()

def record_rtd_event(symbol: str, event_type: str):
    """Record an RTD event."""
    rtd_events_total.labels(symbol=symbol, event_type=event_type).inc()

def record_rtd_reconnect():
    """Record an RTD reconnection."""
    rtd_reconnects_total.inc()

def record_model_prediction(symbol: str, action: str):
    """Record a model prediction."""
    model_predictions_total.labels(symbol=symbol, action=action).inc()

def record_scorer_fallback():
    """Record a scorer fallback to heuristic."""
    scorer_fallbacks_total.inc()

def set_active_positions(count: int):
    """Set active positions gauge."""
    active_positions.set(count)

def set_open_orders(count: int):
    """Set open orders gauge."""
    open_orders.set(count)

def set_rtd_connection(connected: bool):
    """Set RTD connection status."""
    rtd_connection_status.set(1 if connected else 0)

def set_model_loaded(loaded: bool):
    """Set model loaded status."""
    model_loaded.set(1 if loaded else 0)

def set_queue_depth(queue_name: str, depth: int):
    """Set queue depth gauge."""
    queue_depth.labels(queue_name=queue_name).set(depth)

def update_system_metrics():
    """Update system metrics (memory, CPU)."""
    try:
        import psutil
        process = psutil.Process()
        memory_usage_bytes.set(process.memory_info().rss)
        cpu_usage_percent.set(process.cpu_percent())
    except ImportError:
        pass  # psutil not installed

# ═══════════════════════════════════════════════════════════════════
# CONVENIENCE DECORATORS
# ═══════════════════════════════════════════════════════════════════

def observe_latency(histogram: Histogram, labels: Optional[Dict[str, str]] = None):
    """Decorator to observe function latency."""
    return time_histogram(histogram, labels)

def count_calls(counter: Counter, labels: Optional[Dict[str, str]] = None):
    """Decorator to count function calls."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            counter.labels(**(labels or {})).inc()
            return result
        return wrapper
    return decorator

# ═══════════════════════════════════════════════════════════════════
# MIDDLEWARE-STYLE HELPERS
# ═══════════════════════════════════════════════════════════════════

@contextmanager
def track_rtd_event(symbol: str, event_type: str):
    """Context manager to track RTD event processing."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        rtd_event_latency_ms.observe(elapsed_ms)
        rtd_events_total.labels(symbol=symbol, event_type=event_type).inc()

@contextmanager
def track_model_inference(symbol: str = "default"):
    """Context manager to track model inference time."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        model_inference_ms.labels(symbol=symbol).observe(elapsed_ms)

@contextmanager
def track_feature_computation(feature_type: str):
    """Context manager to track feature computation time."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        feature_computation_ms.labels(feature_type=feature_type).observe(elapsed_ms)

# ═══════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    # Metrics
    'trades_total', 'orders_total', 'errors_total', 'rtd_events_total',
    'rtd_reconnects_total', 'model_predictions_total', 'scorer_fallbacks_total',
    'trade_latency_ms', 'model_inference_ms', 'rtd_event_latency_ms',
    'feature_computation_ms', 'active_positions', 'open_orders',
    'rtd_connection_status', 'model_loaded', 'queue_depth',
    'memory_usage_bytes', 'cpu_usage_percent',
    'get_metrics_registry', 'get_metrics_output',
    # Logging
    'configure_structured_logging', 'logger',
    # Helpers
    'MetricsContext', 'time_histogram', 'record_trade', 'record_order',
    'record_error', 'record_rtd_event', 'record_rtd_reconnect',
    'record_model_prediction', 'record_scorer_fallback',
    'set_active_positions', 'set_open_orders', 'set_rtd_connection',
    'set_model_loaded', 'set_queue_depth', 'update_system_metrics',
    # Decorators
    'observe_latency', 'count_calls',
    # Context managers
    'track_rtd_event', 'track_model_inference', 'track_feature_computation',
]