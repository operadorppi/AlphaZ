# -*- coding: utf-8 -*-
"""
captura_eventos_ms.py — SHIM DE COMPATIBILIDADE.

A classe CapturaEventosMS foi movida para adapters/file_storage.py.
Este arquivo existe para nao quebrar imports antigos:
    from captura_eventos_ms import CapturaEventosMS

Gradualmente, todos os imports serao atualizados para:
    from adapters.file_storage import FileStorage
"""

from adapters.file_storage import CapturaEventosMS, FileStorage

__all__ = ['CapturaEventosMS', 'FileStorage']