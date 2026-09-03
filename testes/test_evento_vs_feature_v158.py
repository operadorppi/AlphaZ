# -*- coding: utf-8 -*-
"""
testes/test_evento_vs_feature_v158.py — EVENTO != FEATURE (v15.8).

Princípio arquitetural:
  1 linha RTD -> 1 evento. Mesmo que 100 eventos tenham o mesmo ms, preço,
  quantidade e agressor, são 100 eventos legítimos.

  RAW preserva os 100. A FEATURE (agregação temporal do segundo) deve CONTAR
  os 100 — n=100, vol_total=100 — nunca deduplicá-los por conteúdo.

Cenários:
  1. 100 eventos idênticos no mesmo segundo -> feature n=100 (não n=1)
  2. Feature do segundo fechado contém a agregação completa
  3. REGRESSÃO de captura: o MarketState/alimentar não deduplica — buffer
     com 100 eventos (o dedup por conteúdo foi removido na camada de captura
     em v14.7 e na camada de features em v15.8)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.market_state import MarketState  # noqa: E402
from core.signal_engine import SignalEngine  # noqa: E402

S = 1_787_000_000


def _motor():
    ms = MarketState(config={'book_split': 30})
    se = SignalEngine(ms, config={})
    return ms, se


def test_100_eventos_identicos_mesmo_ms_viram_feature_n100():
    """100 eventos idênticos no mesmo ms -> 100 no buffer -> feature n=100."""
    ms, se = _motor()
    ts = S * 1000 + 500  # mesmo milissegundo

    # App real: alimentar + calcular POR TRADE (buffer do segundo S é consumido
    # a cada evento; a feature final de S só é persistida quando S fecha)
    for _ in range(100):
        ok = ms.alimentar_negocio(ativo='WINV26', ts_ms=ts, preco=170000.0,
                                  qtd=1, agressor='Comprador',
                                  compradora='XP', vendedora='BTG')
        assert ok
        se.calcular(S, skip_avaliar=True)

    # Fecha o segundo (1 trade no segundo seguinte)
    ms.alimentar_negocio(ativo='WINV26', ts_ms=(S + 1) * 1000, preco=170000.2,
                         qtd=1, agressor='Vendedor', compradora='BTG', vendedora='XP')
    se.calcular(S + 1, skip_avaliar=True)

    hist = ms.historico.get('WINV26', [])
    assert len(hist) == 1
    f = hist[0]
    assert f['n'] == 100, f"feature n={f['n']} — deveria ser 100 (agregação conta TODOS os eventos)"
    assert f['vol_total'] == 100, f"vol_total={f['vol_total']} — deveria ser 100"
    assert f['vol_compr'] == 100 and f['vol_vend'] == 0
    assert f['aggr_imb'] == 1.0, '100 compras → aggr_imb = +1.0'


def test_feature_direta_com_100_identicos():
    """processar_lote direto com 100 eventos idênticos -> n=100."""
    ms, _ = _motor()
    from features.feature_engine import FeatureEngine
    fe = FeatureEngine(ms)

    negs = [{'preco': 170000.0, 'qtd': 1, 'agressor': 'Comprador',
             'compradora': 'XP', 'vendedora': 'BTG', 'ts_ms': S * 1000 + 1}
            for _ in range(100)]
    f = fe.processar_lote('WINV26', negs, S)
    assert f is not None
    assert f['n'] == 100, f"processar_lote n={f['n']} — dedup por conteúdo voltou?"
    assert f['vol_total'] == 100


def test_eventos_identicos_entre_ativo_nao_se_misturam():
    """Agregação é por ativo: 100 idênticos no WIN não afetam o WDO."""
    ms, se = _motor()
    ts = S * 1000 + 500
    for _ in range(100):
        ms.alimentar_negocio(ativo='WINV26', ts_ms=ts, preco=170000.0, qtd=1,
                             agressor='Comprador', compradora='XP', vendedora='BTG')
        se.calcular(S, skip_avaliar=True)
    for _ in range(3):
        ms.alimentar_negocio(ativo='WDOV26', ts_ms=ts, preco=5000.0, qtd=1,
                             agressor='Vendedor', compradora='BTG', vendedora='XP')
        se.calcular(S, skip_avaliar=True)

    ms.alimentar_negocio(ativo='WINV26', ts_ms=(S + 1) * 1000, preco=170000.2,
                         qtd=1, agressor='Vendedor', compradora='BTG', vendedora='XP')
    se.calcular(S + 1, skip_avaliar=True)

    f_win = next(h for h in ms.historico['WINV26'] if h['ativo'] == 'WINV26')
    assert f_win['n'] == 100
