# -*- coding: utf-8 -*-
"""
tests/test_replay_realistic_execution.py — Testes para execucao realista (FASE 17).

Categorias:
- test_latency_simulation: verifica atraso de execucao
- test_variable_spread: spread dinâmico baseado em volatilidade
- test_variable_slippage: slippage proporcional ao volume
- test_partial_execution: ordens parciais
- test_order_rejection: rejeicao por circuit breaker
- test_execution_costs: custos de execucao
- test_intraday_stop: stop intrabar
- test_queue_priority: prioridade de fila

Usage:
    python -m pytest tests/test_replay_realistic_execution.py -v
"""

import sys
import os
import json
import time
import logging
from pathlib import Path
from collections import defaultdict
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

# Adiciona o root ao path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

logging.basicConfig(level=logging.WARNING)


class MockMarketState:
    """Mock do MarketState para testes isolados."""
    
    def __init__(self, config=None):
        self.config = config or {}
        self._negocios = []
        self._book = {}
        
    def alimentar_negocio(self, sym, ts_ms, preco, qtd, aggressor, buyer, seller):
        self._negocios.append({
            'sym': sym, 'ts_ms': ts_ms, 'preco': preco,
            'qtd': qtd, 'aggressor': aggressor
        })
        
    def get_volatility_bps(self, sym):
        """Retorna volatilidade simulada."""
        return 100  # 100 bps = 1%


class MockSignalEngine:
    """Mock do SignalEngine."""
    
    def __init__(self):
        self._batch_mode = True
        
    def calcular(self, seg):
        """Retorna sinal mock."""
        return MagicMock(
            lado="C",
            score=0.7,
            tp=100,
            sl=50
        )


class MockScorer:
    """Mock do ScorerML."""
    
    def evento(self, sym, ts_ms, price, qty, aggressor, buyer, seller):
        pass


class TestExecutionSimulator:
    """Testes para o ExecutionSimulator."""
    
    def setup_method(self):
        self.config = {
            "replay": {
                "latency_ms": {"WINV26": 50, "WDOU26": 20},
                "execution_costs": {"WINV26": 5.0, "WDOU26": 1.0},
                "partial_fill_threshold": 0.8,
                "rejection_probability": {
                    "circuit_breaker": 1.0,
                    "daily_limit": 1.0,
                    "spread_excessive": 0.5
                }
            },
            "trading": {"max_trades_dia": 15}
        }
        from replay_engine_v13 import ExecutionSimulator
        self.sim = ExecutionSimulator(self.config)
        
    def test_latency_configuravel(self):
        """Latencia deve ser configuravel por ativo."""
        assert self.sim.latency_ms["WINV26"] == 50
        assert self.sim.latency_ms["WDOU26"] == 20
        
    def test_spread_variavel_com_volatilidade(self):
        """Spread deve aumentar com volatilidade."""
        spread_baixo = self.sim.calculate_spread("WINV26", volatility_bps=50)
        spread_alto = self.sim.calculate_spread("WINV26", volatility_bps=200)
        
        assert spread_alto > spread_baixo
        
    def test_slippage_proporcional_volume(self):
        """Slippage deve aumentar com volume da ordem."""
        slip_pequeno = self.sim.calculate_slippage("WINV26", order_volume=10, price=100)
        slip_grande = self.sim.calculate_slippage("WINV26", order_volume=200, price=100)
        
        assert slip_grande > slip_pequeno
        
    def test_simulacao_execucao_compra(self):
        """Execucao de compra deve adicionar spread + slippage."""
        result = self.sim.simulate_execution(
            sym="WINV26", lado="C", preco_sinal=1000,
            volume=1, ts_ms=0, volatility_bps=100
        )
        
        assert result["exec_price"] > 1000
        assert result["slippage_applied"] > 0
        assert result["spread_applied"] > 0
        
    def test_simulacao_execucao_venda(self):
        """Execucao de venda deve subtrair spread + slippage."""
        result = self.sim.simulate_execution(
            sym="WINV26", lado="V", preco_sinal=1000,
            volume=1, ts_ms=0, volatility_bps=100
        )
        
        assert result["exec_price"] < 1000
        
    def test_ordem_nao_rejeitada_normalemente(self):
        """Ordem nao deve ser rejeitada em condicoes normais."""
        # Usa volatilidade baixa para evitar rejeicao por spread excessivo
        rejected, reason = self.sim.check_order_rejection(
            sym="WINV26", signal_score=0.7, current_spread=5,  # baixo
            daily_trades=defaultdict(int), daily_pnl=0
        )
        
        assert not rejected
        assert reason is None
        
    def test_ordem_rejeita_circuit_breaker(self):
        """Ordem deve ser rejeitada se circuit breaker ativo."""
        rejected, reason = self.sim.check_order_rejection(
            sym="WINV26", signal_score=0.7, current_spread=100,
            daily_trades=defaultdict(int), daily_pnl=0,
            circuit_breaker_active=True
        )
        
        assert rejected
        assert reason == "CIRCUIT_BREAKER"
        
    def test_ordem_rejeita_limite_diario(self):
        """Ordem deve ser rejeitada se limite diario atingido."""
        daily_trades = defaultdict(int, {"WINV26": 15})  # max_trades_dia = 15
        rejected, reason = self.sim.check_order_rejection(
            sym="WINV26", signal_score=0.7, current_spread=100,
            daily_trades=daily_trades, daily_pnl=0
        )
        
        assert rejected
        assert reason == "DAILY_LIMIT"
        
    def test_reset_day(self):
        """Reset de dia deve limpar estado."""
        self.sim._cumulative_pnl = -100
        self.sim.reset_day()
        
        assert self.sim._cumulative_pnl == 0.0


