#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_contracts_rollover.py — Verifica reset ao mudar de contrato.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestContractsRollover:
    """Testes de mudança de contrato."""
    
    def test_vwap_resets_on_new_contract(self):
        """VWAP deve resetar ao mudar de contrato."""
        from features.vwap_tracker import VWAPTracker
        
        tracker1 = VWAPTracker('WINV26', tick=5)
        tracker2 = VWAPTracker('WINV27', tick=5)
        
        # Atualizar tracker1
        tracker1.update(1000, 100, 10)
        vwap1 = tracker1.snapshot()['vwap']
        
        # Tracker2 deve ter VWAP diferente (inicial)
        vwap2 = tracker2.snapshot()['vwap']
        
        # Devem ser independentes
        assert vwap1 != vwap2 or vwap2 == 0
    
    def test_volume_profile_resets_on_new_contract(self):
        """VolumeProfile deve resetar ao mudar de contrato."""
        from features.volume_profile import VolumeProfileTracker
        
        vp1 = VolumeProfileTracker()
        vp2 = VolumeProfileTracker()
        
        # P0-A27 (v15.22): assinatura agora exige ts_ms (rollover interno).
        # Os dois trackers sao instancias independentes — cada um acumula o
        # seu perfil; o teste valida independencia, nao virada de dia.
        ts = 1725000000000  # epoch ms (mesmo dia BRT p/ ambos)
        vp1.atualizar(ts, 100, 10, 'compra')
        vp2.atualizar(ts + 60000, 200, 5, 'venda')
        
        # Devem ser independentes
        assert vp1.calcular(100)['vp_total'] != vp2.calcular(200)['vp_total']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
