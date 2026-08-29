"""
watchdog.py — Monitor que reinicia o motor automaticamente se ele morrer.

Uso:
  python watchdog.py WINV26 WDOU26

Funcionamento:
  1. Verifica se ja existe outro watchdog rodando (anti-duplicata)
  2. Verifica se o motor ja esta rodando (nao duplica)
  3. Inicia o motor como subprocesso
  4. A cada 30s verifica se o processo esta vivo
  5. Se morreu, reinicia apos delay de esfriamento
  6. Limita reinicios: max 10 por hora (evita loop infinito)
  7. Log em watchdog.log
"""
import sys
import os
import time
import signal
import subprocess
import logging
import hashlib
from datetime import datetime, timedelta

# ============================================================
#  CONFIG
# ============================================================
MOTOR_SCRIPT = "run_motor.py"  # v10: entry point unificado
CHECK_INTERVAL_S = 30
RESTART_DELAY_S = 45
MAX_RESTARTS_POR_HORA = 10
STARTUP_GRACE_S = 120  # 2 minutos para RTD + modelo + dashboard
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog.log")
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".watchdog.lock")

# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WD] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("Watchdog")


# ============================================================
#  LOCK FILE (anti-duplicata)
# ============================================================
class WatchdogLock:
    """Impede que duas instancias do watchdog rodem ao mesmo tempo."""

    def __init__(self, path):
        self.path = path
        self.pid = os.getpid()
        self._locked = False

    def acquire(self):
        """Tenta criar o lock file. Retorna True se conseguiu."""
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    old_pid = int(f.read().strip())
                # Verifica se o processo antigo ainda existe
                if self._pid_alive(old_pid):
                    log.error(f"Outro watchdog ja rodando (PID={old_pid}). Saindo.")
                    return False
                else:
                    log.warning(f"Lock file obsoleto (PID={old_pid} morto). Removendo.")
                    os.remove(self.path)
            except (ValueError, OSError):
                log.warning("Lock file corrompido. Removendo.")
                try:
                    os.remove(self.path)
                except OSError:
                    pass

        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(self.pid).encode())
            os.close(fd)
            self._locked = True
            return True
        except FileExistsError:
            log.error("Lock file ja existe. Outro watchdog pode estar rodando.")
            return False

    def release(self):
        if self._locked and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass
            self._locked = False

    def _pid_alive(self, pid):
        """Verifica se um PID esta vivo de forma cross-platform."""
        if os.name == 'nt':
            try:
                result = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}'],
                    capture_output=True, text=True, timeout=5
                )
                return str(pid) in result.stdout
            except Exception: return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError: return False