class TestTradeMetricsRealistic:
    """Testes para TradeMetrics com execucao realista."""
    
    def test_registrar_rejeicao(self):
        """Deve registrar rejeicoes corretamente."""
        from replay_engine_v13 import TradeMetrics
        metrics = TradeMetrics(custo_execucao=5.0)
        
        metrics.registrar_rejeicao("C", "CIRCUIT_BREAKER", ts_ms=1000)
        metrics.registrar_rejeicao("V", "DAILY_LIMIT", ts_ms=2000)
        
        result = metrics.calcular()
        
        assert result["n_rejeicoes"] == 2
        
    def test_gate_com_rejeicoes(self):
        """Gate deve considerar rejeicoes."""
        from replay_engine_v13 import TradeMetrics
        metrics = TradeMetrics(custo_execucao=5.0)
        
        # Adiciona trades
        metrics.registrar("C", 1000, 1100, "TP")
        metrics.registrar("C", 1000, 950, "SL")
        
        # Adiciona rejeicoes
        metrics.registrar_rejeicao("C", "SPREAD_EXCESSIVE")
        
        gate = metrics.gate()
        
        # Verifica que o gate inclui informacao de rejeicoes no motivo
        assert "REJ=1" in gate["motivo"]
        # Gate ainda aprovado pois trades passam nos criterios
        assert gate["aprovado"] == True


