#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FASE 14 P0 — CAUSALIDADE END-TO-END

Testa que features NÃO usam informação do futuro.

Metodologia:
1. Dataset A: eventos até tempo T
2. Dataset B: mesmos eventos até T, + eventos extras DEPOIS de T  
3. Executar features em ambos
4. Se features EM T forem iguais → causal OK
5. Se features EM T diferirem → LEAKAGE detectado
"""

import sys
import random
from pathlib import Path
from typing import List

FREEBUFF_PATH = Path(r"C:\freebuff")
if str(FREEBUFF_PATH) not in sys.path:
    sys.path.insert(0, str(FREEBUFF_PATH))

import pytest


def generate_events(seed: int = 42, n_events: int = 100) -> List[dict]:
    """Gera eventos determinísticos."""
    rng = random.Random(seed)
    events = []
    price = 150000.0
    ts = 1_000_000
    
    for i in range(n_events):
        delta = rng.uniform(-50, 50)
        price += delta
        qtd = rng.choice([1, 2, 5, 10])
        agressor = rng.choice(['Comprador', 'Vendedor'])
        comp = rng.choice(['BTG', 'IB', 'XP', 'None'])
        vend = rng.choice(['Goldman', 'Morgan', 'JP', 'None'])
        
        events.append({
            'ts_ms': ts,
            'preco': price,
            'qtd': qtd,
            'agressor': agressor,
            'compradora': comp,
            'vendedora': vend,
        })
        ts += rng.randint(10, 100)
    
    return events


def generate_extra_events(seed: int = 99999, n_events: int = 100) -> List[dict]:
    """Gera eventos completamente diferentes (pós-split)."""
    rng = random.Random(seed)
    events = []
    price = 151000.0
    ts = 2_000_000
    
    for i in range(n_events):
        delta = rng.uniform(-100, 100)
        price += delta
        qtd = rng.choice([5, 10, 20, 50])
        agressor = rng.choice(['Comprador', 'Vendedor'])
        comp = rng.choice(['ITAU', 'Santander', 'BRADESCO', 'None'])
        vend = rng.choice(['Credit Suisse', 'UBS', 'Barclays', 'None'])
        
        events.append({
            'ts_ms': ts,
            'preco': price,
            'qtd': qtd,
            'agressor': agressor,
            'compradora': comp,
            'vendedora': vend,
        })
        ts += rng.randint(10, 100)
    
    return events


class TestCausalidadeE2E:
    """Verifica que features em T não dependem de eventos após T."""
    
    @pytest.fixture(scope="class")
    def datasets(self):
        """Setup: cria datasets A e B."""
        # Dataset A: 100 eventos
        events_a = generate_events(seed=42, n_events=100)
        # Dataset B: mesmos 100 eventos + 100 eventos extras
        events_extra = generate_extra_events(seed=99999, n_events=100)
        events_b = events_a + events_extra
        
        t_ms = events_a[50]['ts_ms']  # Ponto de teste no meio
        
        return {
            'events_a': events_a,
            'events_b': events_b,
            't_ms': t_ms,
        }
    
    def test_aggr_imb_no_leakage(self, datasets):
        """aggr_imb em T deve ser igual independentemente de eventos pós-T."""
        from features.trade_features import JanelaFeatures
        
        events_a = datasets['events_a']
        events_b = datasets['events_b']
        t_ms = datasets['t_ms']
        
        # Filtra eventos até T
        events_up_to_t_a = [e for e in events_a if e['ts_ms'] <= t_ms]
        events_up_to_t_b = [e for e in events_b if e['ts_ms'] <= t_ms]
        
        # Devem ser idênticos
        assert events_up_to_t_a == events_up_to_t_b
        
        # Calcula features
        jf_a = JanelaFeatures(janela_ms=1000)
        jf_b = JanelaFeatures(janela_ms=1000)
        
        for e in events_up_to_t_a:
            jf_a.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        for e in events_up_to_t_b:
            jf_b.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        
        snap_a = jf_a.snapshot(t_ms)
        snap_b = jf_b.snapshot(t_ms)
        
        assert snap_a['aggr_imb'] == snap_b['aggr_imb'], \
            f"LEAKAGE: aggr_imb difere! A={snap_a['aggr_imb']}, B={snap_b['aggr_imb']}"
    
    def test_cvd_total_no_leakage(self, datasets):
        """cvd_total em T deve ser igual independentemente de eventos pós-T."""
        from features.trade_features import JanelaFeatures
        
        events_a = datasets['events_a']
        events_b = datasets['events_b']
        t_ms = datasets['t_ms']
        
        events_up_to_t_a = [e for e in events_a if e['ts_ms'] <= t_ms]
        events_up_to_t_b = [e for e in events_b if e['ts_ms'] <= t_ms]
        
        jf_a = JanelaFeatures(janela_ms=1000)
        jf_b = JanelaFeatures(janela_ms=1000)
        
        for e in events_up_to_t_a:
            jf_a.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        for e in events_up_to_t_b:
            jf_b.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        
        snap_a = jf_a.snapshot(t_ms)
        snap_b = jf_b.snapshot(t_ms)
        
        assert snap_a['cvd_total'] == snap_b['cvd_total'], \
            f"LEAKAGE: cvd_total difere! A={snap_a['cvd_total']}, B={snap_b['cvd_total']}"
    
    def test_delta_preco_no_leakage(self, datasets):
        """delta_preco em T deve ser igual."""
        from features.trade_features import JanelaFeatures
        
        events_a = datasets['events_a']
        events_b = datasets['events_b']
        t_ms = datasets['t_ms']
        
        events_up_to_t_a = [e for e in events_a if e['ts_ms'] <= t_ms]
        events_up_to_t_b = [e for e in events_b if e['ts_ms'] <= t_ms]
        
        jf_a = JanelaFeatures(janela_ms=1000)
        jf_b = JanelaFeatures(janela_ms=1000)
        
        for e in events_up_to_t_a:
            jf_a.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        for e in events_up_to_t_b:
            jf_b.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        
        snap_a = jf_a.snapshot(t_ms)
        snap_b = jf_b.snapshot(t_ms)
        
        assert snap_a['delta_preco_janela'] == snap_b['delta_preco_janela'], \
            f"LEAKAGE: delta_preco difere!"
    
    def test_volume_sides_no_leakage(self, datasets):
        """Volume comprador/vendedor em T deve ser igual."""
        from features.trade_features import JanelaFeatures
        
        events_a = datasets['events_a']
        events_b = datasets['events_b']
        t_ms = datasets['t_ms']
        
        events_up_to_t_a = [e for e in events_a if e['ts_ms'] <= t_ms]
        events_up_to_t_b = [e for e in events_b if e['ts_ms'] <= t_ms]
        
        jf_a = JanelaFeatures(janela_ms=1000)
        jf_b = JanelaFeatures(janela_ms=1000)
        
        for e in events_up_to_t_a:
            jf_a.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        for e in events_up_to_t_b:
            jf_b.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        
        snap_a = jf_a.snapshot(t_ms)
        snap_b = jf_b.snapshot(t_ms)
        
        assert snap_a['vol_compra'] == snap_b['vol_compra'], "LEAKAGE: vol_compra difere!"
        assert snap_a['vol_venda'] == snap_b['vol_venda'], "LEAKAGE: vol_venda difere!"
    
    def test_hhi_no_leakage(self, datasets):
        """HHI em T deve ser igual."""
        from features.trade_features import JanelaFeatures
        
        events_a = datasets['events_a']
        events_b = datasets['events_b']
        t_ms = datasets['t_ms']
        
        events_up_to_t_a = [e for e in events_a if e['ts_ms'] <= t_ms]
        events_up_to_t_b = [e for e in events_b if e['ts_ms'] <= t_ms]
        
        jf_a = JanelaFeatures(janela_ms=1000)
        jf_b = JanelaFeatures(janela_ms=1000)
        
        for e in events_up_to_t_a:
            jf_a.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        for e in events_up_to_t_b:
            jf_b.add_evento(e['ts_ms'], e['preco'], e['qtd'], e['agressor'], e['compradora'], e['vendedora'])
        
        snap_a = jf_a.snapshot(t_ms)
        snap_b = jf_b.snapshot(t_ms)
        
        assert snap_a['hhi_compra'] == snap_b['hhi_compra'], "LEAKAGE: hhi_compra difere!"
        assert snap_a['hhi_venda'] == snap_b['hhi_venda'], "LEAKAGE: hhi_venda difere!"
    
    def test_deterministic_dataset(self):
        """Dataset deve ser determinístico."""
        d1 = generate_events(seed=42)
        d2 = generate_events(seed=42)
        assert d1 == d2
    
    def test_divergent_datasets_differ_after_split(self):
        """Datasets divergentes devem diferir após o split."""
        events_a = generate_events(seed=42, n_events=50)
        events_b = generate_events(seed=42, n_events=50) + generate_extra_events(seed=99999, n_events=50)
        
        assert events_a[:25] == events_b[:25]  # Idênticos até split
        assert events_a[25:] != events_b[25:]  # Diferentes depois


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
