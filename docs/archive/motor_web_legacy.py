"""
motor_web_v11.py — Coletor ProfitChart RTD (orchestrator fino).

Fluxo de dados modular:
  ProfitChart RTD --> adapters/rtd_connection --> adapters/rtd_parser --> adapters/rtd_writer --> adapters/dashboard

Responsabilidades delegadas:
  adapters/rtd_connection.py  -- COM interfaces, server, discover, connect
  adapters/rtd_parser.py      -- parse_refresh_data, parse_dat, enforce_schema
  adapters/rtd_writer.py      -- writer threads, schemas, parquet, stats
  adapters/dashboard/         -- HTTP dashboard (api, state, handlers)

Este arquivo mantem:
  - main() -- entry point com argumentos CLI
  - thread_com / _thread_com_ciclo_wd -- loop COM (multi-processo)
  - Manifesto e teste de sanidade (auditoria)
  - Re-exports para backward compat (motor_web.fnum, etc.)
"""

import os
import sys
import time
import json
import math
import hashlib
import logging
import threading
import multiprocessing
import shutil
import webbrowser
import itertools
import signal as _signal
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from queue import Empty

import pandas as pd

try:
    import comtypes
    import comtypes.client
except ImportError:
    comtypes = None

# ============================================================================
# IMPORTS DOS ADAPTERS (toda logica extraida)
# ============================================================================

from adapters.rtd_connection import (
    PROG_ID, NIVEIS_BOOK, LINHAS_TT, POLL_S, EVENT_PUMP_S, MAX_JANELAS_RTD,
    BOOK_FIELDS, TT_FIELDS, VALORES_INVALIDOS,
    _next_snapshot_id, _next_event_id, _next_capture_seq, _next_tid,
    _carregar_interfaces, _criar_callback, conectar_servidor,
    _connect, _refresh, descobrir_ativos_rtd, preparar_ativos, diagnosticar_rtd,
    fnum, fint, sstr, _normalizar_simbolo, _topico_invalido, agora_br,
)

from adapters.rtd_parser import (
    parse_refresh_data, _parse_hora_manual, parse_dat, enforce_schema, _is_iterable,
)

from adapters.rtd_writer import (
    thread_escritora, thread_escritora_tt,
    flush_buffers_with_retry, write_parquet_part,
    consolidar_book_parquet, consolidar_tt_parquet,
    limpar_pasta, _tamanho_human_readable,
    BOOK_SCHEMA, TT_SCHEMA,
    _live_inc, _live_get, _registrar_stat, _registrar_book, _registrar_tt,
    _ler_stats, _stats_dia_atual, _stats_path, _stat_chave_book, _stat_chave_tt,
    INTERVALO_SALVAMENTO_S, PARQUET_ENGINE, PARQUET_COMPRESSION, MAX_FILA,
    LIVE_FIELDS, LIVE_FIELD_INDEX,
)

from adapters.com_watchdog import (
    COMHeartbeatMonitor, watchdog_com_cycle,
)

from adapters.dashboard import DashboardAPI, DashboardState

# ============================================================================
# CONFIGURACAO
# ============================================================================

BASE_PASTA = os.environ.get('PROFIT_DATA_DIR', os.path.expanduser('~/MarketData/Profit'))
TZ_BR = ZoneInfo('America/Sao_Paulo')
ATIVOS_CONFIG = ['INDV26', 'WINV26', 'DOLU26', 'WDOU26', 'DI1F28', 'DI1F29']
AUTO_DISCOVER_ATIVOS = True
ATIVOS = []

LAZY_DECAY_EVERY = 10
LAZY_DECAY_AMOUNT = 10

LOG_FORMAT = '%(asctime)s [%(levelname)s] [%(processName)s/%(threadName)s] %(message)s'
_log_file = os.path.join(BASE_PASTA, 'motor_v23_dashboard.log')
try:
    os.makedirs(os.path.dirname(_log_file) or '.', exist_ok=True)
except Exception:
    pass

logger = logging.getLogger('Motor')
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False
_fmt = logging.Formatter(LOG_FORMAT)
_fh = logging.FileHandler(_log_file, encoding='utf-8')
_fh.setLevel(logging.INFO)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_ch = logging.StreamHandler(sys.stderr)
_ch.setLevel(logging.ERROR)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.WARNING)



def thread_com(filas_book, filas_tt, ativos, base_pasta, shutdown_event, stats_lock, live_stats):
    reconecta_delay = 1.0
    max_delay = 60.0
    primeira_vez = True
    estado = None
    # B2: watchdog thread — detecta hang COM no writer
    _ciclo_beat = [time.time()]  # mutable para thread watchdog
    _COM_HANG_TIMEOUT = 90.0   # aumentado para assinatura de milhares de topicos
    def _com_watchdog_writer():
        while not shutdown_event.is_set():
            time.sleep(10.0)
            elapsed = time.time() - _ciclo_beat[0]
            if elapsed > _COM_HANG_TIMEOUT:
                logger.error('[COM-WATCHDOG-WRITER] Hang detectado: %.1fs sem ciclo — forçando reconexão', elapsed)
                _registrar_stat(base_pasta, 'com_watchdog', stats_lock)
                shutdown_event.set()  # mata a thread_com (processo writer reinicia)
                break
    _wd = threading.Thread(target=_com_watchdog_writer, daemon=True, name='com-watchdog-writer')
    _wd.start()
    while not shutdown_event.is_set():
        _registrar_stat(base_pasta, "ciclos", stats_lock)
        sucesso = False
        try:
            sucesso, estado = _thread_com_ciclo(filas_book, filas_tt, ativos, base_pasta, shutdown_event, stats_lock, estado, live_stats)
            _ciclo_beat[0] = time.time()  # B2: atualiza beat apos cada ciclo
        except Exception as e:
            logger.error(f"Ciclo COM falhou: {e}")
            _registrar_stat(base_pasta, "erros", stats_lock)
        if shutdown_event.is_set():
            break
        if sucesso:
            reconecta_delay = 1.0
        else:
            reconecta_delay = min(reconecta_delay * 2, max_delay)
            if not primeira_vez:
                _registrar_stat(base_pasta, "reconexoes", stats_lock)
        primeira_vez = False
        logger.info(f"Reconectando em {reconecta_delay:.0f}s...")
        shutdown_event.wait(timeout=reconecta_delay)
    logger.info("Thread COM finalizada.")


