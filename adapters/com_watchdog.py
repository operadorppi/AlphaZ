# -*- coding: utf-8 -*-
"""
adapters/com_watchdog.py — Watchdog COM em thread separada (B2).

Extraído de motor_web.py para respeitar a arquitetura em camadas (v10.1).
O motor_web.py importa daqui.
"""
import time
import threading

# Watchdog COM: constantes (sobrescrever via monkeypatch nos testes)
COM_WATCHDOG_TIMEOUT_S = 10    # timeout padrão (s)
COM_WATCHDOG_CHECK_S = 1       # intervalo de checagem (s)


class COMHeartbeatMonitor:
    """Watchdog COM em thread daemon (B2).

    Monitora o heartbeat do loop COM. Se o heartbeat não for atualizado
    por mais de timeout_s segundos, considera COM travado, chama
    ServerTerminate() e seta stuck_event para o loop sair.

    Uso:
        mon = COMHeartbeatMonitor(srv, timeout_s=10, check_interval_s=1)
        mon.start()
        # no loop:
        mon.heartbeat()          # apos cada RefreshData ok
        if mon.stuck_event.is_set(): break  # COM travado
        # fim:
        mon.stop()
    """

    def __init__(self, srv, timeout_s=None, check_interval_s=None):
        self._srv = srv
        self._timeout_s = timeout_s or COM_WATCHDOG_TIMEOUT_S
        self._check_s = check_interval_s or COM_WATCHDOG_CHECK_S
        self._last_heartbeat = time.time()
        self.stuck_event = threading.Event()
        self.stuck_count = 0
        self._thread = None
        self._stop_event = threading.Event()

    def heartbeat(self):
        """Registra heartbeat (chamar apos cada RefreshData ok)."""
        self._last_heartbeat = time.time()

    def start(self):
        """Inicia a thread monitora daemon."""
        self._stop_event.clear()
        self.stuck_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Para a thread monitora."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        """Loop da thread monitora."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._check_s)
            if self._stop_event.is_set():
                break
            elapsed = time.time() - self._last_heartbeat
            if elapsed > self._timeout_s:
                if not self.stuck_event.is_set():
                    self.stuck_count += 1
                    try:
                        self._srv.ServerTerminate()
                    except Exception:
                        pass
                    self.stuck_event.set()


# ========================================================================
# Gerenciamento do ciclo COM com watchdog (v10.1.1)
# ========================================================================

def watchdog_com_cycle(srv, cycle_fn, *args, **kwargs):
    """Executa um ciclo COM com watchdog integrado.

    O ciclo COM (conexão + loop PumpEvents/RefreshData) vive em motor_web.py,
    mas o gerenciamento do watchdog (start/heartbeat/stop) vive aqui,
    mantendo a separação de responsabilidades.

    Args:
        srv: servidor COM (para o watchdog monitorar)
        cycle_fn: callable que aceita `mon` como primeiro arg extra
                  (ex: _thread_com_ciclo_com_wd)
        *args, **kwargs: argumentos passados para cycle_fn após `mon`

    Returns:
        resultado de cycle_fn
    """
    mon = COMHeartbeatMonitor(srv)
    mon.start()
    try:
        return cycle_fn(mon, *args, **kwargs)
    finally:
        mon.stop()