class TestReplayEngineRealistic:
    """Testes para ReplayEngine com execucao realista."""
    
    def setup_method(self):
        self.config = {
            "replay": {
                "latency_ms": {"WINV26": 50, "WDOU26": 20},
                "execution_costs": {"WINV26": 5.0, "WDOU26": 1.0},
                "partial_fill_threshold": 0.8,
            },
            "trading": {
                "max_trades_dia": 15,
                "cooldown_entre_trades_ms": 5000
            }
        }
        
    def test_checar_entrada_com_simulacao(self):
        """Checar entrada deve aplicar simulacao de execucao."""
        from replay_engine_v13 import ReplayEngine
        
        # Cria engine sem dependencias externas
        engine = ReplayEngine(config=self.config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        # Simula sinal com score alto para evitar rejeicao por spread
        sig = MagicMock(lado="C", score=0.7)
        
        # Mocka o check_order_rejection para sempre retornar False (sem rejeicao)
        original_check = engine.exec_sim.check_order_rejection
        engine.exec_sim.check_order_rejection = MagicMock(return_value=(False, None))
        
        try:
            # Chama checar_entrada
            engine._checar_entrada("WINV26", sig, preco=1000, ts_ms=1000)
            
            # Verifica posicao aberta com slippage aplicado
            assert engine._posicao is not None
            assert engine._posicao["entrada"] != 1000  # Preço ajustado por slippage
            assert "slippage_applied" in engine._posicao
        finally:
            engine.exec_sim.check_order_rejection = original_check
        
    def test_checar_saida_com_stop_intrabar(self):
        """Stop intrabar deve triggerar saida."""
        from replay_engine_v13 import ReplayEngine
        
        engine = ReplayEngine(config=self.config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        
        # Abre posicao
        engine._posicao = {
            "lado": "C",
            "entrada": 1000,
            "tp": 100,
            "sl": 50,
            "aberta_em": 1000,
            "ativo": "WINV26"
        }
        
        # Simula preco abaixo do stop (queda de 60 pts)
        engine._checar_saida("WINV26", preco=940, ts_ms=2000)
        
        # Verifica se posicao foi fechada
        assert engine._posicao is None
        assert len(engine.metrics.trades) == 1
        assert engine.metrics.trades[0]["motivo"] == "SL_INTRABAR"
        
    def test_checar_entrada_rejeitada(self):
        """Entrada rejeitada nao deve abrir posicao."""
        from replay_engine_v13 import ReplayEngine
        
        engine = ReplayEngine(config=self.config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.exec_sim._daily_loss_limit = 0  # Trigger daily loss stop
        
        # Simula sinal com PNL diario no limite
        sig = MagicMock(lado="C", score=0.7)
        
        # Chama checar_entrada (deve rejeitar)
        engine._checar_entrada("WINV26", sig, preco=1000, ts_ms=1000)
        
        # Verifica que nenhuma posicao foi aberta
        assert engine._posicao is None
        assert len(engine.metrics.rejeicoes) > 0
        
    def test_metricas_registram_execucoes_parciais(self):
        """Metricas devem registrar execucoes parciais."""
        from replay_engine_v13 import TradeMetrics
        
        metrics = TradeMetrics(custo_execucao=5.0)
        metrics.registrar("C", 1000, 1050, "TP", parcial=True)
        
        result = metrics.calcular()
        
        assert result["n_parciais"] == 1
        assert result["trades"][0]["parcial"] == True


class TestReplayIntegration:
    """Testes de integracao do replay realista."""
    
    def test_fluxo_completo_paper(self):
        """Testa fluxo completo de replay paper mode."""
        from replay_engine_v13 import ReplayEngine, TradeMetrics
        
        config = {
            "replay": {
                "latency_ms": {"WINV26": 30},
                "execution_costs": {"WINV26": 3.0}
            },
            "trading": {"max_trades_dia": 20}
        }
        
        # Cria engine sem dependencias externas
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        
        # Verifica inicializacao
        assert engine.exec_sim is not None
        assert engine.metrics.custo_execucao == 5.0  # default
        
    def test_custo_execucao_por_ativo(self):
        """Custo de execucao deve variar por ativo."""
        from replay_engine_v13 import ExecutionSimulator
        
        config = {
            "replay": {
                "execution_costs": {
                    "WINV26": 5.0,
                    "WDOU26": 1.0
                }
            }
        }
        
        sim = ExecutionSimulator(config)
        
        assert sim.execution_costs["WINV26"] == 5.0
        assert sim.execution_costs["WDOU26"] == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