def _thread_com_ciclo(filas_book, filas_tt, ativos, base_pasta, shutdown_event, stats_lock, estado=None, live_stats=None):
    comtypes.CoInitialize()
    srv = None
    topic_map = {}
    conectou = False
    n_ativos = len(ativos)

    if estado is None:
        estado = {
            "dia_replay": [None] * n_ativos,
            "ultimo_horario_s_book": [-1] * n_ativos,
            "ultimo_horario_s_tt": [-1] * n_ativos,
            "baseline_tt": [False] * n_ativos,
            "warmup_tt": [0] * n_ativos,
            "ciclo_contador_tt": [0] * n_ativos,
            "baseline_pending_tt": [False] * n_ativos,
            "ultimo_tms_tt": [None] * n_ativos,
            "book_ultimo_snap": [None] * n_ativos,
            "book_ultimo_t": [0.0] * n_ativos,
            "vistos_tt": [{} for _ in range(n_ativos)],
        }

    dia_replay = estado["dia_replay"]
    ultimo_horario_s_book = estado["ultimo_horario_s_book"]
    ultimo_horario_s_tt = estado["ultimo_horario_s_tt"]
    baseline_tt = estado["baseline_tt"]
    warmup_tt = estado["warmup_tt"]
    ciclo_contador_tt = estado["ciclo_contador_tt"]
    baseline_pending_tt = estado["baseline_pending_tt"]
    ultimo_tms_tt = estado["ultimo_tms_tt"]
    book_ultimo_snap = estado["book_ultimo_snap"]
    book_ultimo_t = estado["book_ultimo_t"]
    vistos_tt = estado["vistos_tt"]

    dia_forcado = getattr(_thread_com_ciclo, "dia_replay_forcado", None)
    if dia_forcado:
        for i in range(n_ativos):
            if dia_replay[i] is None:
                dia_replay[i] = dia_forcado
        logger.info(f"[REPLAY] Dia forcado: {dia_forcado}")

    try:
        srv, IRTDUpdateEvent = conectar_servidor()
        notify = threading.Event()
        disc = threading.Event()
        cb = _criar_callback(IRTDUpdateEvent, notify, disc)
        try:
            srv.ServerStart(cb)
            logger.info("ServerStart OK")
        except Exception as e:
            logger.error(f"ServerStart falhou: {e}")
            return False, estado
        conectou = True

        # B2: executar ciclo COM com watchdog gerenciado por adapters/com_watchdog.py
        try:
            resultado = watchdog_com_cycle(srv, _thread_com_ciclo_wd,
                                          srv, IRTDUpdateEvent, notify, disc, cb,
                                          topic_map, estado, ativos, base_pasta,
                                          shutdown_event, stats_lock, live_stats,
                                          filas_book, filas_tt)
            return resultado
        except Exception:
            return True, estado

    except Exception as e:
        logger.error(f"Conexao falhou: {e}")
        return False, estado
    finally:
        comtypes.CoUninitialize()
        logger.info("Ciclo COM encerrado.")



