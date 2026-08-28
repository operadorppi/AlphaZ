# -*- coding: utf-8 -*-
"""
adapters/ — Camada de I/O e integração externa.

- profit_rtd:    conexão COM com ProfitChart RTD
- file_storage:  gravação de dados brutos (JSONL/Parquet)
- dashboard_api:  roteamento HTTP para o dashboard
"""

from .file_storage import CapturaEventosMS, FileStorage
from .dashboard_api import DashboardAPI
from .com_watchdog import COMHeartbeatMonitor, COM_WATCHDOG_TIMEOUT_S, COM_WATCHDOG_CHECK_S

__all__ = [
    'CapturaEventosMS', 'FileStorage',
    'DashboardAPI',
    'COMHeartbeatMonitor', 'COM_WATCHDOG_TIMEOUT_S', 'COM_WATCHDOG_CHECK_S',
]
