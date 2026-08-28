import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
import unittest
"""
test_features.py — Testes unitários para features_lib.py
Rode: python -m pytest test_features.py -v
"""
import sys
import os
import time
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scripts_dir = os.path.join(base_dir, "scripts")
if os.path.isdir(scripts_dir):
    sys.path.insert(0, scripts_dir)
sys.path.insert(0, base_dir)

from features_lib import (
    ewma_update, hhi, entropia, idade_ms,
    JanelaFeatures, BookLevelFeatures, GeradorJanelas,
    asof_join_linhas, VPINTracker,
    OFITracker, KyleLambdaTracker, EWMAZScore,
    fase_sessao, dias_ate_vencimento, _tod_de_ts,
)
from datetime import date


# ============================================================
#  Funções puras
# ============================================================

class TestEwma:
    def test_primeiro_valor(self):
        assert ewma_update(0, 100, 0.5) == 50.0

    def test_converge(self):
        v = 0
        for _ in range(100):
            v = ewma_update(v, 100, 0.1)
        assert abs(v - 100) < 1

    def test_alpha_zero(self):
        assert ewma_update(10, 20, 0.0) == 10.0

    def test_alpha_um(self):
        assert ewma_update(10, 20, 1.0) == 20.0


class TestHHI:
    def test_dominio_total(self):
        assert hhi([100, 0, 0]) == 1.0

    def test_pulverizado(self):
        assert abs(hhi([10, 10, 10]) - 1/3) < 0.01

    def test_dois_iguais(self):
        assert abs(hhi([50, 50]) - 0.5) < 0.01

    def test_vazio(self):
        assert hhi([]) == 0.0

    def test_um_elemento(self):
        assert hhi([100]) == 1.0


class TestEntropia:
    def test_vazio(self):
        assert entropia([]) == 0.0

    def test_dominio(self):
        # Um agente = entropia zero
        assert entropia([100]) == 0.0

    def test_igual(self):
        e = entropia([50, 50])
        assert e > 0

    def test_mais_agentes_mais_entropia(self):
        assert entropia([25, 25, 25, 25]) > entropia([50, 50])


class TestIdadeMs:
    def test_normal(self):
        assert idade_ms(1000, 900) == 100

    def test_fonte_none(self):
        assert idade_ms(1000, None) is None

    def test_fonte_futura(self):
        assert idade_ms(1000, 1100) == 0


# ============================================================
#  VPIN
# ============================================================

class TestVPIN:
    def test_buckets(self):
        vpin = VPINTracker(bucket_vol=100, n_buckets=5)
        for _ in range(10):
            vpin.add_evento(10, 'Comprador')
            vpin.add_evento(10, 'Vendedor')
        # Compra == venda em cada bucket = imb baixo
        assert vpin.valor() < 0.1

    def test_compra_pura(self):
        vpin = VPINTracker(bucket_vol=100, n_buckets=5)
        for _ in range(50):
            vpin.add_evento(10, 'Comprador')
        # Só compra = imb alto
        assert vpin.valor() > 0.9


# ============================================================
#  JanelaFeatures
# ============================================================