def _thread_com_ciclo_wd(mon, srv, IRTDUpdateEvent, notify, disc, cb,
                          topic_map, estado, ativos, base_pasta,
                          shutdown_event, stats_lock, live_stats,
                          filas_book, filas_tt):
    """Loop COM com watchdog injetado (B2, v10.1.1).

    Esta função contém apenas a lógica do loop COM.
    O watchdog (start/heartbeat/stop) é gerenciado por watchdog_com_cycle().
    """
    n_ativos = len(ativos)
    dia_replay = estado["dia_replay"]
    ultimo_horario_s_book = estado["ultimo_horario_s_book"]
    ultimo_horario_s_tt = estado["ultimo_horario_s_tt"]
    baseline_tt = estado["baseline_tt"]
    warmup_tt = estado["warmup_tt"]
    ciclo_contador_tt = estado["ciclo_contador_tt"]
    baseline_pending_tt = estado["baseline_pending_tt"]
    ultimo_tms_tt = estado["ultimo_tms_tt"]
    book_ultimo_snap = estado["book_ultimo_snap"]
    book_ultimo_t = estado["book_ultimo_t"]
    vistos_tt = estado["vistos_tt"]

    simbolos = [a["simbolo"] for a in ativos]

    def add(strings, kind, idx=-1, field="", a_idx=0):
        tid, val_inicial = _connect(srv, strings)
        topic_map[tid] = (kind, idx, field, a_idx)
        if kind == "info" and val_inicial:
            simb = sstr(val_inicial).upper()
            if simb and simb != "DESCONHECIDO" and simb != simbolos[a_idx]:
                logger.warning(
                    f"[ATIVO {a_idx}] Simbolo RTD ({simb}) diferente do configurado ({simbolos[a_idx]})"
                )

    for a_idx, ativo in enumerate(ativos):
        add([ativo["book"], "INFO", "ATV"], "info", a_idx=a_idx)
        add([ativo["tt"], "INFO", "ATV"], "info", a_idx=a_idx)
        for i in range(NIVEIS_BOOK):
            for f in BOOK_FIELDS:
                add([ativo["book"], f, str(i)], "book", i, f, a_idx)
        if ativo["tt"] is not None:
            for i in range(LINHAS_TT):
                for f in TT_FIELDS:
                    add([ativo["tt"], f, str(i)], "tt", i, f, a_idx)
    logger.info(f"Conectado: {len(topic_map)} topicos para {n_ativos} ativo(s): {simbolos}")

    book_cur = [[dict() for _ in range(NIVEIS_BOOK)] for _ in ativos]
    tt_cur = [[dict() for _ in range(LINHAS_TT)] for _ in ativos]

    # Captura dirigida por evento: o UpdateNotify acorda o loop imediatamente.
    # Um watchdog periodico mantém o fluxo vivo mesmo se o COM perder uma notificacao.
    proximo_watchdog = time.perf_counter() + POLL_S
    while not shutdown_event.is_set() and not disc.is_set() and not mon.stuck_event.is_set():
        agora_loop = time.perf_counter()
        timeout = max(0.0, min(proximo_watchdog - agora_loop, EVENT_PUMP_S))
        comtypes.client.PumpEvents(timeout)

        refresh_due = notify.is_set() or time.perf_counter() >= proximo_watchdog
        if not refresh_due:
            continue

        notify.clear()
        proximo_watchdog = time.perf_counter() + POLL_S

        try:
            data = _refresh(srv)
        except Exception as e:
            logger.debug(f"RefreshData excecao: {e}")
            continue
        mon.heartbeat()
        if data is None:
            continue

        pairs = parse_refresh_data(data)
        if not pairs:
            continue

        mudou_book = [False] * n_ativos
        tt_sujas = [set() for _ in ativos]

        for tid, val in pairs:
            info = topic_map.get(tid)
            if info is None:
                continue
            kind, idx, field, a_idx = info
            if kind == "info":
                if sstr(val):
                    simb = sstr(val).upper()
                    if simb != simbolos[a_idx]:
                        logger.warning(f"[ATIVO {a_idx}] Simbolo mudou: {simbolos[a_idx]} -> {simb}")
                        simbolos[a_idx] = simb
            elif kind == "book":
                if book_cur[a_idx][idx].get(field) != val:
                    book_cur[a_idx][idx][field] = val
                    mudou_book[a_idx] = True
            else:
                tt_cur[a_idx][idx][field] = val
                tt_sujas[a_idx].add(idx)

        # ---- snapshot book ----
        agora = time.perf_counter()
        for a_idx in range(n_ativos):
            tempo_sem_snap = agora - book_ultimo_t[a_idx]
            is_keepalive = (not mudou_book[a_idx]) and tempo_sem_snap >= 30.0
            if not mudou_book[a_idx] and not is_keepalive:
                continue
            if mudou_book[a_idx] and tempo_sem_snap < 0.25:
                continue
            bc = book_cur[a_idx]
            niveis_convertidos = [
                (fnum(l.get("OCP")), int(fnum(l.get("VOC"))), sstr(l.get("ACP")),
                 fnum(l.get("OVD")), int(fnum(l.get("VOV"))), sstr(l.get("AVD")))
                for l in bc
            ]
            chave = tuple(niveis_convertidos)
            if (chave != book_ultimo_snap[a_idx] and any(c[0] > 0 or c[3] > 0 for c in niveis_convertidos)) or is_keepalive:
                book_ultimo_snap[a_idx] = chave
                book_ultimo_t[a_idx] = agora

                horario_book = bc[0].get("HORC") or bc[0].get("HORV")
                dt_book, inferida = parse_dat(horario_book, agora_br())

                if dt_book is not None:
                    horario_s = dt_book.hour * 3600 + dt_book.minute * 60 + dt_book.second
                    if dia_replay[a_idx] is None:
                        dia_replay[a_idx] = dt_book.date()
                        logger.info(f"[REPLAY] Dia inicial (BOOK) ativo {a_idx}: {dia_replay[a_idx]}")
                    # DETECTA VIRADA
                    if (ultimo_horario_s_book[a_idx] >= 0 and
                        horario_s < ultimo_horario_s_book[a_idx] - 6 * 3600 and
                        ultimo_horario_s_book[a_idx] > 20 * 3600 and
                        horario_s < 4 * 3600):
                        dia_replay[a_idx] += timedelta(days=1)
                        logger.info(
                            f"[REPLAY] VIRADA MEIA-NOITE (BOOK) ativo {a_idx}! "
                            f"{ultimo_horario_s_book[a_idx] // 3600:02d}h->"
                            f"{horario_s // 3600:02d}h | Novo dia: {dia_replay[a_idx]}"
                        )
                    if inferida:
                        dt_book = datetime.combine(dia_replay[a_idx], dt_book.time())
                    else:
                        if dia_replay[a_idx] != dt_book.date():
                            logger.warning(
                                f"[ATIVO {a_idx}] Data RTD BOOK ({dt_book.date()}) diverge do "
                                f"dia_replay ({dia_replay[a_idx]}). Adotando RTD."
                            )
                            dia_replay[a_idx] = dt_book.date()
                    ultimo_horario_s_book[a_idx] = horario_s
                    tms = int(dt_book.timestamp() * 1000)
                elif ultimo_tms_tt[a_idx] is not None:
                    tms = ultimo_tms_tt[a_idx]
                else:
                    tms = time.time_ns() // 1_000_000

                row = {
                    "capture_sequence": next(_next_capture_seq),
                    "snapshot_id": next(_next_snapshot_id),
                    "time_ms": tms,
                    "timestamp_recebimento_python": time.time_ns() // 1_000_000,
                    "simbolo": simbolos[a_idx],
                    "keepalive": is_keepalive,
                }
                for nlv in range(1, NIVEIS_BOOK + 1):
                    bid_p, bid_v, bid_ag, ask_p, ask_v, ask_ag = niveis_convertidos[nlv - 1]
                    row[f"bid_p{nlv}"] = bid_p
                    row[f"bid_v{nlv}"] = bid_v
                    row[f"bid_agente{nlv}"] = bid_ag
                    row[f"ask_p{nlv}"] = ask_p
                    row[f"ask_v{nlv}"] = ask_v
                    row[f"ask_agente{nlv}"] = ask_ag
                # Sem descarte deliberado: a fila e de capacidade efetivamente ilimitada.
                # Se o produtor for mais rapido que o escritor, acumulamos backlog e auditamos.
                try:
                    filas_book[a_idx].put([row])
                    _registrar_book(base_pasta, simbolos[a_idx], "capturados", stats_lock)
                    _live_inc(live_stats, a_idx, "book_capturados", 1)
                    _registrar_book(base_pasta, simbolos[a_idx], "enfileirados", stats_lock)
                except Exception as e:
                    _registrar_stat(base_pasta, "queue_errors_book", stats_lock)
                    _registrar_book(base_pasta, simbolos[a_idx], "drops", stats_lock)
                    _live_inc(live_stats, a_idx, "drops", 1)
                    logger.error(f"[QUEUE-BOOK] falha ao enfileirar snapshot {row['snapshot_id']}: {e}")
                    raise

        # ---- T&T ----
        for a_idx in range(n_ativos):
            if ativos[a_idx]["tt"] is None:
                continue
            if not tt_sujas[a_idx]:
                continue

            # Warmup pos-reconexao: deixa o retrato estabilizar sem gerar eventos derivados.
            # Nao semeia vistos_tt; ao final, o retrato atual e simplesmente ignorado como baseline.
            if warmup_tt[a_idx] < 60:
                warmup_tt[a_idx] += 1
                if warmup_tt[a_idx] == 60:
                    vistos_tt[a_idx].clear()
                    baseline_tt[a_idx] = False
                    baseline_pending_tt[a_idx] = True
                    logger.info(f"[WARMUP] T&T[{a_idx}] ({simbolos[a_idx]}) concluido; primeiro retrato sera absorvido como baseline sem gerar evento.")
                continue

            ciclo_contador_tt[a_idx] += 1

            # 1. Conta a frequencia de cada assinatura no retrato ATUAL da tabela
            current_counts = {}
            example_r = {}
            for r in tt_cur[a_idx]:
                # v10.4: Acesso direto via get() sem overhead de conversão se o valor não mudar
                pre_raw = r.get("PRE")
                if not pre_raw or pre_raw == 0:
                    continue
                
                # Assinatura otimizada: evita chamadas de sstr() se já forem strings
                sig = (r.get("DAT"), r.get("ACP"), pre_raw,
                       r.get("QUL"), r.get("AVD"),
                       r.get("AGR"), r.get("AGAG"))
                
                current_counts[sig] = current_counts.get(sig, 0) + 1
                example_r[sig] = r

            # Primeiro retrato pos-warmup: absorve a FIFO atual como baseline.
            # Isso evita fabricar 1000 negocios antigos como se fossem novos.
            if baseline_pending_tt[a_idx]:
                vistos_tt[a_idx] = dict(current_counts)
                baseline_pending_tt[a_idx] = False
                baseline_tt[a_idx] = bool(current_counts)
                logger.info(
                    f"[BASELINE] T&T[{a_idx}] ({simbolos[a_idx]}) absorvido: "
                    f"{sum(current_counts.values())} linhas / {len(current_counts)} assinaturas."
                )
                continue

            novas = []
            # 2. Compara com o historico e extrai apenas os excedentes (microlotes novos)
            for sig, count in current_counts.items():
                seen = vistos_tt[a_idx].get(sig, 0)
                
                if count > seen:
                    diff = count - seen
                    vistos_tt[a_idx][sig] = count  # Atualiza o teto historico


                    r = example_r[sig]
                    dt_str = r.get("DAT")
                    dt, inferida = parse_dat(dt_str, agora_br())

                    if dia_replay[a_idx] is None and dt is not None and not inferida:
                        dia_replay[a_idx] = dt.date()
                        logger.info(f"[REPLAY] Dia detectado via T&T ativo {a_idx}: {dia_replay[a_idx]}")

                    if dt is not None and dia_replay[a_idx] is not None:
                        horario_s = dt.hour * 3600 + dt.minute * 60 + dt.second
                        if (ultimo_horario_s_tt[a_idx] >= 0 and
                            horario_s < ultimo_horario_s_tt[a_idx] - 6 * 3600 and
                            ultimo_horario_s_tt[a_idx] > 20 * 3600 and
                            horario_s < 4 * 3600):
                            dia_replay[a_idx] += timedelta(days=1)
                            logger.info(
                                f"[REPLAY] VIRADA MEIA-NOITE (T&T) ativo {a_idx}! "
                                f"{ultimo_horario_s_tt[a_idx] // 3600:02d}h->"
                                f"{horario_s // 3600:02d}h | Novo dia: {dia_replay[a_idx]}"
                            )
                        if inferida:
                            dt = datetime.combine(dia_replay[a_idx], dt.time())
                        else:
                            if dia_replay[a_idx] != dt.date():
                                logger.warning(
                                    f"[ATIVO {a_idx}] Data RTD T&T ({dt.date()}) diverge do "
                                    f"dia_replay ({dia_replay[a_idx]}). Adotando RTD."
                                )
                                dia_replay[a_idx] = dt.date()
                        ultimo_horario_s_tt[a_idx] = horario_s
                    elif dt is not None:
                        dia_replay[a_idx] = dt.date()

                    if dt is None:
                        continue

                    dtb = dt.replace(tzinfo=TZ_BR) if dt.tzinfo is None else dt
                    agr = sstr(r.get("AGR")).lower()
                    direcao = 1 if agr.startswith("comp") else (-1 if agr.startswith("vend") else 0)

                    is_seed = False
                    if not baseline_tt[a_idx]:
                        baseline_tt[a_idx] = True
                        is_seed = True
                        logger.info(f"Baseline T&T[{a_idx}] semeado no 1o negocio (pre={sig[2]}).")

                    # 3. Adiciona a diferenca (clona os microlotes exatos)
                    for _ in range(diff):
                        novas.append({
                            "time_ms": int(dtb.timestamp() * 1000),
                            "timestamp_recebimento_python": time.time_ns() // 1_000_000,
                            "timestamp_brt": dtb,
                            "simbolo": simbolos[a_idx],
                            "origem": "baseline_seed" if is_seed else "profit_rtd",
                            "compradora": sstr(r.get("ACP")),
                            "preco": sig[2],
                            "quantidade": sig[3],
                            "vendedora": sstr(r.get("AVD")),
                            "agressor": sstr(r.get("AGR")),
                            "agente_agressor": sstr(r.get("AGAG")),
                            "direcao": direcao,
                        })


            # 4. DECAIMENTO LAZY: mesma taxa de envelhecimento, menor custo por ciclo.
            if ciclo_contador_tt[a_idx] % LAZY_DECAY_EVERY == 0:
                ausentes = [sig for sig in vistos_tt[a_idx] if sig not in current_counts]
                for sig in ausentes:
                    vistos_tt[a_idx][sig] = max(0, vistos_tt[a_idx][sig] - LAZY_DECAY_AMOUNT)
                    if vistos_tt[a_idx][sig] == 0:
                        del vistos_tt[a_idx][sig]

            # Purge de seguranca (v10.3: evitamos sorted() em dicts gigantes)
            if len(vistos_tt[a_idx]) > 50_000:
                # Limpeza rápida: removemos itens com contagem zero ou resetamos parte do dict
                keys = list(vistos_tt[a_idx].keys())
                for k in keys[:15_000]: # Remove os 15k mais antigos pela ordem de inserção
                    del vistos_tt[a_idx][k]
                logger.info(f"Purge rápido vistos_tt[{a_idx}]: {len(vistos_tt[a_idx])} ativos")
            if novas:
                n_detectados = len(novas)
                _registrar_tt(base_pasta, simbolos[a_idx], "detectados", stats_lock, n=n_detectados)
                _live_inc(live_stats, a_idx, "tt_detectados", n_detectados)
                novas.sort(key=lambda r: r["time_ms"])
                for n in novas:
                    n["event_id"] = next(_next_event_id)
                    n["capture_sequence"] = next(_next_capture_seq)
                ultimo_tms_tt[a_idx] = novas[-1]["time_ms"]
                # Sem descarte deliberado: filas_tt usa capacidade ilimitada.
                # Qualquer erro de IPC encerra o ciclo para que o supervisor reconecte.
                try:
                    filas_tt[a_idx].put(novas)
                    _registrar_tt(base_pasta, simbolos[a_idx], "enfileirados", stats_lock, n=len(novas))
                    _live_inc(live_stats, a_idx, "tt_detectados", 0)
                except Exception as e:
                    _registrar_stat(base_pasta, "queue_errors_tt", stats_lock, n=len(novas))
                    _registrar_tt(base_pasta, simbolos[a_idx], "drops", stats_lock, n=len(novas))
                    _live_inc(live_stats, a_idx, "drops", len(novas))
                    logger.error(f"[QUEUE-TT] falha ao enfileirar {len(novas)} negocios: {e}")
                    raise

    return conectou, estado


