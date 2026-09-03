# -*- coding: utf-8 -*-
"""
testes/test_cross_asset_estrito_v1518.py — CrossAssetEngine aceita SOMENTE o
par principal×contexto (P0-A24).

Antes: qualquer ativo ≠ principal caía no hist_wdo (virou "contexto WDO" por
padrão) — um instrumento fora do par por erro de configuração contaminava o
contexto. Cenários:
  1. Principal → hist_win; contexto → hist_wdo (roteamento correto).
  2. Ativo desconhecido → REJEITADO (False), nunca entra em nenhum lado.
  3. Rajada de ativo desconhecido não altera features do par (sem contaminação).
  4. registrar() devolve True só para membros do par.
  5. Manager: ativo fora de todos os pares não toca nenhum engine.
  6. Construtor posicional (regressão do teste antigo que passava errado).
  7. Engine só com rejeitados → calcular() devolve zeros (nada vira contexto).
"""

import inspect

from features.cross_asset import CrossAssetEngine, CrossAssetManager

WIN = 'WINV26'
WDO = 'WDOV26'
FORA = 'INDV26'  # instrumento capturado no mesmo motor, fora do par WIN×WDO


def _engine():
    return CrossAssetEngine(ativo_principal=WIN, ativo_contexto=WDO)


class TestRoteamentoEstrito:
    def test_principal_e_contexto_roteiam_certo(self):
        eng = _engine()
        assert eng.registrar(WIN, 1000, 100.0, 0.3) is True
        assert eng.registrar(WDO, 1000, 50.0, 0.3) is True
        assert len(eng.hist_win) == 1
        assert len(eng.hist_wdo) == 1
        assert eng.total_rejeitados == 0

    def test_ativo_fora_do_par_rejeitado(self):
        eng = _engine()
        assert eng.registrar(WIN, 1000, 100.0, 0.3) is True
        assert eng.registrar(FORA, 1000, 999.0, -0.9) is False
        assert eng.registrar('WDO_RLP', 1000, 51.0, 0.2) is False
        # Nada entrou: hist_wdo vazio (não virou "contexto"), hist_win intocado
        assert len(eng.hist_win) == 1
        assert len(eng.hist_wdo) == 0
        assert eng.total_rejeitados == 2
        assert eng._rejeitados.get(FORA) == 1

    def test_rajada_fora_do_par_nao_contamina_features(self):
        eng = _engine()
        for i in range(0, 9):
            t = 1000 + i * 1000
            eng.registrar(WIN, t, 100.0 + i, 0.3)
            eng.registrar(WDO, t, 50.0 + 2 * i, 0.3)
        f_antes = eng.calcular(9000)
        n_antes = len(eng.hist_wdo)
        # Rajada violenta de um instrumento fora do par
        for j in range(200):
            eng.registrar(FORA, 10000 + j, 500.0 + j, -0.9)
        assert len(eng.hist_wdo) == n_antes
        assert eng.calcular(9000) == f_antes
        assert eng.total_rejeitados == 200

    def test_engine_so_com_rejeitados_retorna_zeros(self):
        eng = _engine()
        for j in range(10):
            eng.registrar(FORA, 1000 + j, 500.0 + j, -0.9)
        assert eng.total_rejeitados == 10
        z = eng.calcular(5000)
        assert z['lag_ms'] == 0 and z['wdo_delta'] == 0.0

    def test_construtor_posicional_roteia_certo(self):
        """Regressão: o teste antigo construía CrossAssetEngine('WIN','WDO')
        posicional e (com a assinatura antiga) o principal virava None — tudo
        ia para o contexto. Com a nova ordem, roteia certo e rejeita fora."""
        eng = CrossAssetEngine(WIN, WDO)  # posicional = principal, contexto
        assert eng.ativo_principal == WIN
        assert eng.ativo_contexto == WDO
        assert eng.registrar(WIN, 1000, 100.0, 0.3) is True
        assert eng.registrar(WDO, 1000, 50.0, 0.3) is True
        assert eng.registrar(FORA, 1000, 1.0, 0.0) is False
        assert len(eng.hist_win) == 1 and len(eng.hist_wdo) == 1


class TestManagerEstrito:
    def test_manager_nao_entrega_fora_do_par_ao_engine(self):
        mgr = CrossAssetManager(pairs=[[WIN, WDO]])
        mgr.registrar(WIN, 1000, 100.0, 0.3)
        mgr.registrar(WDO, 1000, 50.0, 0.3)
        eng = next(iter(mgr.engines.values()))
        n_wdo = len(eng.hist_wdo)
        # Fora de TODOS os pares: manager não deve chamar nenhum engine
        mgr.registrar(FORA, 2000, 999.0, -0.9)
        assert len(eng.hist_wdo) == n_wdo
        assert len(eng.hist_win) == 1
        assert eng.total_rejeitados == 0  # nem chegou ao engine


class TestFonte:
    def test_guard_estrutural(self):
        """O código vivo do registrar não pode ter else que vira contexto."""
        src = inspect.getsource(CrossAssetEngine.registrar)
        assert 'self.hist_wdo' in src
        assert 'rejeitado' in src.lower()