class TestJanelaFeatures:
    def _make_window(self):
        jf = JanelaFeatures(janela_ms=1000)
        now = 1000000
        # 60% compra, 40% venda
        jf.add_evento(now + 100, 100.0, 10, 'Comprador', 'A', 'B')
        jf.add_evento(now + 200, 100.0, 5, 'Vendedor', 'B', 'A')
        jf.add_evento(now + 300, 101.0, 10, 'Comprador', 'A', 'B')
        jf.add_evento(now + 400, 101.0, 5, 'Vendedor', 'B', 'A')
        return jf, now

    def test_snapshot_basico(self):
        jf, now = self._make_window()
        snap = jf.snapshot(now + 500)
        assert snap['vol_compra'] == 20
        assert snap['vol_venda'] == 10
        assert snap['vol_total'] == 30
        assert snap['aggr_imb'] > 0  # mais compra

    def test_ewma_existe(self):
        jf, now = self._make_window()
        snap = jf.snapshot(now + 500)
        assert 'ewma_imb_curta' in snap
        assert 'ewma_imb_media' in snap
        assert 'ewma_imb_longa' in snap

    def test_expiracao(self):
        jf = JanelaFeatures(janela_ms=500)
        jf.add_evento(1000, 100.0, 10, 'Comprador', None, None)
        snap1 = jf.snapshot(1200)
        assert snap1['vol_compra'] == 10
        # add_evento com ts 1600 (>500ms depois) expira o anterior
        jf.add_evento(1600, 100.0, 5, 'Vendedor', None, None)
        snap2 = jf.snapshot(1600)
        assert snap2['vol_compra'] == 0  # comprador expirou
        assert snap2['vol_venda'] == 5   # vendedor novo

    def test_corretora_tracking(self):
        jf = JanelaFeatures(janela_ms=10000)
        jf.add_evento(1000, 100.0, 10, 'Comprador', 'Goldman', 'XP')
        snap = jf.snapshot(2000)
        # Um único comprador (Goldman) = HHI de compra = 1.0
        assert snap['hhi_compra'] == 1.0
        assert snap['vol_compra'] == 10
        # Um único vendedor (XP) = HHI de venda = 1.0
        assert snap['hhi_venda'] == 1.0
        assert snap['vol_venda'] == 0  # XP vendeu? Na verdade o agressor é comprador, então XP é vendedora passiva
        # O trade tem volume total 10, compra 10, venda 0 (agressor comprador, vendedora passiva não conta como venda)
        assert snap['vol_total'] == 10

    def test_preco(self):
        jf = JanelaFeatures(janela_ms=10000)
        jf.add_evento(1000, 100.0, 10, 'Comprador', None, None)
        jf.add_evento(1001, 105.0, 5, 'Vendedor', None, None)
        snap = jf.snapshot(1002)
        assert snap['preco_ultimo'] == 105.0
        assert snap['delta_preco_janela'] == 5.0


# ============================================================
#  BookLevelFeatures
# ============================================================

class TestBookLevel:
    def _make_book(self):
        blf = BookLevelFeatures()
        snap = {
            'bid_vol': [100, 80, 60, 40, 20],
            'bid_preco': [100.0, 99.5, 99.0, 98.5, 98.0],
            'ask_vol': [90, 70, 50, 30, 10],
            'ask_preco': [100.5, 101.0, 101.5, 102.0, 102.5],
        }
        return blf, snap

    def test_spread(self):
        blf, snap = self._make_book()
        result = blf.calcular(snap, 'WIN', 1000)
        assert result is not None
        assert result['spread'] == 0.5  # 100.5 - 100.0

    def test_mid(self):
        blf, snap = self._make_book()
        result = blf.calcular(snap, 'WIN', 1000)
        mid = (result['spread'] / 2) + snap['bid_preco'][0]
        assert abs(result['mid'] - mid) < 0.1  # mid = (best_bid + best_ask) / 2

    def test_microprice(self):
        blf, snap = self._make_book()
        result = blf.calcular(snap, 'WIN', 1000)
        # microprice perto do mid mas inclinado pro lado de maior vol
        assert 100.0 < result['microprice'] < 101.0

    def test_imbalance_depths(self):
        blf, snap = self._make_book()
        result = blf.calcular(snap, 'WIN', 1000)
        imb = result['imbalance']
        assert 'L1' in imb
        assert 'L5' in imb
        # bid > ask em L1 (100 vs 90) = positive
        assert imb['L1'] > 0

    def test_book_vazio(self):
        blf = BookLevelFeatures()
        result = blf.calcular({'bid_vol': [], 'ask_vol': [], 'bid_preco': [], 'ask_preco': []}, 'WIN', 1000)
        assert result is None

    def test_velocidade(self):
        blf = BookLevelFeatures()
        snap1 = {
            'bid_vol': [100], 'bid_preco': [100.0],
            'ask_vol': [100], 'ask_preco': [101.0],
        }
        snap2 = {
            'bid_vol': [150], 'bid_preco': [100.0],
            'ask_vol': [80], 'ask_preco': [101.0],
        }
        blf.calcular(snap1, 'WIN', 1000)
        result = blf.calcular(snap2, 'WIN', 2000)  # 1s depois
        assert result is not None
        assert result['vel_bid'] != 0  # book mudou


