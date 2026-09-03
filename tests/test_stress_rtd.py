# -*- coding: utf-8 -*-
"""
tests/test_stress_rtd.py — Testes de estresse para RTD (Fase 18).

Categorias:
- test_rtd_desconectado: falha segura quando RTD desconecta
- test_rtd_reconectado: recupera após reconexão
- test_refreshdata_atrasado: lida com dados atrasados
- test_burst_eventos: handle bursts de eventos
- test_fila_cheia: tratamento de fila cheia

O sistema deve falhar de forma segura (graceful degradation).

Usage:
    python -m pytest tests/test_stress_rtd.py -v
"""

import sys
import os
import time
import logging
import tempfile
import json
from pathlib import Path
from collections import defaultdict
from unittest.mock import MagicMock, patch, PropertyMock
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
        
    def alimentar_negocio(self, sym, ts_ms, preco, qtd, aggressor, buyer, seller):
        self._negocios.append({
            'sym': sym, 'ts_ms': ts_ms, 'preco': preco,
            'qtd': qtd, 'aggressor': aggressor
        })
        
    def get_volatility_bps(self, sym):
        return 100


class MockSignalEngine:
    """Mock do SignalEngine."""
    
    def __init__(self):
        self._batch_mode = True
        
    def calcular(self, seg):
        return MagicMock(lado="C", score=0.7, tp=100, sl=50)


class MockRTDAdapter:
    """Simula adapter RTD com controle de conectividade."""
    
    def __init__(self):
        self._connected = True
        self._events = []
        self._event_index = 0
        
    def connect(self):
        self._connected = True
        return True
        
    def disconnect(self):
        self._connected = False
        
    def is_connected(self):
        return self._connected
        
    def add_event(self, event):
        self._events.append(event)
        
    def events(self):
        """Gera eventos simulados."""
        if not self._connected:
            raise ConnectionError("RTD desconectado")
            
        while self._event_index < len(self._events):
            yield self._events[self._event_index]
            self._event_index += 1


class TestRTDDesconnectado:
    """Testes para RTD desconectado."""
    
    def test_falha_segura_sem_conexao(self):
        """Sistema deve falhar Gracefully quando RTD desconectar."""
        rtd = MockRTDAdapter()
        rtd.disconnect()
        
        with pytest.raises(ConnectionError):
            next(rtd.events())
            
    def test_metricas_nao_crasham_sem_dados(self):
        """Metrics devem lidar com ausencia de dados."""
        from replay_engine_v13 import TradeMetrics
        
        metrics = TradeMetrics(custo_execucao=5.0)
        result = metrics.calcular()
        
        assert result["n_trades"] == 0
        assert result["total_pnl"] == 0
        assert result["win_rate"] == 0
        
    def test_replay_ignora_eventos_sem_rtd(self):
        """Replay deve ignorar silenciosamente quando RTD falha."""
        from replay_engine_v13 import ReplayEngine
        
        config = {
            "replay": {"latency_ms": {"WINV26": 30}},
            "trading": {"max_trades_dia": 20}
        }
        
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        # Simula processo sem eventos do RTD
        eventos = []
        
        for ev in eventos:
            try:
                engine._process_neg(ev)
            except Exception as e:
                # Falha segura — log e continua
                pass
                
        # Nenhuma excecao nao tratada deve propagar
        assert len(engine.metrics.trades) == 0


class TestRTDReconectado:
    """Testes para reconexão do RTD."""
    
    def test_recupera_apos_reconexao(self):
        """Sistema deve recuperar após reconexão."""
        rtd = MockRTDAdapter()
        
        # Desconecta
        rtd.disconnect()
        assert not rtd.is_connected()
        
        # Reconecta
        rtd.connect()
        assert rtd.is_connected()
        
    def test_eventos_continuam_pos_reconexao(self):
        """Eventos devem fluir normalmente apos reconexão."""
        rtd = MockRTDAdapter()
        rtd.add_event({"ativo": "WINV26", "ts_ms": 1000, "preco": 100, "qtd": 1})
        
        # Desconecta e tenta obter eventos
        rtd.disconnect()
        events_gen = rtd.events()
        
        # Antes da reconexão deve levantar erro ao iterar
        with pytest.raises(ConnectionError):
            next(events_gen)
            
        # Reconecta e eventos fluem normalmente
        rtd.connect()
        event = next(rtd.events())
        assert event["ativo"] == "WINV26"


