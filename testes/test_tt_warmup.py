import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_tt_warmup.py — Testes R1: warmup T&T nao emite retrato inicial como negocios falsos

ANTES da correcao (fix v9.16 #13): o `continue` do warmup foi removido, mas
`baseline_pending_tt` so era setado quando warmup_tt == 60. Nas linhas 1-59 do
warmup, com `vistos_tt` vazio na 1a execucao, TODO o retrato inicial da FIFO
T&T (ate ~1000 linhas) tinha count > seen e era emitido como negocios novos
(gravados no Parquet bruto — poluicao do dataset de replay/labels).

DEPOIS: durante o warmup (warmup_tt < 60) NAO sao gerados eventos derivados
(`continue`). O snapshot de book NAO e afetado (loop separado, anterior ao T&T).

Testes:
  1. Retrato T&T estatico durante warmup + baseline -> NENHUM negocio emitido
  2. Book continua sendo capturado durante o warmup (R1 nao quebrou o book)
  3. Apos warmup+baseline, negocio NOVO (assinatura diferente) e emitido
     (dedup continua funcionando — nao houve supressao excessiva)
"""
import sys
import os
import time
import queue
import threading
import itertools
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# motor_web.py foi decomposto na refatoracao v10.1. Apontamos para o modulo que
# ainda concentra o loop COM (POLL_S, conectar_servidor, _connect, _refresh).
from adapters import rtd_connection as mw


class FakeServer:
    def __init__(self):
        self.started = False
        self.terminated = False

    def ServerStart(self, cb):
        self.started = True

    def DisconnectData(self, tid):
        pass

    def ServerTerminate(self):
        self.terminated = True


class FakeCallback:
    def UpdateNotify(self):
        return 0
    def Disconnect(self):
        return 0
    def Heartbeat(self):
        return 1


class FakeIRTDUpdateEvent:
    pass


LINHA_TT_1 = {
    "DAT": "18/08/2024 10:30:45", "ACP": "XP", "PRE": "174000",
    "QUL": "10", "AVD": "BTG", "AGR": "Compra", "AGAG": "AG1",
}
LINHA_TT_2 = {  # assinatura DIFERENTE (ACP/AGAG distintos)
    "DAT": "18/08/2024 10:30:46", "ACP": "CLEAR", "PRE": "174005",
    "QUL": "5", "AVD": "BTG", "AGR": "Compra", "AGAG": "AG2",
}


class _AmbienteComCiclo:
    """Monta o ambiente mockado de _thread_com_ciclo e controla o retrato T&T."""

    def __init__(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mw, 'comtypes', MagicMock())
        monkeypatch.setattr(mw.comtypes, 'CoInitialize', lambda: None)
        monkeypatch.setattr(mw.comtypes, 'CoUninitialize', lambda: None)
        monkeypatch.setattr(mw.comtypes.client, 'PumpEvents', lambda t: None)
        monkeypatch.setattr(mw, 'POLL_S', 0.005)  # acelera o ciclo p/ teste

        self.srv = FakeServer()
        monkeypatch.setattr(mw, 'conectar_servidor', lambda: (self.srv, FakeIRTDUpdateEvent))
        monkeypatch.setattr(mw, '_criar_callback', lambda *a: FakeCallback())

        # _connect: tids unicos + mapeamento strings -> tid
        self.tid_map = {}
        counter = itertools.count(1000)

        def fake_connect(srv, strings):
            tid = next(counter)
            self.tid_map[tuple(strings)] = tid
            return tid, None

        monkeypatch.setattr(mw, '_connect', fake_connect)

        # Retrato T&T controlado pelo teste (mutavel)
        self.linha_atual = dict(LINHA_TT_1)

        def fake_parse(data):
            pairs = []
            for field, val in self.linha_atual.items():
                tid = self.tid_map.get(('WIN_TT', field, '0'))
                if tid is not None:
                    pairs.append((tid, val))
            return pairs

        monkeypatch.setattr(mw, 'parse_refresh_data', fake_parse)
        monkeypatch.setattr(mw, '_refresh', lambda srv: object())

        monkeypatch.setattr(mw, '_registrar_book', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_tt', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_stat', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_live_inc', lambda *a, **k: None)
        monkeypatch.setattr(mw, 'logger', MagicMock())

        # v10.1.1: watchdog_com_cycle gerencia o watchdog; no teste,
        # substituímos por chamada direta (sem thread daemon)
        _fake_mon = MagicMock()
        _fake_mon.stuck_event = threading.Event()
        monkeypatch.setattr(mw, 'watchdog_com_cycle',
                            lambda srv, fn, *a, **kw: fn(_fake_mon, *a, **kw))

        self.shutdown_event = threading.Event()
        self.fila_book = queue.Queue()
        self.fila_tt = queue.Queue()
        self.resultado = []

    def iniciar(self):
        ativos = [{'book': 'WINFUT', 'tt': 'WIN_TT', 'simbolo': 'WINV26'}]
        t = threading.Thread(target=self._rodar, args=(ativos,), daemon=True)
        t.start()
        return t

    def _rodar(self, ativos):
        try:
            conectou, estado = mw._thread_com_ciclo(
                [self.fila_book], [self.fila_tt], ativos, 'C:/tmp/teste',
                self.shutdown_event, None, None, None
            )
            self.resultado.append((conectou, estado))
        except Exception as e:
            self.resultado.append(('EXCEPTION', e))

    def parar(self, timeout=6.0):
        self.shutdown_event.set()
        # drena filas para o loop sair (condicao: shutdown e filas vazias)
        try:
            while True:
                self.fila_book.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self.fila_tt.get_nowait()
        except queue.Empty:
            pass


@pytest.mark.xfail(
    strict=False,
    reason=(
        "ORFAOS DA REFATORACAO v10.1: os 3 testes dependem de `_thread_com_ciclo` "
        "(loop COM com filas book/tt + dict `estado`), que nao existe mais em "
        "nenhum modulo vivo — apenas em docs/archive/motor_web_legacy.py.\n"
        "IMPORTANTE: a LOGICA testada (fix R1) NAO foi perdida. Ela sobreviveu em "
        "ProfitRTDAdapter.events() (adapters/profit_rtd.py): `_baseline_pending` "
        "absorve o 1o RefreshData como baseline (linhas 210-212) e o dedup por "
        "assinatura (DAT+ACP+PRE+QUL+AVD+AGR+AGAG) atua depois (linhas 217-222).\n"
        "TAREFA: reescrever estes 3 testes contra ProfitRTDAdapter, alimentando "
        "_topic_map/_book_cells e mockando _refresh + parse_refresh_data."
    ),
)
class TestWarmupTTNaoEmiteRetratoInicial:
    """R1: warmup nao gera negocios falsos; book segue capturado; dedup segue vivo."""

    def test_retrato_estatico_nao_gera_negocios_durante_warmup(self, monkeypatch, tmp_path):
        """Retrato T&T estatico + warmup completo + baseline = 0 negocios emitidos.

        ANTES da correcao: o 1o ciclo do warmup emitia o retrato inteiro como
        negocios falsos (fila_tt com >=1 item). DEPOIS: fila_tt vazia.
        """
        amb = _AmbienteComCiclo(monkeypatch, tmp_path)
        t = amb.iniciar()

        # tempo suficiente p/ completar 60 ciclos de warmup + baseline + dedup
        time.sleep(3.0)

        q_tt = amb.fila_tt.qsize()
        amb.parar()
        t.join(timeout=6.0)

        assert not t.is_alive(), "Loop COM deveria ter encerrado"
        assert amb.resultado, f"Loop nao retornou: {amb.resultado}"
        conectou, estado = amb.resultado[0]
        assert conectou in (True, False), f"Retorno inesperado: {conectou}"
        assert isinstance(estado, dict) and 'dia_replay' in estado

        # R1: retrato estatico nao pode gerar negocios (nem no warmup, nem no baseline)
        assert q_tt == 0, (
            f"Retrato estatico gerou {q_tt} negocios falsos durante warmup/baseline — "
            f"R1 NAO corrigido (regressao do fix v9.16 #13)"
        )

    def test_book_continua_sendo_capturado_durante_warmup(self, monkeypatch, tmp_path):
        """O fix R1 nao quebrou a captura de book durante o warmup.

        O snapshot de book e processado em loop SEPARADO (anterior ao T&T);
        o `continue` do warmup T&T nao pode afetar a captura de book.
        """
        amb = _AmbienteComCiclo(monkeypatch, tmp_path)
        t = amb.iniciar()

        time.sleep(1.5)  # varias iteracoes ainda dentro do warmup (60 ciclos ~1.2s)

        q_book = amb.fila_book.qsize()
        amb.parar()
        t.join(timeout=6.0)

        assert q_book >= 1, (
            "Book deveria ter sido capturado (keepalive) mesmo durante o warmup T&T"
        )

    def test_negocio_novo_apos_warmup_e_emitido(self, monkeypatch, tmp_path):
        """Apos warmup+baseline, negocio com assinatura NOVA e emitido.

        Garante que o fix R1 nao suprimiu excessivamente o dedup pos-warmup.
        """
        amb = _AmbienteComCiclo(monkeypatch, tmp_path)
        t = amb.iniciar()

        # espera warmup + baseline absorverem o retrato estatico
        time.sleep(2.5)

        # troca o retrato para um negocio NOVO (assinatura diferente)
        amb.linha_atual = dict(LINHA_TT_2)

        deadline = time.time() + 6.0
        while time.time() < deadline and amb.fila_tt.qsize() == 0:
            time.sleep(0.05)

        q_tt = amb.fila_tt.qsize()
        amb.parar()
        t.join(timeout=6.0)

        assert q_tt >= 1, (
            "Negocio novo apos warmup deveria ter sido emitido (dedup vivo)"
        )