# ============================================================
#  GeradorJanelas
# ============================================================

class TestGeradorJanelas:
    def test_emite_snapshots(self):
        gerador = GeradorJanelas(['WIN'], janela_ms=100, passo_ms=100)
        # 3 eventos em tempos diferentes
        saidas1 = gerador.processar_evento('WIN', 1050, 100.0, 10, 'Comprador', None, None)
        saidas2 = gerador.processar_evento('WIN', 1150, 101.0, 5, 'Vendedor', None, None)
        # No segundo evento, deve emitir snapshot do passo 1100
        assert len(saidas2) >= 1
        # O snapshot deve ter dados do WIN
        snap = saidas2[0][1]
        assert snap['vol_total'] > 0


# ============================================================
#  ASOF Join
# ============================================================

class TestAsofJoin:
    def test_join_basico(self):
        principal = [
            {'ts_ms': 1000, 'preco': 100, 'lado': 'WIN'},
            {'ts_ms': 1100, 'preco': 101, 'lado': 'WIN'},
        ]
        contexto = [
            {'ts_ms': 950, 'preco': 5000, 'lado': 'WDO'},
            {'ts_ms': 1050, 'preco': 5001, 'lado': 'WDO'},
        ]
        result = asof_join_linhas(principal, contexto, tolerancia_ms=200)
        assert len(result) == 2
        # Primeiro WIN (1000) deve casar com WDO 950 (idade 50ms)
        assert result[0]['ctx_idade_ms'] == 50
        # Contexto adiciona campos com sufixo _ctx
        ctx_keys = [k for k in result[0].keys() if k.endswith('_ctx')]
        assert len(ctx_keys) > 0
        assert result[1]['ctx_idade_ms'] == 50

    def test_fora_tolerancia(self):
        principal = [{'ts_ms': 2000, 'preco': 100}]
        contexto = [{'ts_ms': 1000, 'preco': 5000}]
        result = asof_join_linhas(principal, contexto, tolerancia_ms=100)
        assert result[0]['ctx_idade_ms'] is None

    def test_contexto_vazio(self):
        principal = [{'ts_ms': 1000, 'preco': 100}]
        result = asof_join_linhas(principal, [], tolerancia_ms=100)
        assert result[0]['ctx_idade_ms'] is None


class TestScorer:
    def test_scorer_import(self):
        from scorer import ScorerML
        assert ScorerML is not None

    def test_scorer_flatten(self):
        from scorer import ScorerML
        snap = {'preco': 100, 'book': {'bid_vol': [10, 20], 'ask_vol': [5, 15]}}
        flat = ScorerML._flatten(snap)
        assert flat['preco'] == 100
        assert flat['book_bid_vol'] == [10, 20]
        assert flat['book_ask_vol'] == [5, 15]