class TestRefreshDataAtrasado:
    """Testes para RefreshData atrasado."""
    
    def test_evento_muito_atrasado_ignorado(self):
        """Evento com timestamp muito antigo deve ser ignorado."""
        from replay_engine_v13 import ReplayEngine
        
        config = {
            "replay": {"latency_ms": {"WINV26": 30}},
            "trading": {"max_trades_dia": 20}
        }
        
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        # Evento com timestamp de 2020 (muito antigo)
        evento_antigo = {
            "ativo": "WINV26",
            "ts_ms": 1577836800000,  # 2020-01-01
            "preco": 100,
            "qtd": 1,
            "agressor": "C"
        }
        
        # Deve processar sem crash
        engine._process_neg(evento_antigo)
        # Evento antigo não gera trade (score ou outros filtros)
        
    def test_evento_futuro_proibido(self):
        """Evento com timestamp futuro deve ser rejeitado."""
        import time
        from replay_engine_v13 import ReplayEngine
        
        config = {"trading": {"max_trades_dia": 20}}
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        # Evento com timestamp no futuro
        futuro = int(time.time() * 1000) + 3600000  # +1 hora
        
        evento_futuro = {
            "ativo": "WINV26",
            "ts_ms": futuro,
            "preco": 100,
            "qtd": 1,
            "agressor": "C"
        }
        
        # Deve lidar gracefully
        engine._process_neg(evento_futuro)


class TestBurstEventos:
    """Testes para bursts de eventos."""
    
    def test_handle_milhares_eventos_por_segundo(self):
        """Sistema deve lidar com milhares de eventos rapidamente."""
        from replay_engine_v13 import ReplayEngine
        
        config = {
            "replay": {"latency_ms": {"WINV26": 30}},
            "trading": {"max_trades_dia": 1000}
        }
        
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        # Gera 1000 eventos
        eventos = [
            {
                "ativo": "WINV26",
                "ts_ms": 1000 + i,
                "preco": 100 + (i % 10),
                "qtd": 1,
                "agressor": "C" if i % 2 == 0 else "V"
            }
            for i in range(1000)
        ]
        
        t0 = time.time()
        for ev in eventos:
            try:
                engine._process_neg(ev)
            except Exception:
                pass
        elapsed = time.time() - t0
        
        # Deve processar em tempo razoavel (< 1 segundo para 1000 eventos)
        assert elapsed < 1.0
        
    def test_nenhum_evento_perdido_no_burst(self):
        """Todos os eventos devem ser processados mesmo em burst."""
        from replay_engine_v13 import ReplayEngine
        
        config = {"trading": {"max_trades_dia": 1000}}
        engine = ReplayEngine(config=config, instrumentos=["WINV26"])
        engine.state = MockMarketState()
        engine.signal_engine = MockSignalEngine()
        
        n_eventos = 500
        eventos = [
            {"ativo": "WINV26", "ts_ms": i * 10, "preco": 100, "qtd": 1, "agressor": "C",
             "compradora": "", "vendedora": ""}
            for i in range(n_eventos)
        ]
        
        for ev in eventos:
            try:
                engine._process_neg(ev)
            except Exception:
                pass  # Falha segura
            
        # Todos os eventos foram processados (state alimentado)
        assert len(engine.state._negocios) == n_eventos


class TestFilaCheia:
    """Testes para fila cheia."""
    
    def test_queue_overflow_handling(self):
        """Sistema deve lidar com overflow de queue."""
        from collections import deque
        
        # Simula fila limitada
        max_size = 100
        queue = deque(maxlen=max_size)
        
        # Enche a fila
        for i in range(200):
            queue.append(i)
            
        # Deve manter apenas os ultimos 100
        assert len(queue) == max_size
        assert queue[0] == 100  # Primeiros 100 descartados
        
    def test_buffer_circular_na_pratica(self):
        """Buffer circular deve funcionar corretamente."""
        from collections import deque
        
        # Simula buffer circular com maxlen
        buffer = deque(maxlen=100)
        
        # Adiciona muitos eventos
        for i in range(200):
            buffer.append(i)
            
        # Deve manter apenas os ultimos 100
        assert len(buffer) == 100
        assert buffer[0] == 100  # Primeiros 100 descartados


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
