#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_book_timestamp_sync.py — Verifica sincronização de timestamps entre Book e T&T.
"""
import sys
import pytest
from pathlib import Path

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

class TestBookTimestampSync:
    """Testes de sincronização de timestamps."""
    
    def test_book_uses_ts_ms(self):
        """Book deve usar ts_ms como timestamp."""
        import inspect
        from adapters.rtd_writer import thread_escritora
        
        src = inspect.getsource(thread_escritora)
        assert 'ts_ms' in src or 'time_ms' in src
    
    def test_tt_uses_ts_ms(self):
        """T&T deve usar ts_ms como timestamp."""
        import inspect
        from adapters.rtd_writer import thread_escritora_tt
        
        src = inspect.getsource(thread_escritora_tt)
        assert 'ts_ms' in src or 'time_ms' in src


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