class TestTreinoLib:
    def test_flatten_snapshot(self):
        from treino_lib import flatten_snapshot
        snap = {
            'imbalance': {'L1': 0.5, 'L5': 0.3},
            'bid_vol': [10, 20],
            'preco': 100.0,
            'flag': True,
            'texto': 'abc',
        }
        flat = flatten_snapshot(snap)
        assert flat['imbalance_L1'] == 0.5
        assert flat['imbalance_L5'] == 0.3
        assert flat['bid_vol_0'] == 10
        assert flat['bid_vol_1'] == 20
        assert flat['preco'] == 100.0
        assert flat['flag'] is True
        assert flat['texto'] == 'abc'

    def test_flatten_vazio(self):
        from treino_lib import flatten_snapshot
        assert flatten_snapshot({}) == {}

    def test_split_com_purge(self):
        from treino_lib import split_com_purge
        import pandas as pd
        import time
        base = int(time.time() * 1000)
        ts = [base + i * 100 for i in range(1000)]  # 100s total
        df = pd.DataFrame({'ts_ms': ts, 'label': [1]*500 + [-1]*500, 'f1': range(1000)})
        train, test = split_com_purge(df, train_pct=0.8, purge_s=5, embargo_s=30)
        # Treino deve ser menor que 80% (purge removeu)
        assert len(train) < 800
        # Sem overlap temporal
        assert train['ts_ms'].max() < test['ts_ms'].min()
        # Gap deve ser >= purge_s
        gap_s = (test['ts_ms'].min() - train['ts_ms'].max()) / 1000
        assert gap_s >= 4.0  # pelo menos ~5s de gap

    def test_split_sem_tempo(self):
        from treino_lib import split_com_purge
        import pandas as pd
        df = pd.DataFrame({'label': [1]*100, 'f1': range(100)})
        train, test = split_com_purge(df, train_pct=0.8)
        assert len(train) == 80
        assert len(test) == 20

    def test_preparar_features(self):
        from treino_lib import preparar_features
        import pandas as pd
        df = pd.DataFrame({
            'label': [1, -1, 0],
            'ts_ms': [100, 200, 300],
            'feature_a': [1.0, 2.0, 3.0],
            'feature_b': [10, 20, 30],
            'texto': ['a', 'b', 'c'],
        })
        cols = preparar_features(df)
        assert 'label' not in cols
        assert 'ts_ms' not in cols
        assert 'texto' not in cols
        assert 'feature_a' in cols
        assert 'feature_b' in cols

    def test_avaliar_modelo(self):
        from treino_lib import avaliar_modelo
        from sklearn.ensemble import RandomForestClassifier
        import pandas as pd
        import numpy as np
        np.random.seed(42)
        X = pd.DataFrame({'f1': np.random.randn(200), 'f2': np.random.randn(200)})
        y = pd.Series((X['f1'] > 0).astype(int))
        m = RandomForestClassifier(n_estimators=10, random_state=42)
        m.fit(X.iloc[:150], y.iloc[:150])
        result = avaliar_modelo(m, X.iloc[150:], y.iloc[150:])
        assert 'acuracia' in result
        assert 'profit_factor' in result
        assert 0 <= result['acuracia'] <= 1


# ============================================================
#  OFI alinhado por preço (v9.7)
# ============================================================

class TestOFI:
    def _ofi(self, b1, a1, b2, a2):
        ofi = OFITracker(niveis=5)
        ofi.atualizar(b1, a1)   # inicializa
        ofi.atualizar(b2, a2)
        return ofi.ofi_total

    def test_melhora_best_bid_sem_mudanca_de_volume(self):
        # Volume 100 migrou de 100.0 → 100.5: OFI líquido deve ser ~0
        # (adição +100 no novo nível, remoção -100 no nível deslocado).
        # A versão antiga (por profundidade) dava +200 aqui — overcounting.
        ofi = self._ofi(
            [(100.0, 100), (99.5, 80)], [(100.5, 90), (101.0, 70)],
            [(100.5, 100), (99.5, 80)], [(100.5, 90), (101.0, 70)],
        )
        assert abs(ofi) < 0.5, f"OFI deveria ser ~0, veio {ofi}"

    def test_adiacao_limpa(self):
        # Volume novo de 50 no melhor bid = +50
        ofi = self._ofi(
            [(100.0, 100)], [(100.5, 90)],
            [(100.0, 150)], [(100.5, 90)],
        )
        assert abs(ofi - 50) < 0.5, f"OFI deveria ser +50, veio {ofi}"

    def test_remocao_limpa(self):
        # Retirada de 60 do melhor bid = -60
        ofi = self._ofi(
            [(100.0, 100)], [(100.5, 90)],
            [(100.0, 40)], [(100.5, 90)],
        )
        assert abs(ofi + 60) < 0.5, f"OFI deveria ser -60, veio {ofi}"

    def test_primeira_chamada_inicializa(self):
        ofi = OFITracker(niveis=5)
        ofi.atualizar([(100.0, 100)], [(100.5, 90)])
        assert ofi.ofi_total == 0.0  # sem referência anterior

    def test_niveis_vazios_ignorados(self):
        # Tuplas (0, 0) não devem quebrar nem contar
        ofi = self._ofi(
            [(100.0, 100), (0, 0), (0, 0)], [(100.5, 90), (0, 0), (0, 0)],
            [(100.0, 120), (0, 0), (0, 0)], [(100.5, 90), (0, 0), (0, 0)],
        )
        assert abs(ofi - 20) < 0.5, f"OFI deveria ser +20, veio {ofi}"


