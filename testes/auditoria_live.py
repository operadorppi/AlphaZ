#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_live.py — Auditoria comparativa LIVE vs BACKTEST.
"""
import sys
import inspect
from pathlib import Path
from collections import defaultdict

_root = Path('.').resolve()
sys.path.insert(0, str(_root))

def main():
    print('='*70)
    print('AUDITORIA LIVE vs BACKTEST')
    print('='*70)
    
    findings = []
    
    # ========================================================================
    # 1. COMPARAR FEATURES ENTRE BATCH E LIVE
    # ========================================================================
    print('\n[1] COMPARANDO FEATURES ENTRE BATCH E LIVE...')
    
    # Features do batch (dataset_builder)
    batch_features = set()
    dataset_builder = _root / 'ml' / 'dataset_builder.py'
    if dataset_builder.exists():
        content = dataset_builder.read_text(encoding='utf-8')
        # Procurar por colunas/ features mencionadas
        import re
        # Padrões comuns de nomes de features
        patterns = re.findall(r"'([a-z_]+)'", content)
        batch_features.update(patterns)
    
    # Features do live (scorer)
    live_features = set()
    scorer_file = _root / 'ml' / 'scorer.py'
    if scorer_file.exists():
        content = scorer_file.read_text(encoding='utf-8')
        # Procurar por assignments de row[...]
        patterns = re.findall(r"row\['([a-z_]+)'\]", content)
        live_features.update(patterns)
        # Também procurar por self.regime snapshot
        patterns = re.findall(r"result\['([a-z_]+)'\]", content)
        live_features.update(patterns)
    
    print(f'  Features no batch: {len(batch_features)}')
    print(f'  Features no live: {len(live_features)}')
    
    # Encontrar diferenças
    only_batch = batch_features - live_features
    only_live = live_features - batch_features
    
    if only_batch:
        print(f'\n  [WARN] Features no batch mas NÃO no live ({len(only_batch)}):')
        for f in sorted(only_batch)[:20]:
            print(f'    - {f}')
        findings.append(f'features_somente_batch:{len(only_batch)}')
    
    if only_live:
        print(f'\n  [INFO] Features no live mas NÃO no batch ({len(only_live)}):')
        for f in sorted(only_live)[:20]:
            print(f'    - {f}')
    
    # ========================================================================
    # 2. VERIFICAR RESET DIÁRIO
    # ========================================================================
    print('\n[2] VERIFICANDO RESET DIÁRIO...')
    
    trackers = [
        ('VWAPTracker', 'features.vwap_tracker'),
        ('VolumeProfileTracker', 'features.volume_profile'),
        ('KyleLambdaTracker', 'features.kyle_lambda'),
        ('VolumeRelativoTracker', 'features.volume_relativo'),
        ('PocMigrationTracker', 'features.poc_migration'),
        ('RegimeTracker', 'ml.scorer'),
    ]
    
    reset_issues = []
    for name, module in trackers:
        try:
            mod = __import__(module, fromlist=[name])
            cls = getattr(mod, name)
            has_reset = hasattr(cls, 'reset_diario')
            print(f'  {name}: {"OK" if has_reset else "FAIL"} (reset_diario)')
            if not has_reset:
                reset_issues.append(name)
        except Exception as e:
            print(f'  {name}: ERROR ({e})')
            reset_issues.append(name)
    
    if reset_issues:
        print(f'\n  [FAIL] {len(reset_issues)} trackers sem reset_diario:')
        for t in reset_issues:
            print(f'    - {t}')
        findings.append(f'reset_diario_ausente:{len(reset_issues)}')
    
    # ========================================================================
    # 3. VERIFICAR BUFFERS QUE ACUMULAM DADOS
    # ========================================================================
    print('\n[3] VERIFICANDO BUFFERS...')
    
    # Verificar ScorerML
    from ml.scorer import ScorerML
    import inspect
    src = inspect.getsource(ScorerML.__init__)
    
    # Procurar por listas/dicionários que podem acumular
    buffer_patterns = [
        ('_precos', 'Lista de preços'),
        ('_vwap_history', 'Histórico VWAP'),
        ('_cvd_history', 'Histórico CVD'),
    ]
    
    for attr, desc in buffer_patterns:
        if attr in src:
            print(f'  {attr} ({desc}): presente')
            # Verificar se há limitação de tamanho
            scorer_src = inspect.getsource(ScorerML)
            if f'self.{attr}' in scorer_src:
                # Procurar por limitação
                if f'len(self.{attr})' in scorer_src or f'{attr}[-' in scorer_src:
                    print(f'    [OK] Limitado')
                else:
                    print(f'    [WARN] Pode acumular indefinidamente')
                    findings.append(f'buffer_sem_limite:{attr}')
    
    # ========================================================================
    # 4. VERIFICAR TIMESTAMP CONSISTENCY
    # ========================================================================
    print('\n[4] VERIFICANDO TIMESTAMPS...')
    
    # Verificar se batch e live usam o mesmo formato
    batch_ts_check = True
    live_ts_check = True
    
    # Batch usa ts_ms do JSONL
    batch_file = _root / 'ml' / 'batch_processor.py'
    if batch_file.exists():
        content = batch_file.read_text(encoding='utf-8')
        if 'ts_ms' not in content:
            print('  [WARN] batch_processor pode não usar ts_ms corretamente')
            batch_ts_check = False
    
    # Live usa ts_ms dos eventos
    if 'ts_ms' in src or 'ts_ms' in inspect.getsource(ScorerML._prever):
        print('  [OK] Live usa ts_ms')
    else:
        print('  [WARN] Live pode não usar ts_ms corretamente')
        live_ts_check = False
    
    if not (batch_ts_check and live_ts_check):
        findings.append('timestamp_inconsistente')
    
    # ========================================================================
    # 5. VERIFICAR CALCULOS DIFERENTES
    # ========================================================================
    print('\n[5] VERIFICANDO CÁLCULOS...')
    
    # VWAP
    from features.vwap_tracker import VWAPTracker
    vwap_src = inspect.getsource(VWAPTracker.update)
    if 'cumsum' in vwap_src and 'preco' in vwap_src and 'qtd' in vwap_src:
        print('  [OK] VWAP: cálculo causal (cumsum preco*qtd / cumsum qtd)')
    else:
        print('  [WARN] VWAP: verificar cálculo')
        findings.append('vwap_calculo_diferente')
    
    # ATR
    if 'atr_alpha' in src and '2.0 / 15.0' in src:
        print('  [OK] ATR: alpha = 2/15 (consistente com batch)')
    else:
        print('  [WARN] ATR: verificar alpha')
        findings.append('atr_alpha_diferente')
    
    # ========================================================================
    # 6. VERIFICAR RACE CONDITIONS
    # ========================================================================
    print('\n[6] VERIFICANDO CONCURRENÊNCIA...')
    
    # Verificar uso de locks
    app_file = _root / 'core' / 'app.py'
    if app_file.exists():
        content = app_file.read_text(encoding='utf-8')
        if 'threading.Lock' in content or 'threading.RLock' in content:
            print('  [OK] Uso de locks identificado')
        else:
            print('  [WARN] Nenhum lock encontrado no app.py')
            findings.append('possivel_race_condition')
    
    # Verificar MarketState
    from core.market_state import MarketState
    ms_src = inspect.getsource(MarketState)
    if 'self._lock' in ms_src or 'RLock' in ms_src:
        print('  [OK] MarketState usa lock')
    else:
        print('  [WARN] MarketState pode não ter lock')
        findings.append('market_state_sem_lock')
    
    # ========================================================================
    # 7. VERIFICAR FILAS
    # ========================================================================
    print('\n[7] VERIFICANDO FILAS...')
    
    # CaptureDaemon
    capture_file = _root / 'core' / 'capture_daemon.py'
    if capture_file.exists():
        content = capture_file.read_text(encoding='utf-8')
        if 'queue.Queue' in content or 'queue.empty' in content:
            print('  [OK] CaptureDaemon usa queue')
            # Verificar tamanho máximo
            if 'maxsize' in content:
                print('  [OK] Queue tem tamanho máximo')
            else:
                print('  [WARN] Queue sem tamanho máximo (pode saturar)')
                findings.append('queue_sem_limite')
        else:
            print('  [WARN] CaptureDaemon pode não usar queue')
    
    # ========================================================================
    # 8. VERIFICAR PERDA DE EVENTOS
    # ========================================================================
    print('\n[8] VERIFICANDO PERDA DE EVENTOS...')
    
    # Verificar deduplication
    rtd_file = _root / 'adapters' / 'profit_rtd.py'
    if rtd_file.exists():
        content = rtd_file.read_text(encoding='utf-8')
        if 'vistos_tt' in content or 'dedup' in content.lower():
            print('  [OK] Deduplication de trades implementada')
        else:
            print('  [WARN] Deduplication não encontrada')
            findings.append('possivel_duplicacao')
    
    # ========================================================================
    # 9. VERIFICAR MEMÓRIA
    # ========================================================================
    print('\n[9] VERIFICANDO MEMÓRIA...')
    
    # Verificar se há limites de memória
    scorer_src_full = inspect.getsource(ScorerML)
    if 'maxlen' in scorer_src_full or 'deque(maxlen' in scorer_src_full:
        print('  [OK] Uso de deque com maxlen identificado')
    else:
        print('  [WARN] Nenhum limite de memória encontrado')
        findings.append('possivel_vazamento_memoria')
    
    # ========================================================================
    # 10. VERIFICAR GARGALHOS
    # ========================================================================
    print('\n[10] VERIFICANDO GARGALHOS...')
    
    # Verificar I/O
    if 'flush' in scorer_src_full or 'sync' in scorer_src_full:
        print('  [INFO] Flush/sync identificado (pode ser gargalo de I/O)')
    
    # Verificar processamento em lote
    if 'processar_lote' in inspect.getsource(__import__('features.feature_engine', fromlist=['FeatureEngine']).FeatureEngine):
        print('  [OK] Processamento em lote implementado')
    else:
        print('  [WARN] Processamento em lote não encontrado')
    
    # ========================================================================
    # RESUMO
    # ========================================================================
    print('\n' + '='*70)
    print('RESUMO DA AUDITORIA LIVE')
    print('='*70)
    
    if findings:
        print(f'\n[ATTENTION] {len(findings)} problema(s) encontrado(s):')
        for f in findings:
            print(f'  - {f}')
        return 1
    else:
        print('\n[LIVE OK] Nenhuma irregularidade crítica encontrada.')
        return 0

if __name__ == '__main__':
    sys.exit(main())
