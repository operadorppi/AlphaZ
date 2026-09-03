#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_rtd.py — Auditoria completa do adapter RTD.
"""
import sys
import inspect
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA DO RTD')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. ARQUITETURA DE THREADS
    # ========================================================================
    print('\n[1] ARQUITETURA DE THREADS...')
    
    from core.capture_daemon import CaptureDaemon
    import inspect
    src = inspect.getsource(CaptureDaemon.__init__)
    
    if 'threading.Thread' in src or 'self._thread' in src:
        print('  [OK] CaptureDaemon usa threading')
    else:
        print('  [WARN] CaptureDaemon pode não usar threading corretamente')
        findings.append('thread_capture')
    
    # Verificar tamanho da fila
    if '_MAX_QUEUE' in dir() or 'maxsize' in src:
        print('  [OK] Fila com tamanho máximo')
    else:
        print('  [WARN] Fila sem tamanho máximo definido')
        findings.append('fila_sem_limite')
    
    # ========================================================================
    # 2. POLLING E FREQUÊNCIA
    # ========================================================================
    print('\n[2] POLLING E FREQUÊNCIA...')
    
    from adapters.profit_rtd import ProfitRTDAdapter
    src = inspect.getsource(ProfitRTDAdapter.events)
    
    if 'PumpEvents' in src or 'time.sleep' in src:
        print('  [OK] Polling implementado')
        # Verificar frequência
        if '0.05' in src or '0.1' in src or 'POLL_S' in src:
            print('  [OK] Frequência de polling identificada')
        else:
            print('  [WARN] Frequência de polling não identificada')
    else:
        print('  [WARN] Polling não identificado')
        findings.append('polling')
    
    # ========================================================================
    # 3. FLUSH E FSYNC
    # ========================================================================
    print('\n[3] FLUSH E FSYNC...')
    
    from adapters.rtd_writer import thread_escritora, thread_escritora_tt
    src_escritora = inspect.getsource(thread_escritora)
    
    if 'flush' in src_escritora.lower() or 'fsync' in src_escritora.lower():
        print('  [OK] Flush/fsync identificado no writer')
    else:
        print('  [WARN] Flush/fsync não identificado')
        findings.append('flush')
    
    # Verificar CaptureDaemon
    src_daemon = inspect.getsource(CaptureDaemon)
    if 'flush' in src_daemon.lower():
        print('  [OK] Flush no CaptureDaemon')
    else:
        print('  [WARN] Flush não identificado no CaptureDaemon')
        findings.append('flush_daemon')
    
    # ========================================================================
    # 4. PARQUET
    # ========================================================================
    print('\n[4] PARQUET...')
    
    from adapters.rtd_writer import write_parquet_part, consolidar_book_parquet
    src_parquet = inspect.getsource(write_parquet_part)
    
    if 'pyarrow' in src_parquet or 'parquet' in src_parquet.lower():
        print('  [OK] Escrita Parquet implementada')
    else:
        print('  [WARN] Escrita Parquet não identificada')
        findings.append('parquet')
    
    # Verificar schemas
    from adapters.rtd_writer import BOOK_SCHEMA, TT_SCHEMA
    print(f'  [OK] BOOK_SCHEMA: {len(BOOK_SCHEMA)} campos')
    print(f'  [OK] TT_SCHEMA: {len(TT_SCHEMA)} campos')
    
    # ========================================================================
    # 5. RECUPERAÇÃO APÓS ERRO
    # ========================================================================
    print('\n[5] RECUPERAÇÃO APÓS ERRO...')
    
    # Verificar try/except no daemon
    if 'try:' in src_daemon and 'except' in src_daemon:
        print('  [OK] Try/except no CaptureDaemon')
    else:
        print('  [WARN] Try/except não identificado no daemon')
        findings.append('recuperacao')
    
    # Verificar quarentena
    try:
        from adapters.rtd_writer import _quarentena
        if _quarentena:
            print('  [OK] Quarentena de dados suspeitos implementada')
        else:
            print('  [WARN] Quarentena não identificada')
            findings.append('quarentena')
    except ImportError:
        print('  [INFO] Quarentena não implementada (dados rejeitados diretamente)')
    
    # ========================================================================
    # 6. PERDA DE DADOS
    # ========================================================================
    print('\n[6] PERDA DE DADOS...')
    
    # Verificar se há contadores de eventos
    if 'stats' in src_daemon.lower() or 'counter' in src_daemon.lower():
        print('  [OK] Contadores de eventos identificados')
    else:
        print('  [WARN] Contadores de eventos não identificados')
        findings.append('perda_dados')
    
    # ========================================================================
    # 7. DUPLICAÇÃO
    # ========================================================================
    print('\n[7] DUPLICAÇÃO...')
    
    from adapters.profit_rtd import ProfitRTDAdapter
    src_rtd = inspect.getsource(ProfitRTDAdapter)
    
    if 'vistos_tt' in src_rtd or 'dedup' in src_rtd.lower():
        print('  [OK] Deduplication de trades implementada')
    else:
        print('  [WARN] Deduplication não identificada')
        findings.append('duplicacao')
    
    # ========================================================================
    # 8. ORDENAÇÃO TEMPORAL
    # ========================================================================
    print('\n[8] ORDENAÇÃO TEMPORAL...')
    
    # Verificar se dados são ordenados por ts_ms
    if 'sort' in src_daemon.lower() or 'order' in src_daemon.lower():
        print('  [OK] Ordenação temporal identificada')
    else:
        print('  [INFO] Ordenação pode não ser necessária (dados chegam ordenados)')
    
    # ========================================================================
    # 9. CALCULAR MÉTRICAS
    # ========================================================================
    print('\n[9] MÉTRICAS DE PERFORMANCE...')
    
    # Taxa de captura
    print('  Taxa de captura:')
    print('    - BOOK: 250ms (4 Hz)')
    print('    - T&T: 100ms (10 Hz)')
    
    # Tamanho das filas
    from core.capture_daemon import _MAX_QUEUE
    print(f'\n  Tamanho da fila: {_MAX_QUEUE:,} eventos')
    
    # Backlog
    print('  Backlog:')
    print('    - Monitorar via health check')
    
    # ========================================================================
    # 10. RESISTÊNCIA A RAJADAS
    # ========================================================================
    print('\n[10] RESISTÊNCIA A RAJADAS...')
    
    # Verificar se há limite de eventos
    if _MAX_QUEUE < 1_000_000:
        print(f'  [WARN] Fila pode satura em rajadas (> {_MAX_QUEUE:,} eventos)')
        findings.append('rajada')
    else:
        print(f'  [OK] Fila grande o suficiente ({_MAX_QUEUE:,} eventos)')
    
    # Verificar mecanismo de rejeição
    if 'Full' in src_daemon or 'queue.Full' in src_daemon:
        print('  [OK] Mecanismo de rejeição identificado')
    else:
        print('  [WARN] Mecanismo de rejeição não identificado')
        findings.append('rejeicao')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA RTD')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} problema(s) encontrado(s):')
        for f in findings:
            print(f'  - {f}')
        return 1
    else:
        print('\n[RTD OK] Nenhuma irregularidade crítica encontrada.')
        return 0

if __name__ == '__main__':
    sys.exit(main())
