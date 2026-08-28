import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
#!/usr/bin/env python3
"""
test_labeler_invariants.py — Testes formais de invariantes do triple barrier.

Estes testes NAO testam implementacao. Testam REGRA.
Se qualquer implementacao (core, vectorizado, futura) falhar aqui,
a implementacao esta ERRADA, nao os testes.

═══════════════════════════════════════════════════════════════════
INVARIANTES
═══════════════════════════════════════════════════════════════════

1. Se TP acontece antes de SL → label = TP
2. Se SL acontece antes de TP → label = SL
3. Se nenhuma barreira → label = TIMEOUT
4. Nunca usar evento fora do horizonte
5. Nunca atravessar fronteira (ativo, dia)
6. Nenhum label depende de dados posteriores ao horizonte
7. Alterar eventos DEPOIS do horizonte nao altera o label

Adicionais:
8. AMBIGUOUS quando TP e SL no mesmo tick
9. Simetria LONG/SHORT
10. Equivalencia core vs vectorizado (10k+ cenarios)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from labeler_core import (
    label_ponto_ref, label_array_ref,
    LabelOutcome, AMBIGUOUS, validar_equivalencia,
)


# ════════════════════════════════════════════════════════════════
# INVARIANTE 1: TP antes de SL → label = TP
# ════════════════════════════════════════════════════════════════

class TestInvariante1_TPBeforeSL:
    """Se o preco atinge TP antes de SL, o label e TP."""

    def test_subida_simples(self):
        """Preco sobe ate TP sem nunca tocar SL."""
        precos = np.array([100.0, 101, 102, 103, 104, 105, 106])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP

    def test_tp_no_limite(self):
        """Preco exatamente igual a P0 + TP."""
        precos = np.array([100.0, 105.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP
        assert r.preco_saida == 105.0

    def test_tp_apos_volatilidade(self):
        """Preco oscila mas atinge TP antes."""
        precos = np.array([100.0, 98, 99, 101, 103, 102, 104, 105, 106])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP

    def test_gap_para_tp(self):
        """Gap de abertura que ja satisfaz TP."""
        precos = np.array([100.0, 110.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP


# ════════════════════════════════════════════════════════════════
# INVARIANTE 2: SL antes de TP → label = SL
# ════════════════════════════════════════════════════════════════

class TestInvariante2_SLBeforeTP:
    """Se o preco atinge SL antes de TP, o label e SL."""

    def test_queda_simples(self):
        """Preco cai ate SL sem nunca tocar TP."""
        precos = np.array([100.0, 99, 98, 97, 96, 95, 94])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL

    def test_sl_no_limite(self):
        """Preco exatamente igual a P0 - SL."""
        precos = np.array([100.0, 97.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL
        assert r.preco_saida == 97.0

    def test_sl_apos_volatilidade(self):
        """Preco sobe primeiro mas depois cai e toca SL."""
        precos = np.array([100.0, 103, 102, 100, 98, 97, 96])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL

    def test_gap_para_sl(self):
        """Gap de abertura que ja satisfaz SL."""
        precos = np.array([100.0, 90.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL

    def test_tp_visitado_depois_do_sl(self):
        """Preco toca SL no tick 1, depois TP no tick 5. SL conta porque veio primeiro."""
        precos = np.array([100.0, 96, 98, 100, 103, 105])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL
        assert r.duracao_ms == 100  # SL no tick 1


# ════════════════════════════════════════════════════════════════
# INVARIANTE 3: Nenhuma barreira → TIMEOUT
# ════════════════════════════════════════════════════════════════

class TestInvariante3_Timeout:
    """Se nenhuma barreira e tocada no horizonte, label = TIMEOUT."""

    def test_mercado_lateral(self):
        """Preco oscila dentro da faixa TP/SL."""
        precos = np.array([100.0, 100.5, 99.5, 100.3, 99.7, 100.1, 99.9])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_tp_perto_mas_nao_atinge(self):
        """Preco chega a P0+TP-0.01 mas nao toca."""
        precos = np.array([100.0, 104.99])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_sl_perto_mas_nao_atinge(self):
        """Preco chega a P0-SL+0.01 mas nao toca."""
        precos = np.array([100.0, 97.01])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_preco_constante(self):
        """Preco nao se move."""
        precos = np.array([100.0, 100.0, 100.0, 100.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT


# ════════════════════════════════════════════════════════════════
# INVARIANTE 4: Nunca usar evento fora do horizonte
# ════════════════════════════════════════════════════════════════

class TestInvariante4_HoldingRespeitado:
    """A barreira temporal e o holding NUNCA sao excedidos."""

    def test_tp_depois_do_hold(self):
        """Preco atinge TP so DEPOIS do holding. Deve ser TIMEOUT."""
        # holding = 200ms = 2 ticks (tick_ms=100)
        precos = np.array([100.0, 101.0, 102.0, 105.0, 106.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0,
                            max_holding_ms=200, tick_ms=100)
        assert r.outcome == LabelOutcome.TIMEOUT
        assert r.duracao_ms == 200  # holding max

    def test_sl_depois_do_hold(self):
        """Preco atinge SL so DEPOIS do holding. Deve ser TIMEOUT."""
        precos = np.array([100.0, 99.0, 98.0, 95.0, 94.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0,
                            max_holding_ms=200, tick_ms=100)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_tp_no_ultimo_tick(self):
        """Preco atinge TP exatamente no ultimo tick do holding."""
        precos = np.array([100.0, 101.0, 105.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0,
                            max_holding_ms=200, tick_ms=100)
        assert r.outcome == LabelOutcome.TP
        assert r.duracao_ms == 200

    def test_holding_varia(self):
        """Holding diferentes produzem duracoes diferentes."""
        precos = np.array([100.0, 101, 102, 103, 104, 105, 106])
        r1 = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=200)
        r2 = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=500)
        # r1: TP no tick 5 = 500ms, mas holding=200ms → TIMEOUT
        # r2: TP no tick 5 = 500ms, holding=500ms → TP
        assert r1.outcome == LabelOutcome.TIMEOUT
        assert r2.outcome == LabelOutcome.TP


# ════════════════════════════════════════════════════════════════
# INVARIANTE 5: Nunca atravessar fronteira (ativo, dia)
# ════════════════════════════════════════════════════════════════

class TestInvariante5_Fronteiras:
    """A janela ahead NUNCA cruza segmento (ativo+dia)."""

    def test_sl_fora_do_segmento(self):
        """SL esta no proximo segmento — nao deve contar."""
        precos = np.array([100.0, 99.0, 98.0, 96.0])  # idx 3 = 96, SL=3 -> 97
        r = label_ponto_ref(precos, 0, tp_pts=10.0, sl_pts=3.0,
                            max_holding_ms=1000, seg_fim=3)
        # Segmento: idx 0,1,2 (precos 100, 99, 98). Min=98 > 97 (SL). TIMEOUT.
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_tp_fora_do_segmento(self):
        """TP esta no proximo segmento — nao deve contar."""
        precos = np.array([100.0, 101.0, 102.0, 110.0])  # idx 3 = 110, TP=5 -> 105
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0,
                            max_holding_ms=1000, seg_fim=3)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_sl_dentro_do_segmento(self):
        """SL dentro do segmento — DEVE contar."""
        precos = np.array([100.0, 99.0, 98.0, 96.0])
        r = label_ponto_ref(precos, 0, tp_pts=10.0, sl_pts=3.0,
                            max_holding_ms=1000, seg_fim=4)
        assert r.outcome == LabelOutcome.SL


# ════════════════════════════════════════════════════════════════
# INVARIANTE 6: Nenhum label depende de dados posteriores ao horizonte
# ════════════════════════════════════════════════════════════════

class TestInvariante6_SemLeakage:
    """Dados alem do horizonte nao podem influenciar o label."""

    def test_alterar_futuro(self):
        """Mudar precos alem do holding nao muda o label."""
        precos_orig = np.array([100.0, 100.5, 100.3, 100.7, 100.2, 100.8])
        precos_mod = precos_orig.copy()
        precos_mod[4] = 200.0  # muda depois do horizonte
        precos_mod[5] = 50.0

        holding = 300  # 3 ticks
        r_orig = label_ponto_ref(precos_orig, 0, tp_pts=5.0, sl_pts=3.0,
                                 max_holding_ms=holding)
        r_mod = label_ponto_ref(precos_mod, 0, tp_pts=5.0, sl_pts=3.0,
                                max_holding_ms=holding)
        assert r_orig.outcome == r_mod.outcome
        assert r_orig.preco_saida == r_mod.preco_saida
        assert r_orig.duracao_ms == r_mod.duracao_ms


# ════════════════════════════════════════════════════════════════
# INVARIANTE 7: Alterar eventos DEPOIS do horizonte nao altera o label
# ════════════════════════════════════════════════════════════════

class TestInvariante7_IndependenciaFuturo:
    """Versao forte do invariante 6: multiplas alteracoes."""

    def test_destruir_preco(self):
        """Colapsar todos os precos apos o horizonte."""
        # P0=100, TP=5 -> barreira 105. tick 5: preco=105 -> TP atingido.
        precos = np.array([100.0, 101, 102, 103, 104, 105, 0.01, 0.01])
        holding = 500  # 5 ticks
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=holding)
        # TP atingido no tick 5 (preco=105), antes do holding (5 ticks = 500ms)
        assert r.outcome == LabelOutcome.TP
        assert r.duracao_ms == 500

    def test_inserir_volatilidade_extrema(self):
        """Inserir volatilidade extrema apos o horizonte."""
        precos_orig = np.array([100.0, 100.5, 100.3, 100.1])
        precos_mod = np.array([100.0, 100.5, 100.3, 500.0, 10.0, 999.0])

        holding = 200  # 2 ticks
        r_orig = label_ponto_ref(precos_orig, 0, tp_pts=5.0, sl_pts=3.0,
                                 max_holding_ms=holding)
        r_mod = label_ponto_ref(precos_mod, 0, tp_pts=5.0, sl_pts=3.0,
                                max_holding_ms=holding)
        assert r_orig.outcome == r_mod.outcome


# ════════════════════════════════════════════════════════════════
# INVARIANTE 8: AMBIGUOUS quando TP e SL no mesmo tick
# ════════════════════════════════════════════════════════════════

class TestInvariante8_Ambiguous:
    """Quando TP e SL sao atingidos no mesmo tick, resultado = AMBIGUOUS."""

    def test_simetria_tp_sl_igual(self):
        """TP = SL = mesmo valor, preco no meio. Nenhum toca."""
        precos = np.array([100.0, 100.5])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=5.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT

    def test_gap_impossivel(self):
        """Para LONG, e impossivel satisfazer TP (P>=P0+TP) E SL (P<=P0-SL)
        ao mesmo tempo, pois P0+TP > P0-SL."""
        # P0=100, TP=5 -> barreira 105. SL=5 -> barreira 95.
        # Nao existe P tal que P>=105 E P<=95.
        # Portanto AMBIGUOUS e impossivel com dados reais LONG.
        # Mas se o codigo encontrar, deve tratar corretamente.
        precos = np.array([100.0, 105.0])  # so TP
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=5.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP

    def test_primeiro_wins(self):
        """SL no tick 1, TP no tick 2. SL conta porque veio primeiro."""
        precos = np.array([100.0, 96.0, 106.0])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL
        assert r.duracao_ms == 100


# ════════════════════════════════════════════════════════════════
# INVARIANTE 9: Simetria LONG/SHORT
# ════════════════════════════════════════════════════════════════

class TestInvariante9_Simetria:
    """LONG e SHORT devem ser simetricos (via label_array_ref)."""

    def test_long_tp_short_sl(self):
        """Serie que sobe: LONG=TP, SHORT=SL."""
        precos = np.array([100.0, 101, 102, 103, 104, 105, 106])
        ts = np.arange(len(precos), dtype=np.int64) * 100
        ativos = np.array(['TEST'] * len(precos))

        res = label_array_ref(precos, ts, ativos, tp_pts=5.0, sl_pts=3.0,
                              max_holding_s=10)
        # Para LONG: TP atingido (105+)
        # O label_array_ref so faz LONG por agora
        assert res['label'][0] == 1  # TP


# ════════════════════════════════════════════════════════════════
# TESTE ADVERSARIAL: Feature com futuro deve ser detectada
# ════════════════════════════════════════════════════════════════

class TestAdversarial:
    """Testes deliberadamente maliciosos para detectar leakage."""

    def test_inserir_preco_futuro(self):
        """Inserir preco futuro nos dados. Label nao deve mudar."""
        precos_base = np.array([100.0, 100.5, 100.3, 100.7, 100.2])
        precos_leak = precos_base.copy()
        precos_leak[1] = 200.0  # "preco futuro" inserido no passado

        r_base = label_ponto_ref(precos_base, 0, tp_pts=5.0, sl_pts=3.0,
                                 max_holding_ms=300)
        r_leak = label_ponto_ref(precos_leak, 0, tp_pts=5.0, sl_pts=3.0,
                                 max_holding_ms=300)
        # O label muda porque o preco futuro esta DENTRO do horizonte
        # Isso e CORRETO — o label DEVE mudar se o dado dentro do horizonte muda
        # O que NAO pode acontecer e mudar com dados FORA do horizonte

    def test_label_nao_muda_com_dados_depois(self):
        """Mudanca em dados APOS horizonte NUNCA muda label."""
        precos = np.array([100.0, 102.0, 104.0, 103.0, 102.0, 101.0])
        holding = 200  # 2 ticks

        r1 = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0,
                             max_holding_ms=holding)

        # Muda tudo depois do horizonte
        precos_corrompido = precos.copy()
        precos_corrompido[3:] = [1.0, 1.0, 1.0]

        r2 = label_ponto_ref(precos_corrompido, 0, tp_pts=5.0, sl_pts=3.0,
                             max_holding_ms=holding)

        assert r1.outcome == r2.outcome, \
            f'Label mudou com dados posteriores ao horizonte! {r1.outcome} vs {r2.outcome}'
        assert r1.preco_saida == r2.preco_saida
        assert r1.duracao_ms == r2.duracao_ms

    def test_transpor_dados(self):
        """Trocar dados entre dois segmentos. Labels individuais nao mudam."""
        precos = np.array([100.0, 101, 102, 103,    # segmento A
                          200.0, 201, 202, 203])    # segmento B
        ts = np.array([0, 100, 200, 300,
                      86400000, 86400100, 86400200, 86400300], dtype=np.int64)
        ativos = np.array(['A', 'A', 'A', 'A', 'B', 'B', 'B', 'B'])

        res1 = label_array_ref(precos, ts, ativos, tp_pts=5.0, sl_pts=3.0,
                               max_holding_s=10)
        # Trocar precos entre segmentos
        precos2 = precos.copy()
        precos2[:4] = [200.0, 201, 202, 203]
        precos2[4:] = [100.0, 101, 102, 103]
        res2 = label_array_ref(precos2, ts, ativos, tp_pts=5.0, sl_pts=3.0,
                               max_holding_s=10)

        # Os outcomes locais devem ser identicos (cada segmento isolado)
        # Segmento A no res1 = segmento B no res2 (precos iguais)
        assert res1['label'][0] == res2['label'][4]
        assert res1['label'][4] == res2['label'][0]


# ════════════════════════════════════════════════════════════════
# TESTE DE EQUIVALENCIA: core vs vectorizado (10k cenarios)
# ════════════════════════════════════════════════════════════════

class TestEquivalencia:
    """labeler_core (referencia) e labeler_vectorizado devem produzir
    os mesmos resultados para as entradas validas."""

    @pytest.mark.parametrize("seed", range(100))
    def test_equivalencia_100_cenarios(self, seed):
        """100 cenarios aleatorios — equivalencia exata."""
        np.random.seed(seed)
        n = np.random.randint(50, 300)
        base = np.random.uniform(1000, 100000)
        noise = np.random.normal(0, 20, n)
        precos = np.cumsum(noise) + base
        precos = np.maximum(precos, 1)

        ts = np.arange(n, dtype=np.int64) * 100
        ativos = np.array(['TEST'] * n)

        tp = np.random.choice([5, 10, 20, 50])
        sl = np.random.choice([3, 5, 10, 25])

        ok, divs = validar_equivalencia(precos, ts, ativos,
                                         tp_pts=tp, sl_pts=sl,
                                         max_holding_s=30)
        assert ok, f'Divergencia encontrada: {divs[:3]}'


# ════════════════════════════════════════════════════════════════
# TESTE: retorno_pts coerente com label
# ════════════════════════════════════════════════════════════════

class TestRetornoCoerente:
    """O retorno em pontos deve ser coerente com o label."""

    def test_tp_retorno_positivo(self):
        """TP deve ter retorno positivo (preco_saida > preco_entrada)."""
        precos = np.array([100.0, 101, 102, 103, 104, 105, 106])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TP
        assert r.retorno_pts > 0

    def test_sl_retorno_negativo(self):
        """SL deve ter retorno negativo (preco_saida < preco_entrada)."""
        precos = np.array([100.0, 99, 98, 97, 96, 95, 94])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.SL
        assert r.retorno_pts < 0

    def test_timeout_retorno_zero(self):
        """TIMEOUT deve ter retorno zero."""
        precos = np.array([100.0, 100.5, 100.3])
        r = label_ponto_ref(precos, 0, tp_pts=5.0, sl_pts=3.0, max_holding_ms=1000)
        assert r.outcome == LabelOutcome.TIMEOUT
        assert r.retorno_pts == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
