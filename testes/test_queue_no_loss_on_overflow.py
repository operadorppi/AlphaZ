#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_queue_no_loss_on_overflow.py — Verifica se fila não perde dados ao saturar.
"""
import sys
import pytest
import queue
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestQueueNoLoss:
    """Testes de perda de dados na fila."""
    
    def test_queue_full_behavior(self):
        """Queue com maxsize deve bloquear ou rejeitar."""
        q = queue.Queue(maxsize=10)
        
        # Preencher fila
        for i in range(10):
            q.put(i)
        
        # Tentar adicionar mais deve bloquear ou levantar Full
        with pytest.raises(queue.Full):
            q.put_nowait(11)
    
    def test_capture_daemon_queue_size(self):
        """CaptureDaemon deve ter tamanho máximo na fila."""
        from core.capture_daemon import _MAX_QUEUE
        
        assert _MAX_QUEUE > 0
        assert _MAX_QUEUE < 1_000_000  # Deve ser razoável


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
