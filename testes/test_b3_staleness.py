import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_b3_staleness.py — Testes B3: reconexão por falta de dados (staleness por ativo)

ANTES: o _loop reconectava apenas se `total_neg == 0` ACUMULADO desde o start
do processo — após o 1º negócio o check nunca mais disparava (morto), mesmo com
o RTD mudo por minutos.

DEPOIS: `_verificar_staleness_reconexao` usa staleness REAL por ativo
(ultimo_neg_tempo/ultimo_book_tempo), restrito ao pregão contínuo
(seg-sex 8:45-18:30) e com cooldown entre reconexões.
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime as _RealDatetime, timedelta
import motor_rt_alphaz as mra


class FakeEstado:
    def __init__(self, ultimo_neg_tempo, ultimo_book_tempo, neg_detectados=0):
        self.ultimo_neg_tempo = ultimo_neg_tempo
        self.ultimo_book_tempo = ultimo_book_tempo
        self.neg_detectados = neg_detectados


class FakeApp:
    def __init__(self, estados):
        self._conexao_ok = True
        self._srv = object()
        self._ultima_reconexao = 0.0
        self.estados = estados
        self.reconexoes = 0

    def _reconectar(self):
        self.reconexoes += 1


def _fake_datetime(hora, minuto, weekday=0):
    """Retorna classe FakeDateTime.now() no weekday pedido (0=seg, 5=sab).

    2024-08-18 e domingo; desloca pelo calendario real ate o weekday desejado.
    """
    base = _RealDatetime(2024, 8, 18)  # domingo
    dias = (weekday - base.weekday()) % 7

    class FakeDateTime:
        @staticmethod
        def now():
            return base.replace(hour=hora, minute=minuto) + timedelta(days=dias)
    return FakeDateTime


class TestSemDadosPorAtivo:
    def test_ambos_parados_fora_pre_abertura(self):
        agora = time.time()
        est = FakeEstado(agora - 60, agora - 60)
        assert mra._sem_dados_por_ativo(est, agora, pre_abertura=False)

    def test_book_vivo_nao_conta(self):
        agora = time.time()
        est = FakeEstado(agora - 60, agora - 5)  # trades parados, book vivo
        assert not mra._sem_dados_por_ativo(est, agora, pre_abertura=False)

    def test_pre_abertura_so_book_conta(self):
        agora = time.time()
        est = FakeEstado(agora - 60, agora - 40)  # book parado > 30s
        assert mra._sem_dados_por_ativo(est, agora, pre_abertura=True)
        est2 = FakeEstado(agora - 60, agora - 20)  # book atualizado
        assert not mra._sem_dados_por_ativo(est2, agora, pre_abertura=True)

    def test_dados_recentes_nao_conta(self):
        agora = time.time()
        est = FakeEstado(agora - 5, agora - 5)
        assert not mra._sem_dados_por_ativo(est, agora, pre_abertura=False)


class TestVerificarStalenessReconexao:
    def test_reconecta_apos_primeiro_trade(self, monkeypatch):
        """B3: com neg_detectados acumulado != 0 o check antigo NUNCA disparava.
        O novo detecta silêncio real e reconecta mesmo tendo visto negócios."""
        agora = time.time()
        est = FakeEstado(agora - 60, agora - 60, neg_detectados=150)
        app = FakeApp({'WINV26': est})
        _fdt = _fake_datetime(10, 0)  # pregão
        import core.app as _ca
        monkeypatch.setattr(_ca, 'datetime', _fdt)
        monkeypatch.setattr(mra, 'datetime', _fdt)

        assert app.reconexoes == 0
        assert mra.App._verificar_staleness_reconexao(app) is True
        assert app.reconexoes == 1

    def test_cooldown_evita_loop(self, monkeypatch):
        agora = time.time()
        est = FakeEstado(agora - 60, agora - 60)
        app = FakeApp({'WINV26': est})
        _fdt = _fake_datetime(10, 0)
        import core.app as _ca
        monkeypatch.setattr(_ca, 'datetime', _fdt)
        monkeypatch.setattr(mra, 'datetime', _fdt)

        mra.App._verificar_staleness_reconexao(app)
        assert app.reconexoes == 1
        # imediatamente depois: cooldown de 30s impede nova reconexão
        mra.App._verificar_staleness_reconexao(app)
        assert app.reconexoes == 1

    def test_fora_do_pregao_nao_reconecta(self, monkeypatch):
        agora = time.time()
        est = FakeEstado(agora - 600, agora - 600)
        app = FakeApp({'WINV26': est})
        _fdt = _fake_datetime(23, 0)  # mercado fechado
        import core.app as _ca
        monkeypatch.setattr(_ca, 'datetime', _fdt)
        monkeypatch.setattr(mra, 'datetime', _fdt)

        assert mra.App._verificar_staleness_reconexao(app) is False
        assert app.reconexoes == 0

    def test_fim_de_semana_nao_reconecta(self, monkeypatch):
        agora = time.time()
        est = FakeEstado(agora - 600, agora - 600)
        app = FakeApp({'WINV26': est})
        _fdt = _fake_datetime(10, 0, weekday=5)  # sábado
        import core.app as _ca
        monkeypatch.setattr(_ca, 'datetime', _fdt)
        monkeypatch.setattr(mra, 'datetime', _fdt)

        assert mra.App._verificar_staleness_reconexao(app) is False
        assert app.reconexoes == 0

    def test_sem_conexao_nao_faz_nada(self, monkeypatch):
        agora = time.time()
        est = FakeEstado(agora - 600, agora - 600)
        app = FakeApp({'WINV26': est})
        app._conexao_ok = False
        _fdt = _fake_datetime(10, 0)
        import core.app as _ca
        monkeypatch.setattr(_ca, 'datetime', _fdt)
        monkeypatch.setattr(mra, 'datetime', _fdt)

        assert mra.App._verificar_staleness_reconexao(app) is False
        assert app.reconexoes == 0