# ============================================================
#  Kyle's Lambda completo (v9.7)
# ============================================================

class TestKyleLambda:
    def test_abaixo_minimo(self):
        k = KyleLambdaTracker(janela=50)
        for i in range(10):
            k.atualizar(100.0 + i, 10, 'Comprador')
        r = k.calcular()
        # 10 ticks → 9 observações (o primeiro só inicializa o preço)
        assert r['kyle_lambda'] == 0.0
        assert r['kyle_n'] == 9

    def test_inclui_trades_sem_movimento(self):
        # Trades no mesmo preço (ΔP=0) DEVEM contar (objeto teórico)
        k = KyleLambdaTracker(janela=100)
        for i in range(30):
            k.atualizar(100.0, 10, 'Comprador')   # ΔP = 0
        r = k.calcular()
        assert r['kyle_n'] == 29, f"kyle_n deveria ser 29, veio {r['kyle_n']}"

    def test_compra_forte_preco_subindo(self):
        # Compra agressiva (volume variando) + preço subindo → lambda positivo.
        # dp é gerado proporcional ao volume assinado → covariância garantida > 0
        k = KyleLambdaTracker(janela=100)
        preco = 100.0
        for i in range(40):
            sv = 5 + (i % 4) * 3
            preco += sv * 0.01
            k.atualizar(preco, sv, 'Comprador')
        r = k.calcular()
        assert r['kyle_lambda'] > 0, f"lambda deveria ser > 0, veio {r['kyle_lambda']}"

    def test_venda_forte_preco_caindo(self):
        # Venda agressiva (volume variando) + preço caindo → lambda positivo
        k = KyleLambdaTracker(janela=100)
        preco = 100.0
        for i in range(40):
            sv = -(5 + (i % 4) * 3)
            preco += sv * 0.01   # sv negativo → preço cai
            k.atualizar(preco, -sv, 'Vendedor')
        r = k.calcular()
        assert r['kyle_lambda'] > 0, f"lambda deveria ser > 0, veio {r['kyle_lambda']}"


# ============================================================
#  Normalização z-score por EWMA (v9.7)
# ============================================================

class TestEWMAZScore:
    def test_min_amostras(self):
        zt = EWMAZScore(min_amostras=100)
        for _ in range(50):
            zt.atualizar(1.0)
        assert zt.z(5.0) == 0.0  # ainda sem informação suficiente

    def test_z_sinal(self):
        zt = EWMAZScore(alpha=0.05, min_amostras=30, piso=1e-9)
        for i in range(200):
            zt.atualizar(0.0 if i % 2 == 0 else 2.0)  # média 1, std ~1
        assert abs(zt.z(2.0) - 1.0) < 0.35   # acima da média → +1
        assert abs(zt.z(0.0) + 1.0) < 0.35   # abaixo da média → -1
        assert abs(zt.z(1.0)) < 0.35         # na média → ~0

    def test_constante_retorna_zero(self):
        # Sem variância → z ≈ 0 (sem sinal), mesmo antes de convergir totalmente
        zt = EWMAZScore(alpha=0.05, min_amostras=10)
        for _ in range(500):
            zt.atualizar(7.0)
        assert abs(zt.z(7.0)) < 0.01


# ============================================================
#  CVD (delta acumulado) + divergência (v9.8)
# ============================================================

