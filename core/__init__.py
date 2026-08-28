# -*- coding: utf-8 -*-
"""
core/ — Domínio do motor de trading.

Módulos:
  contracts         — Dataclasses partilhados
  event_clock        — Relógio mestre
  market_state       — Estado de mercado (historico, book, stats, trackers)
  persistence        — I/O (trades, decisões, checkpoints)
  metrics            — Métricas de desempenho
  regime_detector    — Regime de mercado (direção × volatilidade)
  learning           — Aprendizado de pesos (MFE/MAE, decay)
  risk_manager       — Gate de risco (circuit breaker, TP/SL, horário)
  position_manager   — Gestão de posições
  signal_engine      — Scoring e sinais
  app                — Orquestrador principal (façade)
"""
