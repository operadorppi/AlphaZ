import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_com_watchdog.py — Testes B2: watchdog COM em thread separada

ANTES da correcao: o loop COM (_thread_com_ciclo) nao tinha nenhum watchdog
em thread separada. Se PumpEvents/RefreshData/ConnectData bloqueassem, a
thread ficava presa para sempre, sem deteccao nem reconexao. O unico
"watchdog" era um timer de polling (POLL_S) que nunca detectava travamento.

DEPOIS da correcao: COMHeartbeatMonitor roda em daemon thread, monitora o
heartbeat do loop, detecta estagnacao > timeout e sinaliza stuck_event,
fazendo o loop sair e o thread_com reconectar.

Os testes abaixo validam:
  1. Deteccao de bloqueio (sem heartbeat) -> stuck_event setado
  2. Heartbeat continuo -> falso positivo nao ocorre
  3. stuck_count nao incrementa apos primeira deteccao
  4. Integracao: loop sai via stuck_event quando _refresh bloqueia
"""
import sys
import os
import time
import threading
import queue
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# motor_web.py foi decomposto na refatoracao v10.1. COMHeartbeatMonitor e as
# constantes de watchdog vivem agora em adapters.com_watchdog.
# O loop COM em si (_thread_com_ciclo) NAO foi migrado: o substituto e
# ProfitRTDAdapter.events() (adapters/profit_rtd.py), gerador de MarketEvent.
from adapters import com_watchdog as mw


# ============================================================================
# CLASSES E HELPERs
# ============================================================================

class FakeServer:
    """Simula o servidor RTD COM para os testes do watchdog."""
    def __init__(self):
        self.started = False
        self.terminated = False
        self.terminate_calls = 0
        self.disconnect_calls = 0

    def ServerStart(self, cb):
        self.started = True

    def DisconnectData(self, tid):
        self.disconnect_calls += 1

    def ServerTerminate(self):
        self.terminate_calls += 1
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


# ============================================================================
# TESTES UNITARIOS DO COMHeartbeatMonitor
# ============================================================================

class TestCOMHeartbeatMonitor:
    """Testes unitarios do COMHeartbeatMonitor (B2)."""

    def test_detecta_bloqueio_sem_heartbeat(self):
        """Monitor detecta bloqueio quando heartbeat nao e atualizado."""
        srv = FakeServer()
        mon = mw.COMHeartbeatMonitor(srv, timeout_s=0.2, check_interval_s=0.05)
        mon.heartbeat()  # unico heartbeat — depois fica estagnado
        mon.start()
        try:
            time.sleep(0.8)  # >> timeout_s
            assert mon.stuck_event.is_set(), (
                "stuck_event deveria estar setado apos bloqueio de 0.8s com timeout=0.2s"
            )
            assert mon.stuck_count == 1, (
                f"stuck_count deveria ser 1, obtido {mon.stuck_count}"
            )
            # ServerTerminate deve ter sido chamado
            assert srv.terminate_calls >= 1, "ServerTerminate deveria ter sido chamado"
        finally:
            mon.stop()

    def test_nao_dispara_com_heartbeat_continuo(self):
        """Monitor NAO acusa falso positivo quando heartbeat e mantido."""
        srv = FakeServer()
        mon = mw.COMHeartbeatMonitor(srv, timeout_s=0.2, check_interval_s=0.05)
        mon.start()
        try:
            for _ in range(30):
                mon.heartbeat()
                time.sleep(0.04)  # total ~1.2s, heartbeat mantido
            assert not mon.stuck_event.is_set(), (
                "stuck_event NAO deveria estar setado com heartbeat continuo"
            )
            assert mon.stuck_count == 0, (
                f"stuck_count deveria ser 0, obtido {mon.stuck_count}"
            )
        finally:
            mon.stop()

    def test_nao_repete_deteccao_apos_stuck(self):
        """Apos stuck, monitor nao incrementa contagem repetidamente."""
        srv = FakeServer()
        mon = mw.COMHeartbeatMonitor(srv, timeout_s=0.15, check_interval_s=0.05)
        mon.heartbeat()
        mon.start()
        try:
            time.sleep(0.6)  # detecta, espera mais
            assert mon.stuck_count == 1, (
                f"stuck_count deveria ser 1 (uma deteccao), obtido {mon.stuck_count}"
            )
        finally:
            mon.stop()

    def test_stop_encerra_thread(self):
        """stop() encerra a thread monitora."""
        srv = FakeServer()
        mon = mw.COMHeartbeatMonitor(srv, timeout_s=1.0, check_interval_s=0.1)
        mon.start()
        assert mon._thread is not None and mon._thread.is_alive()
        mon.stop()
        assert mon._thread is None or not mon._thread.is_alive()


# ============================================================================
# TESTE DE INTEGRACAO: loop COM com _refresh bloqueante
# ============================================================================

class TestCOMThreadCicloWatchdog:
    """Teste de integracao do watchdog com o loop COM.

    Simula um _refresh que BLOQUEIA (cenario C4: COM travado).
    O watchdog em thread separada detecta, sinaliza stuck_event e o
    loop sai para reconexao.

    ANTES da correcao: o loop nunca sairia (thread presa indefinidamente).
    DEPOIS: o watchtog detecta, o loop break, thread_com reconecta.
    """

    @staticmethod
    def _fake_connect(srv, strings):
        _next_tid = getattr(TestCOMThreadCicloWatchdog, '_next_tid', 1000)
        TestCOMThreadCicloWatchdog._next_tid = _next_tid + 1
        return _next_tid, None

    @staticmethod
    def _fake_refresh_blocking(srv):
        """Simula RefreshData bloqueante ate o watchdog forcar ServerTerminate."""
        SLEEP_STEP = 0.02
        MAX_WAIT_S = 30  # safety limit
        waited = 0.0
        while not srv.terminated and waited < MAX_WAIT_S:
            time.sleep(SLEEP_STEP)
            waited += SLEEP_STEP
        if not srv.terminated:
            raise RuntimeError("TIMEOUT: _refresh_blocking nao foi desbloqueado")
        raise RuntimeError("COM encerrado pelo watchdog")  # simula erro apos terminacao

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "ORFAO DA REFATORACAO v10.1: `_thread_com_ciclo` (loop COM baseado em "
            "filas) nao existe mais em nenhum modulo vivo — so em "
            "docs/archive/motor_web_legacy.py. O sucessor arquitetural e "
            "ProfitRTDAdapter.events() (adapters/profit_rtd.py), um gerador de "
            "MarketEvent, que NAO usa filas, stats_lock, live_stats nem o dict "
            "`estado`. Reescrever este teste exige remodelar a integracao "
            "watchdog x loop contra o novo adapter (ver ONBOARDING)."
        ),
    )
    def test_loop_sai_com_watchdog_quando_com_trava(self, monkeypatch):
        """_thread_com_ciclo sai via stuck_event quando _refresh bloqueia.

        O watchdog (COMHeartbeatMonitor) detecta o bloqueio, chama
        ServerTerminate, seta stuck_event, e o loop break para reconexao.
        """
        # --- Patches ---
        monkeypatch.setattr(mw, 'comtypes', MagicMock())
        monkeypatch.setattr(mw.comtypes, 'CoInitialize', lambda: None)
        monkeypatch.setattr(mw.comtypes, 'CoUninitialize', lambda: None)
        monkeypatch.setattr(mw.comtypes.client, 'PumpEvents', lambda t: None)

        srv = FakeServer()
        monkeypatch.setattr(mw, 'conectar_servidor', lambda: (srv, FakeIRTDUpdateEvent))
        monkeypatch.setattr(mw, '_criar_callback', lambda *a: FakeCallback())
        monkeypatch.setattr(mw, '_connect', self._fake_connect)
        monkeypatch.setattr(mw, 'parse_refresh_data', lambda data: [])
        monkeypatch.setattr(mw, '_refresh', self._fake_refresh_blocking)

        # Acelera timeout do watchdog para o teste
        # Patcha adapters.com_watchdog (onde COMHeartbeatMonitor vive)
        import adapters.com_watchdog as _acw
        monkeypatch.setattr(_acw, 'COM_WATCHDOG_TIMEOUT_S', 0.3)
        monkeypatch.setattr(_acw, 'COM_WATCHDOG_CHECK_S', 0.05)
        monkeypatch.setattr(mw, 'COM_WATCHDOG_TIMEOUT_S', 0.3)
        monkeypatch.setattr(mw, 'COM_WATCHDOG_CHECK_S', 0.05)

        # Stats no-ops
        monkeypatch.setattr(mw, '_registrar_book', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_tt', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_stat', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_live_inc', lambda *a, **k: None)
        monkeypatch.setattr(mw, 'logger', MagicMock())

        # --- Setup ---
        shutdown_event = threading.Event()
        ativos = [{'book': 'WINFUT', 'tt': 'WIN_TT', 'simbolo': 'WINV26'}]
        fila_book = queue.Queue()
        fila_tt = queue.Queue()

        # --- Execucao (timeout total de 10s) ---
        resultado = []

        def run():
            try:
                conectou, estado = mw._thread_com_ciclo(
                    [fila_book], [fila_tt], ativos, '/tmp/teste',
                    shutdown_event, None, None, None
                )
                resultado.append((conectou, estado))
            except Exception as e:
                resultado.append(('EXCEPTION', e))

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=10.0)

        # --- Asserts ---
        assert len(resultado) == 1, f"Loop nao retornou: {resultado}"
        conectou, estado = resultado[0]
        assert conectou is True or conectou is False, (
            f"_thread_com_ciclo deveria ter retornado (True/False), recebeu: {conectou}"
        )
        assert srv.terminated, (
            "ServerTerminate deveria ter sido chamado pelo watchdog (COM travado)"
        )
        assert srv.terminate_calls >= 1, (
            "ServerTerminate deveria ter sido chamado pelo menos 1x"
        )
        # Verifica que o estado foi preservado (contem dia_replay etc.)
        assert isinstance(estado, dict), f"estado deveria ser dict, obtido: {type(estado)}"
        assert 'dia_replay' in estado, "estado deveria conter dia_replay"

        # Opcional: verifica que shutdown_event NAO foi setado (loop saiu limpo)
        assert not shutdown_event.is_set(), "shutdown_event nao deveria estar setado"