# ============================================================================
# THREAD ESCRITORA (BOOK)
# ============================================================================


# ============================================================================
# DASHBOARD WEB
# ============================================================================

def _start_web_dashboard(filas_book, filas_tt, live_stats, base_pasta, port=5000, open_browser=True):
    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True
    server = _Server(('127.0.0.1', port), DashboardAPI)
    server.state = DashboardState(filas_book, filas_tt, live_stats, base_pasta, ATIVOS)
    threading.Thread(target=server.serve_forever, name='WebDashboard', daemon=True).start()
    url = f'http://127.0.0.1:{port}/'
    logger.info(f'[WEB] Dashboard: {url}')
    if open_browser:
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            logger.warning(f'[WEB] navegador: {e}')
    return server


def gerar_manifesto(base_pasta, dia_str):
    import pyarrow.parquet as pq

    dt = datetime.strptime(dia_str, "%Y%m%d").date()
    pasta_dia = os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}"
    )
    if not os.path.isdir(pasta_dia):
        print(f"[MANIFESTO] Pasta nao existe: {pasta_dia}")
        return None

    arquivos = []
    for root, _, files in os.walk(pasta_dia):
        for fn in files:
            if fn.endswith(".parquet") or fn.endswith(".csv"):
                arquivos.append(os.path.join(root, fn))
    arquivos.sort()

    entradas = []
    agregados = {}
    for caminho in arquivos:
        rel = os.path.relpath(caminho, pasta_dia)
        try:
            sym = next(p.split("=", 1)[1] for p in rel.split(os.sep) if p.startswith("sym="))
            tipo = next(p.split("=", 1)[1] for p in rel.split(os.sep) if p.startswith("tipo="))
        except StopIteration:
            sym, tipo = "DESCONHECIDO", "DESCONHECIDO"

        n_regs = None
        min_ms = max_ms = None
        if caminho.endswith(".csv"):
            try:
                df_tmp = pd.read_csv(caminho, usecols=["time_ms"], on_bad_lines="warn")
                n_regs = len(df_tmp)
                if n_regs > 0:
                    min_ms = int(df_tmp["time_ms"].min())
                    max_ms = int(df_tmp["time_ms"].max())
            except Exception as e:
                logger.error(f"[MANIFESTO] Falha ao ler CSV {caminho}: {e}")
                n_regs, min_ms, max_ms = None, None, None
        else:
            try:
                pf = pq.ParquetFile(caminho)
                n_regs = pf.metadata.num_rows
                min_ms = max_ms = None
                if "time_ms" in pf.schema_arrow.names:
                    col = pf.read(columns=["time_ms"]).column("time_ms")
                    if len(col) > 0:
                        import pyarrow.compute as pc
                        min_ms = int(pc.min(col).as_py())
                        max_ms = int(pc.max(col).as_py())
            except Exception as e:
                logger.error(f"[MANIFESTO] Falha ao ler {caminho}: {e}")
                n_regs, min_ms, max_ms = None, None, None

        entrada = {
            "arquivo": rel.replace("\\", "/"),
            "simbolo": sym,
            "tipo": tipo,
            "tamanho_bytes": os.path.getsize(caminho),
            "registros": n_regs,
            "time_ms_min": min_ms,
            "time_ms_max": max_ms,
            "sha256": _sha256_arquivo(caminho),
        }
        entradas.append(entrada)

        chave = (sym, tipo)
        ag = agregados.setdefault(chave, {"registros": 0, "arquivos": 0, "min_ms": None, "max_ms": None})
        ag["arquivos"] += 1
        ag["registros"] += n_regs or 0
        if min_ms is not None:
            ag["min_ms"] = min_ms if ag["min_ms"] is None else min(ag["min_ms"], min_ms)
        if max_ms is not None:
            ag["max_ms"] = max_ms if ag["max_ms"] is None else max(ag["max_ms"], max_ms)

    manifesto = {
        "dia": dia_str,
        "gerado_em": agora_br().isoformat(),
        "pasta": pasta_dia.replace("\\", "/"),
        "total_arquivos": len(entradas),
        "total_registros": sum(e["registros"] or 0 for e in entradas),
        "agregados_por_simbolo_tipo": {
            f"{sym}/{tipo}": v for (sym, tipo), v in sorted(agregados.items())
        },
        "stats_execucao": _ler_stats(base_pasta, dt),
        "arquivos": entradas,
    }

    caminho_manifesto = os.path.join(pasta_dia, "manifesto.json")
    tmp = f"{caminho_manifesto}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifesto, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, caminho_manifesto)

    print(f"[MANIFESTO] {len(entradas)} arquivos, {manifesto['total_registros']} registros -> {caminho_manifesto}")
    return manifesto