class TestCVD:
    def _jf(self):
        return JanelaFeatures(janela_ms=10_000_000, simbolo='WINV26')

    def test_acumula_delta(self):
        jf = self._jf()
        jf.add_evento(100, 100.0, 10, 'Comprador', 'XP', '')
        jf.add_evento(200, 100.0, 5, 'Comprador', 'XP', '')
        jf.add_evento(300, 100.0, 8, 'Vendedor', '', 'BTG')
        s = jf.snapshot(400)
        assert s['cvd_total'] == 7, f"cvd deveria ser 7, veio {s['cvd_total']}"

    def test_topo_confirma_sem_divergencia(self):
        jf = self._jf()
        for i in range(5):
            jf.add_evento(i * 100, 100.0 + i, 10, 'Comprador', 'XP', '')
        s = jf.snapshot(9999)
        assert s['cvd_div'] == 0  # topo com delta maior = confirmação

    def test_topo_com_divergencia_bearish(self):
        jf = self._jf()
        jf.add_evento(100, 100.0, 10, 'Comprador', 'XP', '')
        jf.add_evento(200, 101.0, 10, 'Comprador', 'XP', '')
        jf.add_evento(300, 102.0, 50, 'Vendedor', '', 'BTG')  # topo com delta caindo
        jf.add_evento(400, 102.0, 5, 'Comprador', 'XP', '')
        s = jf.snapshot(9999)
        assert s['cvd_div'] == -1, f"div deveria ser -1, veio {s['cvd_div']}"

    def test_fundo_com_divergencia_bullish(self):
        jf = self._jf()
        jf.add_evento(100, 100.0, 10, 'Vendedor', '', 'BTG')
        jf.add_evento(200, 99.0, 10, 'Vendedor', '', 'BTG')
        jf.add_evento(300, 98.0, 50, 'Comprador', 'XP', '')   # fundo com delta subindo
        jf.add_evento(400, 98.0, 5, 'Vendedor', '', 'BTG')
        s = jf.snapshot(9999)
        assert s['cvd_div'] == 1, f"div deveria ser 1, veio {s['cvd_div']}"


# ============================================================
#  Volatilidade realizada + range + taxa de eventos (v9.8)
# ============================================================

class TestVolNova:
    def test_preco_constante_vol_zero(self):
        jf = JanelaFeatures(janela_ms=10_000_000, simbolo='WINV26')
        for i in range(20):
            jf.add_evento(i * 100, 100.0, 5, 'Comprador', 'XP', '')
        s = jf.snapshot(9999)
        assert s['realized_vol_bps'] == 0.0
        assert s['range_vol_bps'] == 0.0

    def test_preco_movendo_vol_positiva(self):
        jf = JanelaFeatures(janela_ms=10_000_000, simbolo='WINV26')
        for i in range(20):
            jf.add_evento(i * 100, 100.0 + i * 0.5, 5, 'Comprador', 'XP', '')
        s = jf.snapshot(9999)
        assert s['realized_vol_bps'] > 0
        assert s['range_vol_bps'] > 0

    def test_taxa_eventos(self):
        jf = JanelaFeatures(janela_ms=100)  # janela de 100ms
        for i in range(5):
            jf.add_evento(i * 10, 100.0, 5, 'Comprador', 'XP', '')
        s = jf.snapshot(9999)
        assert s['taxa_eventos'] == 50.0  # 5 eventos / 0.1s


# ============================================================
#  Fase de sessão + dias até vencimento (v9.8)
# ============================================================

