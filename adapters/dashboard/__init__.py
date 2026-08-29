# -*- coding: utf-8 -*-
"""
adapters/dashboard/ — Dashboard web para visualização de dados RTD.

Separa responsabilidades do antigo dashboard_api.py:
  - api.py:     Roteamento HTTP (DashboardAPI)
  - state.py:   Estado compartilhado (DashboardState)
  - handlers.py: Lógica de negócio dos endpoints (handlers individuais)
"""

from .state import DashboardState
from .api import DashboardAPI
from .handlers import DashboardHandlers

__all__ = ['DashboardState', 'DashboardAPI', 'DashboardHandlers']