# ============================================================================
# TESTE DE SANIDADE
# ============================================================================

def teste_sanidade(base_pasta, dia_str, ativos):
    dt = datetime.strptime(dia_str, "%Y%m%d").date()
    pasta_dia = os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}"
    )
    print("=" * 70)
    print(f"TESTE DE SANIDADE — {dia_str}")
    print("=" * 70)

    resultados = []

    def check(nome, ok, detalhe=""):
        resultados.append(ok)
        marca = "OK" if ok else "FALHA"
        print(f"  [{marca}] {nome}" + (f" — {detalhe}" if detalhe else ""))

    if not os.path.isdir(pasta_dia):
        check("pasta do dia existe", False, pasta_dia)
        print("=" * 70)
        return False

    stats = _ler_stats(base_pasta, dt)
    detalhe_stats = (
        f"erros={stats.get('erros', 0)} reconexoes={stats.get('reconexoes', 0)} "
        f"drops_book={stats.get('drops_book', 0)} drops_tt={stats.get('drops_tt', 0)}"
        if stats else "sem _stats_captura.json"
    )
    check("iniciou corretamente (stats de execucao presentes)", bool(stats), detalhe_stats)
    for ativo in ativos:
        sym = ativo["simbolo"]
        det = stats.get(_stat_chave_tt(sym, "detectados"), 0)
        enq = stats.get(_stat_chave_tt(sym, "enfileirados"), 0)
        grav = stats.get(_stat_chave_tt(sym, "gravados"), 0)
        drops = stats.get(_stat_chave_tt(sym, "drops"), 0)
        taxa = (grav / det * 100.0) if det else 100.0
        check(
            f"integridade T&T {sym}",
            det == enq and drops == 0 and grav == enq,
            f"detectados={det:,} enfileirados={enq:,} gravados={grav:,} drops={drops:,} integridade={taxa:.6f}%"
        )

    manifesto = gerar_manifesto(base_pasta, dia_str)
    if manifesto is None:
        check("manifesto gerado", False)
        print("=" * 70)
        return False
    check("manifesto/hash gerado", True, f"{manifesto['total_arquivos']} arquivos")

    tmp_sobrando = [
        os.path.join(r, fn) for r, _, fs in os.walk(pasta_dia) for fn in fs if ".tmp." in fn
    ]
    check("arquivos fechados (sem .tmp. sobrando)", not tmp_sobrando, f"{len(tmp_sobrando)} tmp encontrados")

    simbolos_esperados = {a["simbolo"] for a in ativos}
    simbolos_presentes = {e["simbolo"] for e in manifesto["arquivos"]}
    faltando = simbolos_esperados - simbolos_presentes
    check("todos os ativos presentes", not faltando, f"faltando: {sorted(faltando)}" if faltando else "")

    for sym in sorted(simbolos_esperados):
        n_book = manifesto["agregados_por_simbolo_tipo"].get(f"{sym}/BOOK", {}).get("registros", 0)
        n_tt = manifesto["agregados_por_simbolo_tipo"].get(f"{sym}/TT", {}).get("registros", 0)
        check(f"{sym}: book com snapshots", n_book > 0, f"{n_book} registros")
        check(f"{sym}: T&T recebendo registros", n_tt > 0, f"{n_tt} registros")

    todos_min = [e["time_ms_min"] for e in manifesto["arquivos"] if e["time_ms_min"] is not None]
    todos_max = [e["time_ms_max"] for e in manifesto["arquivos"] if e["time_ms_max"] is not None]
    check("timestamps presentes e coerentes (max > min)", bool(todos_min) and max(todos_max) > min(todos_min))

    check("quantidade de registros contabilizada", manifesto["total_registros"] > 0, str(manifesto["total_registros"]))

    ok_geral = all(resultados)
    print("=" * 70)
    print(f"RESULTADO: {'CONFIAVEL' if ok_geral else 'REVISAR ANTES DE USAR'} "
          f"({sum(resultados)}/{len(resultados)} checks OK)")
    print("=" * 70)
    return ok_geral