# ============================================================
#  WATCHDOG
# ============================================================
class Watchdog:
    def __init__(self, args):
        self.args = args
        self.motor_proc = None
        self.reinicios = []
        self.running = True
        self._script_dir = os.path.dirname(os.path.abspath(__file__))
        self._motor_path = os.path.join(self._script_dir, MOTOR_SCRIPT)

    def _motor_ja_rodando(self):
        """Verifica se o motor ja esta rodando de forma agnostica a plataforma."""
        if os.name == 'nt':
            try:
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     ("Get-CimInstance Win32_Process | Where-Object { "
                      "$_.Name -like 'python*' -and "
                      "$_.CommandLine -like '*run_motor*' } | "
                      "Select-Object -ExpandProperty ProcessId")],
                    capture_output=True, text=True, timeout=10
                )
                return bool(result.stdout.strip())
            except Exception: return False
        if os.name != 'nt':
            try:
                import psutil
                for proc in psutil.process_iter(['cmdline']):
                    try:
                        cmd = " ".join(proc.info['cmdline'] or [])
                        if MOTOR_SCRIPT in cmd and proc.pid != os.getpid():
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except ImportError:
                log.warning("psutil não instalado. Verificação de processo Linux limitada.")
            except Exception as e:
                log.warning(f"Erro ao verificar motor: {e}")
        return False

    def _iniciar_motor(self):
        """Inicia o motor como subprocesso."""
        cmd = [sys.executable, self._motor_path] + self.args
        log.info(f"Iniciando motor: {' '.join(cmd)}")

        try:
            log_stdout = open(
                os.path.join(self._script_dir, 'motor_stdout.log'), 'ab'
            )
            # Remove creationflags no Linux
            cf = 0x08000000 if os.name == 'nt' else 0
            self.motor_proc = subprocess.Popen(
                cmd,
                cwd=self._script_dir,
                stdout=log_stdout,
                stderr=subprocess.STDOUT,
                creationflags=cf,
            )
            self._log_stdout = log_stdout
            log.info(f"Motor iniciado PID={self.motor_proc.pid}")
            return True
        except Exception as e:
            log.error(f"Falha ao iniciar motor: {e}")
            return False

    def _matar_motor(self):
        """Mata o motor de forma robusta."""
        if self.motor_proc and self.motor_proc.poll() is None:
            pid = self.motor_proc.pid
            log.info(f"Mantando motor PID={pid}")

            # 1. Tentar terminate (WM_CLOSE)
            try:
                self.motor_proc.terminate()
                self.motor_proc.wait(timeout=5)
                log.info(f"Motor PID={pid} encerrado graceful")
                self._fechar_log()
                return
            except subprocess.TimeoutExpired:
                log.warning(f"Motor PID={pid} nao respondeu a terminate")
            except Exception:
                pass

            # 2. Tentar kill (forcado)
            try:
                self.motor_proc.kill()
                self.motor_proc.wait(timeout=3)
                log.info(f"Motor PID={pid} morto (kill)")
                self._fechar_log()
                return
            except Exception:
                pass

            # 3. taskkill forcado (ultimo recurso)
            try:
                subprocess.run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True, timeout=5
                )
                log.info(f"Motor PID={pid} morto (taskkill /F)")
            except Exception:
                log.error(f"Falha ao matar motor PID={pid}")

        self._consolidar_parquets()
        self._fechar_log()

    def _consolidar_parquets(self):
        """Consolida arquivos .part_*.parquet em arquivos únicos por hora."""
        try:
            from adapters.rtd_writer import consolidar_book_parquet, consolidar_tt_parquet
            from datetime import date
            pasta = os.path.join(self._script_dir, 'D:\\MarketData\\Profit')
            dia_str = date.today().strftime('%Y%m%d')
            log.info(f'[WD] Consolidando Parquets do dia {dia_str}...')
            n_book = consolidar_book_parquet(pasta, dia_str)
            n_tt = consolidar_tt_parquet(pasta, dia_str)
            log.info(f'[WD] Consolidados: {n_book} book + {n_tt} TT')
        except Exception as e:
            log.warning(f'[WD] Falha na consolidacao: {e}')

    def _fechar_log(self):
        if hasattr(self, '_log_stdout') and self._log_stdout:
            try:
                self._log_stdout.close()
            except Exception:
                pass

    def _esta_vivo(self):
        """Verifica se o motor esta vivo (processo Python rodando)."""
        if self.motor_proc is None:
            return False
        exit_code = self.motor_proc.poll()
        if exit_code is not None:
            log.warning(f"Motor morreu (exit code={exit_code})")
            return False
        return True

    def _pode_reiniciar(self):
        """Verifica se nao excedeu o limite de reinicios.
        v9.35: nao limita reinicios se o watchdog ainda nao viu o motor
        funcionar (primeiros restarts sao normais — RTD pode nao estar pronto).
        So limita apos o motor ja ter ficado vivo por >5min (Startup grace + margem)."""
        agora = datetime.now()
        self.reinicios = [t for t in self.reinicios if agora - t < timedelta(hours=1)]
        if len(self.reinicios) >= MAX_RESTARTS_POR_HORA:
            # Se o motor ja funcionou por >5min alguma vez, o limite vale.
            # Se nunca funcionou, e o RTD que nao esta pronto — nao limitar.
            if getattr(self, '_motor_ja_funcionou', False):
                log.error(
                    f"Maximo de {MAX_RESTARTS_POR_HORA} reinicios/hora atingido "
                    f"(motor ja funcionou antes). PARANDO watchdog."
                )
                return False
            else:
                log.warning(
                    f"Maximo de {MAX_RESTARTS_POR_HORA} reinicios/hora atingido "
                    f"mas motor ainda nunca funcionou — continuando (RTD pode nao estar pronto)."
                )
                time.sleep(60)
                return True
        return True

    def run(self):
        """Loop principal do watchdog."""
        # Verificar se eh fim de semana
        dia_semana = datetime.now().weekday()
        if dia_semana >= 5:
            log.info("=" * 50)
            log.info(f"Watchdog NAO inicia em fim de semana (dia={dia_semana}).")
            log.info("=" * 50)
            return

        # Verificar se o motor ja esta rodando
        if self._motor_ja_rodando():
            log.info("Motor ja esta rodando. Watchdog modo passivo (so monitora).")
            # Entrar em modo monitor (nao mata, so observa)
            self._modo_monitor()
            return

        log.info("=" * 50)
        log.info(f"Watchdog iniciado | Motor: {MOTOR_SCRIPT} | Args: {self.args}")
        log.info(f"Check: {CHECK_INTERVAL_S}s | Grace: {STARTUP_GRACE_S}s")
        log.info("=" * 50)

        # Iniciar motor
        if not self._iniciar_motor():
            # v9.35: sempre tentar de novo (nao depende de horario)
            log.warning("Falha ao iniciar motor — aguardando 60s e tentando de novo...")
            time.sleep(60)
            if not self._iniciar_motor():
                log.error("Falha persistente ao iniciar motor. Watchdog encerrando.")
                return

        tempo_inicio = time.time()
        tempo_ultimo_log = time.time()

        while self.running:
            try:
                time.sleep(CHECK_INTERVAL_S)

                tempo_desde_inicio = time.time() - tempo_inicio

                # Grace period
                if tempo_desde_inicio < STARTUP_GRACE_S:
                    if self._esta_vivo():
                        log.info(
                            f"Grace period... ({tempo_desde_inicio:.0f}s/{STARTUP_GRACE_S}s)"
                        )
                        continue
                    else:
                        log.warning("Motor morreu durante grace period")

                # Verificar se motor esta vivo
                if not self._esta_vivo():
                    if not self._pode_reiniciar():
                        break

                    log.warning("Motor morto! Reiniciando...")
                    self._matar_motor()
                    log.info(f"Aguardando {RESTART_DELAY_S}s...")
                    time.sleep(RESTART_DELAY_S)

                    if not self._iniciar_motor():
                        log.error("Falha ao reiniciar motor")
                        time.sleep(60)
                        continue

                    self.reinicios.append(datetime.now())
                    tempo_inicio = time.time()
                    continue

                # Marcar que motor ja funcionou (apos grace period)
                if not getattr(self, '_motor_ja_funcionou', False) and tempo_desde_inicio > STARTUP_GRACE_S:
                    self._motor_ja_funcionou = True
                    log.info("Motor sobreviveu ao grace period — marcado como funcional.")

                # Log periodico (5 min)
                if time.time() - tempo_ultimo_log >= 300:
                    uptime_min = (time.time() - tempo_inicio) / 60
                    log.info(
                        f"Motor vivo | PID={self.motor_proc.pid} | "
                        f"Uptime: {uptime_min:.0f}min | "
                        f"Reinicios (1h): {len(self.reinicios)}"
                    )
                    tempo_ultimo_log = time.time()
                
                # v11.18: Consolidacao periodica (a cada 1h)
                if not hasattr(self, '_ultimo_consolidar'):
                    self._ultimo_consolidar = time.time()
                if time.time() - self._ultimo_consolidar >= 3600:
                    self._consolidar_parquets()
                    self._ultimo_consolidar = time.time()

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Erro no watchdog: {e}")
                time.sleep(5)

        log.info("Watchdog encerrando...")
        self._matar_motor()
        log.info("Watchdog finalizado.")

    def _modo_monitor(self):
        """Modo passivo: so monitora o motor existente, nao mata nada."""
        log.info("Watchdog em modo monitor (nao reinicia)")
        while self.running:
            try:
                time.sleep(CHECK_INTERVAL_S)
                # So loga, nao mata
                if self.motor_proc and self.motor_proc.poll() is not None:
                    log.warning(
                        f"Motor morreu (exit={self.motor_proc.returncode}) "
                        f"mas watchdog esta em modo monitor"
                    )
            except KeyboardInterrupt:
                break
            except Exception:
                pass


# ============================================================
#  MAIN
# ============================================================
if __name__ == "__main__":
    args = sys.argv[1:]
    args = [
        a for a in args
        if not os.path.exists(a) and not a.endswith(('.py', '.json', '.bat', '.txt'))
    ]
    if not args:
        args = ["WINV26", "WDOU26"]

    lock = WatchdogLock(LOCK_FILE)
    if not lock.acquire():
        sys.exit(1)

    wd = Watchdog(args)

    def _sig(sig, frame):
        wd.running = False

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        wd.run()
    except KeyboardInterrupt:
        wd.running = False
        wd._matar_motor()
    finally:
        lock.release()