class TestSessao:
    def test_fases(self):
        assert fase_sessao(9 * 3600000 + 30 * 60000) == 'abertura'    # 09:30
        assert fase_sessao(11 * 3600000) == 'meio'                     # 11:00
        assert fase_sessao(12 * 3600000 + 30 * 60000) == 'almoco'      # 12:30
        assert fase_sessao(15 * 3600000) == 'meio'                     # 15:00
        assert fase_sessao(16 * 3600000 + 45 * 60000) == 'fechamento'  # 16:45

    def test_tod_de_ts(self):
        import datetime
        epoch = int(datetime.datetime(2026, 8, 21, 10, 30, 0).timestamp() * 1000)
        assert _tod_de_ts(epoch) == 10 * 3600000 + 30 * 60000
        # time-of-day inalterado
        assert _tod_de_ts(37000000) == 37000000

    def test_dias_vencimento(self):
        hoje = date(2026, 8, 21)
        assert dias_ate_vencimento('WINV26', hoje) == (date(2026, 10, 15) - hoje).days
        assert dias_ate_vencimento('WDOU26', hoje) == (date(2026, 9, 15) - hoje).days
        assert dias_ate_vencimento('IND', hoje) is None

    def test_snapshot_inclui_sessao(self):
        jf = JanelaFeatures(janela_ms=10_000_000, simbolo='WINV26')
        jf.add_evento(9 * 3600000, 100.0, 5, 'Comprador', 'XP', '')
        s = jf.snapshot(9 * 3600000)
        assert s['fase_sessao'] == 'abertura'
        assert isinstance(s['dias_ate_venc'], int)

    def test_gerador_inclui_features_novas(self):
        g = GeradorJanelas(['WINV26'], janela_ms=100, passo_ms=100)
        out = g.processar_evento('WINV26', 1000, 100.0, 10, 'Comprador', 'XP', '')
        out2 = g.processar_evento('WINV26', 1100, 100.5, 10, 'Comprador', 'XP', '')
        snaps = out + out2
        assert snaps, "deveria emitir snapshots"
        snap = snaps[-1][1]
        for k in ('cvd_total', 'cvd_div', 'realized_vol_bps', 'range_vol_bps',
                  'taxa_eventos', 'fase_sessao', 'dias_ate_venc'):
            assert k in snap, f"falta a feature {k} no snapshot"


# ============================================================
#  Captura: dedup não pode crashear na poda (v9.8.1)
# ============================================================