# ============================================================================
# DUCKDB
# ============================================================================

def _duckdb_shell(base_pasta):
    try:
        import duckdb
    except ImportError:
        print("DuckDB nao instalado. Rode: pip install duckdb --break-system-packages")
        return

    raw = os.path.join(base_pasta, "RAW").replace("\\", "/")
    con = duckdb.connect(database=":memory:")
    con.execute(f"""
        CREATE VIEW RAW_BOOK AS
        SELECT * FROM read_parquet(
            '{raw}/ano=*/mes=*/dia=*/sym=*/tipo=BOOK/*.parquet',
            hive_partitioning = 1,
            union_by_name = true,
            filename = true
        )
    """)
    con.execute(f"""
        CREATE VIEW RAW_TT AS
        SELECT * FROM read_parquet(
            '{raw}/ano=*/mes=*/dia=*/sym=*/tipo=TT/*.parquet',
            hive_partitioning = 1,
            union_by_name = true,
            filename = true
        )
    """)
    print(f"[DUCKDB] Views prontas sobre: {raw}")
    print("  RAW_BOOK, RAW_TT  (filtre por 'sym' e/ou 'mes'/'dia' para pruning real de particao;")
    print("  use time_ms para o filtro fino dentro da hora)")
    print("  Exemplo: SELECT * FROM RAW_BOOK WHERE sym='INDQ26' AND mes='08' "
          "AND dia='10' AND time_ms BETWEEN ? AND ? ORDER BY time_ms;")
    print("  Digite SQL (ou 'exit' para sair):\n")
    while True:
        try:
            q = input("duckdb> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        try:
            print(con.execute(q).df())
        except Exception as e:
            print(f"ERRO: {e}")


# ============================================================================
# CSV TO PARQUET
# ============================================================================

def _duckdb_shell(base_pasta):
    try:
        import duckdb
    except ImportError:
        print("DuckDB nao instalado. Rode: pip install duckdb --break-system-packages")
        return

    raw = os.path.join(base_pasta, "RAW").replace("\\", "/")
    con = duckdb.connect(database=":memory:")
    con.execute(f"""
        CREATE VIEW RAW_BOOK AS
        SELECT * FROM read_parquet(
            '{raw}/ano=*/mes=*/dia=*/sym=*/tipo=BOOK/*.parquet',
            hive_partitioning = 1,
            union_by_name = true,
            filename = true
        )
    """)
    con.execute(f"""
        CREATE VIEW RAW_TT AS
        SELECT * FROM read_parquet(
            '{raw}/ano=*/mes=*/dia=*/sym=*/tipo=TT/*.parquet',
            hive_partitioning = 1,
            union_by_name = true,
            filename = true
        )
    """)
    print(f"[DUCKDB] Views prontas sobre: {raw}")
    print("  RAW_BOOK, RAW_TT  (filtre por 'sym' e/ou 'mes'/'dia' para pruning real de particao;")
    print("  use time_ms para o filtro fino dentro da hora)")
    print("  Exemplo: SELECT * FROM RAW_BOOK WHERE sym='INDQ26' AND mes='08' "
          "AND dia='10' AND time_ms BETWEEN ? AND ? ORDER BY time_ms;")
    print("  Digite SQL (ou 'exit' para sair):\n")
    while True:
        try:
            q = input("duckdb> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit"):
            break
        try:
            print(con.execute(q).df())
        except Exception as e:
            print(f"ERRO: {e}")


# ============================================================================
# CSV TO PARQUET
# ============================================================================

def csv_to_parquet(base_pasta, dia_str=None):
    import glob

    if dia_str is None:
        dia_forcado = getattr(_thread_com_ciclo, "dia_replay_forcado", None)
        if dia_forcado:
            dia_str = dia_forcado.strftime("%Y%m%d")
        else:
            dia_str = agora_br().strftime("%Y%m%d")

    dt = datetime.strptime(dia_str, "%Y%m%d")
    pasta_dia = os.path.join(
        base_pasta, "RAW",
        f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}"
    )

    padrao = os.path.join(pasta_dia, "sym=*", "tipo=TT", "*.csv")
    arquivos = sorted(glob.glob(padrao))

    if not arquivos:
        print(f"[CSV->PARQUET] Nenhum CSV encontrado para {dia_str}")
        return

    print(f"[CSV->PARQUET] {len(arquivos)} CSVs encontrados para {dia_str}. Convertendo...")
    total_regs = 0

    for arq in arquivos:
        try:
            df = pd.read_csv(arq, on_bad_lines="warn")
            if df.empty:
                os.remove(arq)
                continue
            df = enforce_schema(df, TT_SCHEMA)
            parquet_path = arq.replace(".csv", ".parquet")
            df.to_parquet(parquet_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
            os.remove(arq)
            total_regs += len(df)
            print(f"  [OK] {os.path.basename(arq)} -> {os.path.basename(parquet_path)} ({len(df):,} regs)")
        except Exception as e:
            print(f"  [ERRO] {os.path.basename(arq)}: {e}")

    print(f"[CSV->PARQUET] Concluido: {total_regs:,} registros convertidos para {dia_str}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    global ATIVOS
    args = sys.argv[1:]
    do_limpar = "--limpar" in args
    do_backup = "--backup" in args
    do_dryrun = "--dry-run" in args
    do_tudo = "--tudo" in args
    dia_replay = None
    base_pasta = BASE_PASTA
    manifesto_dia = None
    sanidade_dia = None
    web_port = 5000
    web_open = True

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dia-replay" and i + 1 < len(args):
            dia_replay = args[i + 1]
            i += 2
        elif a == "--pasta-saida" and i + 1 < len(args):
            base_pasta = args[i + 1]
            i += 2
        elif a == "--manifesto" and i + 1 < len(args):
            manifesto_dia = args[i + 1]
            i += 2
        elif a == "--sanidade" and i + 1 < len(args):
            sanidade_dia = args[i + 1]
            i += 2
        elif a == "--web-port" and i + 1 < len(args):
            web_port = int(args[i + 1])
            i += 2
        elif a == "--no-browser":
            web_open = False
            i += 1
        else:
            i += 1

    if "--diag" in sys.argv:
        preparar_ativos(ATIVOS_CONFIG, AUTO_DISCOVER_ATIVOS)
        diagnosticar_rtd(ATIVOS)
        return

    if "--duckdb" in sys.argv:
        _duckdb_shell(base_pasta)
        return

    if manifesto_dia:
        gerar_manifesto(base_pasta, manifesto_dia)
        return

    if sanidade_dia:
        preparar_ativos(ATIVOS_CONFIG, AUTO_DISCOVER_ATIVOS)
        teste_sanidade(base_pasta, sanidade_dia, ATIVOS)
        return

    if "--csv-to-parquet" in sys.argv:
        csv_to_parquet(base_pasta, dia_replay if dia_replay else None)
        return

    if "--consolidar-book" in sys.argv:
        dia_str = dia_replay if dia_replay else agora_br().strftime("%Y%m%d")
        consolidar_book_parquet(base_pasta, dia_str)
        return

    if do_limpar:
        limpar_pasta(base_pasta, dry_run=do_dryrun, backup=do_backup, tudo=do_tudo)
        if do_dryrun:
            return

    if dia_replay:
        try:
            dt = datetime.strptime(dia_replay, "%Y%m%d").date()
            _thread_com_ciclo.dia_replay_forcado = dt
            logger.info(f"Dia do replay forcado: {dt}")
        except ValueError:
            print(f"ERRO: --dia-replay deve ser AAAAMMDD (ex: 20260807)")
            sys.exit(1)

    import signal as _signal

    shutdown_event = multiprocessing.Event()
    stats_lock = multiprocessing.Lock()

    def _sig(sig, frame):
        logger.info("Shutdown solicitado (Ctrl+C)...")
        shutdown_event.set()
    try:
        _signal.signal(_signal.SIGINT, _sig)
        _signal.signal(_signal.SIGTERM, _sig)
    except Exception:
        pass

    ATIVOS = preparar_ativos(ATIVOS_CONFIG, AUTO_DISCOVER_ATIVOS)
    if not ATIVOS:
        logger.error("Nenhum ativo RTD valido encontrado. Encerrando.")
        return

    print("=" * 70)
    print("motor_web_v11.py — ProfitChart RTD + Dashboard Web (modular)")
    print(f"Ativos: {[a['simbolo'] for a in ATIVOS]} ({len(ATIVOS)} ativos) | Pasta: {base_pasta}")
    print(f"Dashboard: http://127.0.0.1:{web_port} | BOOK=Parquet/hora | TT=Parquet/hora")
    print("=" * 70)

    live_stats = multiprocessing.Array("q", len(ATIVOS) * len(LIVE_FIELDS), lock=True)

    filas_book = [multiprocessing.Queue(maxsize=500_000) for _ in ATIVOS]  # B4: limite OOM
    filas_tt = [multiprocessing.Queue(maxsize=500_000) for _ in ATIVOS]

    workers_book = [
        multiprocessing.Process(
            target=thread_escritora,
            args=(filas_book[i], f"EscritoraBook-{ativo['simbolo']}", "book", BOOK_SCHEMA,
                  base_pasta, shutdown_event, stats_lock, i, live_stats),
            name=f"EscritoraBook-{ativo['simbolo']}",
            daemon=False,
        )
        for i, ativo in enumerate(ATIVOS)
    ]
    workers_tt = [
        multiprocessing.Process(
            target=thread_escritora_tt,
            args=(filas_tt[i], f"EscritoraTT-{ativo['simbolo']}", ativo["simbolo"], TT_SCHEMA,
                  base_pasta, shutdown_event, stats_lock, i, live_stats),
            name=f"EscritoraTT-{ativo['simbolo']}",
            daemon=False,
        )
        for i, ativo in enumerate(ATIVOS)
    ]
    t_com = threading.Thread(
        target=thread_com, args=(filas_book, filas_tt, ATIVOS, base_pasta, shutdown_event, stats_lock, live_stats),
        name="COM_Multi",
    )
    web_server = _start_web_dashboard(filas_book, filas_tt, live_stats, base_pasta, port=web_port, open_browser=web_open)

    for w in workers_book:
        w.start()
    for w in workers_tt:
        w.start()
    t_com.start()

    try:
        while t_com.is_alive():
            t_com.join(timeout=1.0)
    except KeyboardInterrupt:
        pass

    logger.info("Aguardando drain das escritoras...")
    shutdown_event.set()
    for w in workers_book:
        w.join()
    for w in workers_tt:
        w.join()
    logger.info("Motor encerrado.")
    try:
        web_server.shutdown()
        web_server.server_close()
    except Exception:
        pass

    dia_audit = dia_replay or agora_br().strftime("%Y%m%d")
    # CSV->Parquet removido: escritoras agora salvam Parquet diretamente.
    try:
        logger.info(f"[AUDITORIA] Gerando manifesto para {dia_audit}...")
        gerar_manifesto(base_pasta, dia_audit)
    except Exception as e:
        logger.error(f"[AUDITORIA] Falha ao gerar manifesto: {e}")
    try:
        logger.info(f"[AUDITORIA] Rodando teste de sanidade para {dia_audit}...")
        teste_sanidade(base_pasta, dia_audit, ATIVOS)
    except Exception as e:
        logger.error(f"[AUDITORIA] Falha no teste de sanidade: {e}")


if __name__ == "__main__":
    main()
