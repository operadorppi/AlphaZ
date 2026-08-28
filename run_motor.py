#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_motor.py — Entry point unificado do motor (v10.0).

A partir da v10.0, este arquivo usa core/app.py (orquestrador em camadas).
O motor_rt_alphaz.py original foi arquivado para docs/archive/.

Uso:
  python run_motor.py
  python run_motor.py WINV26 WDOU26

Watchdog e Task Scheduler apontam para este arquivo.
"""
import sys
import os
import signal
import logging

# Garante que a raiz do projeto está no path
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Run")

from core.app import App

if __name__ == "__main__":
    import os
    from config import get_config_dict
    
    if os.name == 'nt':
        from adapters.profit_rtd import ProfitRTDAdapter
        log.info("[RUN] Iniciando em modo Windows LIVE (Profit RTD)")
        cfg = get_config_dict()
        ds = ProfitRTDAdapter(cfg)
    else:
        log.warning("[RUN] Ambiente não-Windows. Para testar, use o ReplayEngine ou implemente um MockAdapter.")
        sys.exit(1)

    app = App(data_source=ds, config=cfg)
    signal.signal(signal.SIGINT, lambda s, f: app.parar())
    signal.signal(signal.SIGTERM, lambda s, f: app.parar())
    app.run()