def _tod_agora():
    from datetime import datetime
    dt = datetime.now()
    return ((dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000) + dt.microsecond // 1000


class TestCapturaDedup:
    @unittest.skip("B5: dedup removido em v9.22 — _trades_recentes nao existe mais")
    def test_poda_dedup_sem_crash(self):
        pass

    def test_dedup_aceita_duplicatas(self):
        # B5: dedup removido em v9.22 — RTD nunca envia duplicado
        from captura_eventos_ms import CapturaEventosMS
        base = os.path.dirname(os.path.abspath(__file__))
        sess = f'TESTE_DUP_{int(time.time() * 1000)}'
        try:
            cap = CapturaEventosMS(base, session_ts=sess)
            tms = _tod_agora()
            cap.registrar_negocios([('WINV26', tms, 174000.0, 10, 'Comprador', 'XP', 'BTG')])
            cap.registrar_negocios([('WINV26', tms, 174000.0, 10, 'Comprador', 'XP', 'BTG')])
            # B5: dedup removido — ambos devem ser aceitos
            assert cap.rejeitados.get('dup', 0) == 0, f"dup={cap.rejeitados.get('dup', 0)}"
            cap.fechar()
        finally:
            for nome in (f'raw_negocios_ms_{sess}.jsonl', f'raw_book_ms_{sess}.jsonl',
                         f'raw_meta_{sess}.json'):
                p = os.path.join(base, nome)
                if os.path.exists(p):
                    os.remove(p)


class TestCapturaRotacao:
    def test_rotacao_por_tamanho(self):
        # v9.9: arquivo vira em partes (_p01, _p02...) ao passar o limite
        from captura_eventos_ms import CapturaEventosMS
        base = os.path.dirname(os.path.abspath(__file__))
        sess = f'TESTE_ROT_{int(time.time() * 1000)}'
        try:
            cap = CapturaEventosMS(base, session_ts=sess, flush_a_cada=1,
                                   max_bytes_por_arquivo=250)
            tod = _tod_agora()
            for i in range(8):
                cap.registrar_negocios([('WINV26', tod + i, 174000.0, 10,
                                         'Comprador', 'XP', 'BTG')])
            cap.fechar()
            arquivos = sorted(f for f in os.listdir(base)
                              if f.startswith(f'raw_negocios_ms_{sess}'))
            assert len(arquivos) >= 2, f"rotação não aconteceu: {arquivos}"
            total_linhas = 0
            for arq in arquivos:
                with open(os.path.join(base, arq), encoding='utf-8') as fh:
                    total_linhas += sum(1 for _ in fh)
            assert total_linhas == 8, f"total de linhas {total_linhas}"
        finally:
            for f in os.listdir(base):
                if (f.startswith(f'raw_negocios_ms_{sess}') or f.startswith(f'raw_book_ms_{sess}')
                        or f.startswith(f'raw_meta_{sess}')):
                    try:
                        os.remove(os.path.join(base, f))
                    except OSError:
                        pass


class TestCapturaMeta:
    def test_meta_sessao(self):
        # v9.10: fechar() grava raw_meta_<session>.json com contagens
        from captura_eventos_ms import CapturaEventosMS
        base = os.path.dirname(os.path.abspath(__file__))
        sess = f'TESTE_META_{int(time.time() * 1000)}'
        try:
            cap = CapturaEventosMS(base, session_ts=sess, flush_a_cada=1)
            tod = _tod_agora()
            cap.registrar_negocios([('WINV26', tod, 174000.0, 10, 'Comprador', 'XP', 'BTG')])
            cap.registrar_negocios([('WDOU26', tod + 1, 5200.5, 5, 'Vendedor', '', 'BTG')])
            cap.registrar_book('WINV26', tod, {}, 10, 5)
            cap.fechar()
            meta_path = os.path.join(base, f'raw_meta_{sess}.json')
            assert os.path.exists(meta_path), "meta não foi gravado"
            with open(meta_path, encoding='utf-8') as fh:
                meta = json.load(fh)
            assert meta['negocios'] == 2, meta
            assert meta['negocios_por_ativo'].get('WINV26') == 1, meta
            assert meta['book_snapshots'] == 1, meta
            assert meta['fim_epoch_ms'] is not None, meta
        finally:
            for f in os.listdir(base):
                if (f.startswith(f'raw_meta_{sess}') or f.startswith(f'raw_negocios_ms_{sess}')
                        or f.startswith(f'raw_book_ms_{sess}')):
                    try:
                        os.remove(os.path.join(base, f))
                    except OSError:
                        pass


class TestValidarDia:
    def test_dia_sem_arquivos(self):
        from relatorio_diario import validar_dia
        info = validar_dia(os.path.dirname(os.path.abspath(__file__)), '20990101')
        assert info['problemas'], "deveria apontar ausência de arquivos"
        assert 'sem arquivos' in info['problemas'][0]

    def test_dia_saudavel(self):
        from relatorio_diario import validar_dia
        base = os.path.dirname(os.path.abspath(__file__))
        data = '20990101'
        arquivo = os.path.join(base, f'raw_negocios_ms_{data}_TESTEVAL.jsonl')
        try:
            # 600 negócios, span de 5h (30s entre eles) → dia saudável
            with open(arquivo, 'w', encoding='utf-8') as fh:
                for i in range(600):
                    fh.write(json.dumps({'ts_ms': 1700000000000 + i * 30000,
                                         'ativo': 'WINV26', 'preco': 174000.0,
                                         'qtd': 5, 'agressor': 'Comprador'}) + '\n')
            info = validar_dia(base, data)
            assert info['negocios'] == 600, info
            assert info['span_horas'] == 5.0, info
            assert not info['problemas'], info['problemas']
        finally:
            try:
                os.remove(arquivo)
            except OSError:
                pass

    def test_dia_poucos_negocios(self):
        from relatorio_diario import validar_dia
        base = os.path.dirname(os.path.abspath(__file__))
        data = '20990102'
        arquivo = os.path.join(base, f'raw_negocios_ms_{data}_TESTEVAL.jsonl')
        try:
            with open(arquivo, 'w', encoding='utf-8') as fh:
                for i in range(10):
                    fh.write(json.dumps({'ts_ms': 1700000000000 + i * 30000,
                                         'ativo': 'WINV26', 'preco': 174000.0,
                                         'qtd': 5, 'agressor': 'Comprador'}) + '\n')
            info = validar_dia(base, data)
            assert any('poucos negócios' in p for p in info['problemas']), info['problemas']
        finally:
            try:
                os.remove(arquivo)
            except OSError:
                pass


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])