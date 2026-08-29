# -*- coding: utf-8 -*-
"""
adapters/ — Camada de I/O e integração externa.

Módulos:
  - rtd_connection: conexão COM com ProfitChart RTD
  - rtd_parser:     parsing de dados RTD (RefreshData, datas, schemas)
  - rtd_writer:     escrita de dados em Parquet (threads, flush, consolidação)
  - profit_rtd:     adapter MarketDataSource (wrapper de alto nível)
  - file_storage:   gravação de dados brutos (JSONL com timestamp ms)
  - dashboard_api:  roteamento HTTP para o dashboard (compatibilidade)
  - dashboard/      dashboard web (api, state, handlers)
  - com_watchdog:   monitor de saúde da conexão COM
"""

from .file_storage import CapturaEventosMS, FileStorage
from .dashboard import DashboardAPI
from .com_watchdog import COMHeartbeatMonitor, COM_WATCHDOG_TIMEOUT_S, COM_WATCHDOG_CHECK_S
from .rtd_connection import (
    conectar_servidor, descobrir_ativos_rtd, preparar_ativos,
    _connect, _refresh, _criar_callback, diagnosticar_rtd,
    fnum, fint, sstr, agora_br,
    NIVEIS_BOOK, LINHAS_TT, POLL_S, EVENT_PUMP_S,
    BOOK_FIELDS, TT_FIELDS,
)
from .rtd_parser import parse_refresh_data, parse_dat, enforce_schema, parse_hms_ms
from .rtd_writer import (
    thread_escritora, thread_escritora_tt,
    flush_buffers_with_retry, write_parquet_part,
    consolidar_book_parquet, consolidar_tt_parquet,
    limpar_pasta,
    BOOK_SCHEMA, TT_SCHEMA,
    _live_inc, _live_get,
    _registrar_stat, _registrar_book, _registrar_tt,
)
from .dashboard import DashboardState, DashboardAPI as DashboardAPINew, DashboardHandlers

__all__ = [
    # File storage
    'CapturaEventosMS', 'FileStorage',
    # COM watchdog
    'COMHeartbeatMonitor', 'COM_WATCHDOG_TIMEOUT_S', 'COM_WATCHDOG_CHECK_S',
    # RTD connection
    'conectar_servidor', 'descobrir_ativos_rtd', 'preparar_ativos',
    '_connect', '_refresh', '_criar_callback', 'diagnosticar_rtd',
    'fnum', 'fint', 'sstr', 'agora_br',
    'NIVEIS_BOOK', 'LINHAS_TT', 'POLL_S', 'EVENT_PUMP_S',
    'BOOK_FIELDS', 'TT_FIELDS',
    # RTD parser
    'parse_refresh_data', 'parse_dat', 'enforce_schema', 'parse_hms_ms',
    # RTD writer
    'thread_escritora', 'thread_escritora_tt',
    'flush_buffers_with_retry', 'write_parquet_part',
    'consolidar_book_parquet', 'consolidar_tt_parquet',
    'limpar_pasta',
    'BOOK_SCHEMA', 'TT_SCHEMA',
    '_live_inc', '_live_get',
    '_registrar_stat', '_registrar_book', '_registrar_tt',
    # Dashboard (old compat)
    'DashboardAPI',
    # Dashboard (new)
    'DashboardState', 'DashboardAPINew', 'DashboardHandlers',
]
