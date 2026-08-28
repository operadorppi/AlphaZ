"""
motor_v23_auto.py v23-auto-rtd — Coletor ProfitChart RTD (multi-ativo, single-instance COM, multi-processo por ativo)

CORRECOES v22z-decay-fast:
  - FIX: dia_replay agora eh POR ATIVO (lista), nao variavel unica compartilhada.
    Virada de meia-noite no WIN nao mais corrompe timestamps do DOL/WDO/IND.
  - FIX: Estado (dia_replay, ultimo_horario, baseline, snaps) persiste entre
    reconexoes COM. Se a conexao cair as 00:30, o estado de "ja virei o dia"
    nao se perde.
  - FIX: parse_dat retorna flag 'inferida'. datetime.combine() so eh usado
    quando o RTD devolve SOMENTE hora (sem data). Quando vem data completa
    (DD/MM/AAAA ou AAAA-MM-DD), usa a data do RTD diretamente.
  - FIX: Dedup T&T reescrito com contagem de frequencia no retrato + DECAIMENTO
    do teto historico. Cada ciclo, assinaturas ausentes do retrato atual tem
    seu teto decrementado em 1. Isso resolve tanto a duplicacao massiva (v22y)
    quanto a perda de negocios legítimos que reaparecem apos sair da FIFO
    (v22z). A tabela FIFO do ProfitChart tem ~3s de residencia media; o
    decaimento garante que negocios idênticos reaparecendo apos 3s sejam
    capturados corretamente.
  - FIX: main() --csv-to-parquet e --consolidar-book nao mais chamam
    .strftime() em string (AttributeError).
  - FIX: Processos escritores ignoram SIGINT/SIGTERM (Windows), evitando
    morte no Ctrl+C com buffer nao salvo. O shutdown eh coordenado pelo
    shutdown_event do processo pai.
  - FIX: LINHAS_TT expandido de 500 para 1000 para absorver picos de volatilidade.
  - FIX: POLL_S reduzido de 0.05s (20Hz) para 0.02s (50Hz). Negocios que entram e saem
    da tabela T&T em menos de 50ms eram perdidos entre ciclos de RefreshData.
  - NEW: contadores de integridade T&T por ativo: detectados -> enfileirados -> gravados,
    com drops/falhas de escrita e percentual acumulado de integridade em _stats_captura.json.

USO:
  python motor.py --dia-replay 20260808

CONFIGURACAO:
  Edite ATIVOS abaixo com os simbolos e tabelas RTD do ProfitChart.
"""

import os
import sys
import time
import math
import json
import hashlib
import logging
import threading
import itertools
import importlib
import shutil
import multiprocessing
import ctypes
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from itertools import islice
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from queue import Empty, Full
from ctypes import c_int, c_long, byref

import comtypes
import comtypes.client
import pandas as pd

try:
    import pyarrow  # noqa: F401
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False


# ============================================================================
# CONFIGURACAO MULTI-ATIVO
# ============================================================================
ATIVOS_CONFIG = ["INDV26", "WINV26", "DOLU26", "WDOU26", "DI1F28", "DI1F29"]
AUTO_DISCOVER_ATIVOS = True
MAX_JANELAS_RTD = 12
ATIVOS = []  # preenchido na inicializacao


PROG_ID = "RTDTrading.RTDServer"
NIVEIS_BOOK = 500
LINHAS_TT = 1000  # Expandido para maior folga de buffer em alta volatilidade
POLL_S = 0.02  # watchdog period; RefreshData e priorizado por UpdateNotify
EVENT_PUMP_S = 0.005  # PumpEvents curto para reduzir latencia do callback
LAZY_DECAY_EVERY = 10  # decaimento em blocos de 10 ciclos
LAZY_DECAY_AMOUNT = 10

# Contadores de painel em memória compartilhada entre processos.
LIVE_FIELDS = ("book_capturados", "book_gravados", "tt_detectados", "tt_gravados", "drops", "falhas_gravacao")
LIVE_FIELD_INDEX = {name: i for i, name in enumerate(LIVE_FIELDS)}

def _live_inc(live_stats, a_idx, campo, n=1):
    if live_stats is None or a_idx is None:
        return
    try:
        live_stats[a_idx * len(LIVE_FIELDS) + LIVE_FIELD_INDEX[campo]] += int(n)
    except Exception:
        pass

def _live_get(live_stats, a_idx, campo):
    if live_stats is None:
        return 0
    try:
        return int(live_stats[a_idx * len(LIVE_FIELDS) + LIVE_FIELD_INDEX[campo]])
    except Exception:
        return 0

BASE_PASTA = os.environ.get('PROFIT_DATA_DIR', os.path.expanduser("~/MarketData/Profit"))
TZ_BR = ZoneInfo("America/Sao_Paulo")

INTERVALO_SALVAMENTO_S = 60
FSYNC_INTERVALO_S = 300
PARQUET_ENGINE = "pyarrow"
PARQUET_COMPRESSION = "snappy"

# B2: Watchdog COM — importado de adapters/com_watchdog.py
from adapters.com_watchdog import (
    COMHeartbeatMonitor, COM_WATCHDOG_TIMEOUT_S, COM_WATCHDOG_CHECK_S,
    watchdog_com_cycle,
)
# B4: Flush transacional com retry — importado de adapters/file_storage.py
from adapters.file_storage import flush_buffers_with_retry
MAX_FILA = 1_000_000

from adapters.dashboard_api import DashboardAPI, DashboardState

BOOK_FIELDS = ["HORC", "ACP", "VOC", "OCP", "OVD", "VOV", "AVD", "HORV"]
TT_FIELDS = ["DAT", "ACP", "PRE", "QUL", "AVD", "AGR", "AGAG"]


# ============================================================================
# SCHEMAS
# ============================================================================

BOOK_SCHEMA = {
    "capture_sequence": "int64", "snapshot_id": "int64",
    "time_ms": "int64", "timestamp_recebimento_python": "int64",
    "simbolo": "string",
}
for n in range(1, NIVEIS_BOOK + 1):
    BOOK_SCHEMA[f"bid_p{n}"] = "float64"
    BOOK_SCHEMA[f"bid_v{n}"] = "int64"
    BOOK_SCHEMA[f"bid_agente{n}"] = "string"
    BOOK_SCHEMA[f"ask_p{n}"] = "float64"
    BOOK_SCHEMA[f"ask_v{n}"] = "int64"
    BOOK_SCHEMA[f"ask_agente{n}"] = "string"
BOOK_SCHEMA["keepalive"] = "bool"

TT_SCHEMA = {
    "capture_sequence": "int64",
    "event_id": "int64",
    "time_ms": "int64",
    "timestamp_recebimento_python": "int64",
    "timestamp_brt": "datetime64[ns]",
    "simbolo": "string",
    "origem": "string",
    "compradora": "string",
    "preco": "float64",
    "quantidade": "int64",
    "vendedora": "string",
    "agressor": "string",
    "agente_agressor": "string",
    "direcao": "int8",
}


LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(processName)s/%(threadName)s] %(message)s"

# O console fica reservado para o dashboard. INFO/WARNING vao para arquivo;
# somente ERROR+ aparece no prompt para nao destruir o layout fixo.
_log_file = os.path.join(BASE_PASTA, "motor_v23_dashboard.log") if "BASE_PASTA" in globals() else "motor_v23_dashboard.log"
try:
    os.makedirs(os.path.dirname(_log_file) or ".", exist_ok=True)
except Exception:
    pass
logger = logging.getLogger("Motor")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False
_fmt = logging.Formatter(LOG_FORMAT)
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_ch = logging.StreamHandler(sys.stderr)
_ch.setLevel(logging.ERROR)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.WARNING)

_next_snapshot_id = itertools.count(1)
_next_event_id = itertools.count(1)
_next_capture_seq = itertools.count(1)


def agora_br():
    return datetime.now(tz=TZ_BR)


# ============================================================================
# LIMPEZA
# ============================================================================

def _tamanho_human_readable(tamanho):
    for unidade in ['B', 'KB', 'MB', 'GB']:
        if tamanho < 1024:
            return f"{tamanho:.1f} {unidade}"
        tamanho /= 1024
    return f"{tamanho:.1f} TB"


def limpar_pasta(base_pasta, dry_run=False, backup=False, tudo=False):
    base = Path(base_pasta)
    if not base.exists():
        print(f"Pasta nao existe: {base}")
        return
    lixo, parquet_validos = [], []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        nome, tamanho = p.name, p.stat().st_size
        if ".tmp." in nome and nome.endswith(".parquet"):
            lixo.append((p, tamanho, "tmp abortado"))
        elif nome.endswith(".bak"):
            lixo.append((p, tamanho, "quarentena"))
        elif tamanho == 0 and nome.endswith(".parquet"):
            lixo.append((p, tamanho, "zero bytes"))
        elif nome.endswith(".parquet") and not tudo:
            parquet_validos.append((p, tamanho))
        elif tudo and nome.endswith(".parquet"):
            lixo.append((p, tamanho, "parquet (modo tudo)"))
    total_lixo = sum(t for _, t, _ in lixo)
    total_parquet = sum(t for _, t in parquet_validos)
    print(f"=" * 60)
    print(f"[LIMPEZA] Pasta base: {base}")
    print(f"  Lixo: {len(lixo)} arquivos ({_tamanho_human_readable(total_lixo)})")
    print(f"  Parquet validos: {len(parquet_validos)} ({_tamanho_human_readable(total_parquet)})")
    print(f"=" * 60)
    if backup and parquet_validos:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pasta_backup = base / f"backup_{ts}"
        if not dry_run:
            pasta_backup.mkdir(parents=True, exist_ok=True)
        print(f"\n[BACKUP] Destino: {pasta_backup}")
        for p, t in parquet_validos:
            rel, dest = p.relative_to(base), pasta_backup / p.relative_to(base)
            print(f"  -> {rel} ({_tamanho_human_readable(t)})")
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
    if lixo:
        print(f"\n[REMOCAO]")
        for p, t, motivo in lixo:
            print(f"  x {p.relative_to(base)} ({motivo}, {_tamanho_human_readable(t)})")
            if not dry_run:
                try:
                    p.unlink()
                except Exception as e:
                    print(f"    ERRO: {e}")
    if not dry_run:
        vazias = 0
        for p in sorted(base.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                try:
                    p.rmdir()
                    vazias += 1
                except Exception:
                    pass
        if vazias:
            print(f"\n  Pastas vazias removidas: {vazias}")
    print(f"\n{'[DRY-RUN] Nada alterado.' if dry_run else '[LIMPEZA] Concluida.'}")
    print("=" * 60)


# ============================================================================
# COM INTERFACES
# ============================================================================

def _carregar_interfaces():
    if os.name != 'nt':
        raise RuntimeError("O protocolo RTD/COM requer Windows. Ambiente atual: " + os.name)
        
    candidatos = [
        "comtypes.gen.RTDTrading",
        "comtypes.gen._EFCFBDCA_78A5_450B_8228_346C4F44D5B8_0_1_0",
    ]

    for nome in candidatos:
        try:
            mod = importlib.import_module(nome)
            logger.info(f"Modulo importado (1a tentativa): {nome}")
            return mod
        except ImportError:
            continue

    logger.warning("Modulos nao encontrados no cache. Tentando criar objeto COM para gerar stubs...")
    try:
        srv = comtypes.client.CreateObject(PROG_ID)
        logger.info(f"Objeto COM criado para gerar stubs: {type(srv).__name__}")
        if "comtypes.gen" in sys.modules:
            importlib.reload(sys.modules["comtypes.gen"])
    except Exception as e:
        logger.warning(f"Falha ao criar objeto COM para stubs: {e}")

    for nome in candidatos:
        try:
            mod = importlib.import_module(nome)
            logger.info(f"Modulo importado (2a tentativa): {nome}")
            return mod
        except ImportError:
            continue

    msg = (
        "Modulo RTDTrading nao encontrado. Verifique:\n"
        "  1. ProfitChart esta aberto?\n"
        "  2. A janela RTD esta visivel no ProfitChart?\n"
        "  3. Rode: python -c \"import comtypes.client; comtypes.client.CreateObject('RTDTrading.RTDServer')\""
    )
    raise RuntimeError(msg)


def _criar_callback(IRTDUpdateEvent, notify, disc):
    class Callback(comtypes.COMObject):
        _com_interfaces_ = [IRTDUpdateEvent]
        def UpdateNotify(self):
            notify.set()
            return 0
        def Disconnect(self):
            disc.set()
            return 0
        def Heartbeat(self):
            return 1
    return Callback()


_next_tid = itertools.count(1)


def _connect(srv, strings):
    tid = next(_next_tid)
    gnv = c_int(1)
    out = srv.ConnectData(tid, list(strings), byref(gnv))
    valor = out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else None
    return tid, valor


def _refresh(srv):
    try:
        return srv.RefreshData()
    except Exception:
        try:
            n = c_long(0)
            return srv.RefreshData(n)
        except Exception:
            n = c_long(0)
            return srv.RefreshData(byref(n))


def conectar_servidor():
    mod = _carregar_interfaces()
    srv = comtypes.client.CreateObject(PROG_ID)
    logger.info(f"Servidor criado: {type(srv).__name__}")
    return srv, mod.IRTDUpdateEvent


def descobrir_ativos_rtd():
    """Descobre BOOKn/T&Tn validos usando INFO/ATV + RefreshData.

    CRITICO: BOOK e T&T sao sequencias INDEPENDENTES no ProfitChart RTD.
    BOOK0 nao necessariamente pertence ao mesmo ativo que T&T0.
    Fazemos o pareamento por SIMBOLO (INFO/ATV), nunca por indice.
    """
    comtypes.CoInitialize()
    srv = None
    tids = []
    info_map = {}
    book_map = {}   # indice -> simbolo
    tt_map = {}     # indice -> simbolo
    try:
        srv, IRTDUpdateEvent = conectar_servidor()
        notify = threading.Event()
        disc = threading.Event()
        cb = _criar_callback(IRTDUpdateEvent, notify, disc)
        srv.ServerStart(cb)

        for i in range(MAX_JANELAS_RTD):
            for kind, prefix in (("book", "BOOK"), ("tt", "T&T")):
                try:
                    tid, val = _connect(srv, [f"{prefix}{i}" if prefix == "BOOK" else f"T&T{i}", "INFO", "ATV"])
                    tids.append(tid)
                    info_map[tid] = (i, kind)
                    v = sstr(val).upper()
                    if v and not _topico_invalido(v):
                        if kind == "book":
                            book_map[i] = v
                        else:
                            tt_map[i] = v
                except Exception:
                    continue

        # Algumas janelas respondem INFO/ATV somente apos o primeiro ciclo de atualizacao.
        deadline = time.perf_counter() + 1.5
        while time.perf_counter() < deadline:
            try:
                comtypes.client.PumpEvents(0.05)
            except Exception:
                pass
            try:
                data = _refresh(srv)
            except Exception:
                data = None
            for tid, val in parse_refresh_data(data):
                info = info_map.get(tid)
                if not info:
                    continue
                i, kind = info
                v = sstr(val).upper()
                if v and not _topico_invalido(v):
                    if kind == "book":
                        book_map[i] = v
                    else:
                        tt_map[i] = v
            if notify.is_set():
                notify.clear()

        # PAREAMENTO POR SIMBOLO (nao por indice)
        prioridade = {s.upper(): idx for idx, s in enumerate(ATIVOS_CONFIG)}
        saida = []
        vistos = set()

        for simbolo_conf in ATIVOS_CONFIG:
            sym_upper = simbolo_conf.upper()
            if sym_upper in vistos:
                continue
            vistos.add(sym_upper)

            # Encontra indice do BOOK para este simbolo
            book_idx = None
            for idx, sym in book_map.items():
                if sym == sym_upper:
                    book_idx = idx
                    break

            # Encontra indice do T&T para este simbolo
            tt_idx = None
            for idx, sym in tt_map.items():
                if sym == sym_upper:
                    tt_idx = idx
                    break

            if book_idx is None and tt_idx is None:
                logger.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: nenhuma janela RTD encontrada.")
                continue

            if book_idx is not None and tt_idx is not None and book_idx != tt_idx:
                logger.info(
                    f"[AUTO-DISCOVERY] {simbolo_conf}: BOOK{book_idx} + T&T{tt_idx} "
                    f"(indices diferentes — pareamento por simbolo OK)"
                )
            elif book_idx is None:
                logger.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: somente T&T{tt_idx} encontrado (sem BOOK).")
            elif tt_idx is None:
                logger.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: somente BOOK{book_idx} encontrado (sem T&T).")
            else:
                logger.info(f"[AUTO-DISCOVERY] {simbolo_conf}: BOOK{book_idx} + T&T{tt_idx}")

            saida.append({
                "simbolo": sym_upper,
                "book": f"BOOK{book_idx}" if book_idx is not None else None,
                "tt": f"T&T{tt_idx}" if tt_idx is not None else None,
            })

        saida.sort(key=lambda a: prioridade.get(a["simbolo"], 10_000))
        logger.info(f"[AUTO-DISCOVERY] {len(saida)} ativo(s) mapeado(s) de {MAX_JANELAS_RTD} janelas testadas")
        for a in saida:
            b = a["book"] or "N/A"
            t = a["tt"] or "N/A"
            logger.info(f"[AUTO-DISCOVERY]   -> {a['simbolo']}: book={b}, tt={t}")
        return saida
    finally:
        try:
            if srv is not None:
                for tid in tids:
                    try:
                        srv.DisconnectData(tid)
                    except Exception:
                        pass
                try:
                    srv.ServerTerminate()
                except Exception:
                    pass
        finally:
            comtypes.CoUninitialize()


def preparar_ativos():
    global ATIVOS
    if AUTO_DISCOVER_ATIVOS:
        encontrados = descobrir_ativos_rtd()
        if encontrados:
            ATIVOS = encontrados
            logger.info(f"[AUTO-DISCOVERY] {len(ATIVOS)} ativo(s) selecionado(s).")
            return
    ATIVOS = [{"simbolo": s, "book": f"BOOK{i}", "tt": f"T&T{i}"} for i, s in enumerate(ATIVOS_CONFIG)]
    logger.warning("[AUTO-DISCOVERY] Fallback para configuracao manual.")


# ============================================================================
# PARSER REFRESHDATA
# ============================================================================

def _is_iterable(obj):
    try:
        return hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes))
    except Exception:
        return False


def parse_refresh_data(data):
    if data is None:
        return []
    flat = list(data)
    if not flat:
        return []
    pairs = []
    if len(flat) == 2 and isinstance(flat[1], tuple) and len(flat[1]) == 2:
        tids, vals = flat[1]
        if _is_iterable(tids) and _is_iterable(vals):
            try:
                for tid, val in zip(tids, vals):
                    pairs.append((int(tid), val))
                return pairs
            except Exception:
                pass
    if flat and isinstance(flat[0], (list, tuple)):
        for r in flat:
            try:
                if r is not None and len(r) >= 2:
                    pairs.append((int(r[0]), r[1]))
            except Exception:
                continue
        return pairs
    if len(flat) % 2 == 0:
        for i in range(0, len(flat), 2):
            try:
                pairs.append((int(flat[i]), flat[i + 1]))
            except Exception:
                continue
        return pairs
    logger.warning(f"Formato RefreshData nao reconhecido: {flat!r}")
    return []


# ============================================================================
# UTILS
# ============================================================================

def fnum(v, d=0.0):
    try:
        if v is None or v == "":
            return d
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v)
        if not s:
            return d
        x = float(s.replace(",", "."))
        return d if not math.isfinite(x) else x
    except Exception:
        return d


def sstr(v):
    return "" if v is None else str(v).strip()


def _normalizar_simbolo(v):
    """Normaliza valor do RTD para símbolo comparável (uppercase, strip)."""
    s = sstr(v).upper()
    if s in ('', 'NONE', 'FERRAMENTA INVÁLIDA', 'FERRAMENTA INV' + chr(225) + 'LIDA'):
        return ''
    return s


def _parse_hora_manual(hora_str, ano, mes, dia):
    principal, _, frac = hora_str.partition(".")
    h_str, m_str, sec_str = principal.split(":")
    h, m, sec = int(h_str), int(m_str), int(sec_str)
    micro = 0
    if frac:
        micro = int((frac + "000000")[:6])
    return datetime(ano, mes, dia, h, m, sec, micro)


def parse_dat(s, dia_ref):
    s = sstr(s)
    if not s:
        return None, False
    base = dia_ref.replace(tzinfo=None) if getattr(dia_ref, "tzinfo", None) is not None else dia_ref
    try:
        if "/" in s:
            data_str, _, hora_str = s.partition(" ")
            if not hora_str:
                return None, False
            dia_s, mes_s, ano_s = data_str.split("/")
            return _parse_hora_manual(hora_str, int(ano_s), int(mes_s), int(dia_s)), False
        if "-" in s:
            data_str, _, hora_str = s.partition(" ")
            if not hora_str:
                return None, False
            ano_s, mes_s, dia_s = data_str.split("-")
            return _parse_hora_manual(hora_str, int(ano_s), int(mes_s), int(dia_s)), False
        dt = _parse_hora_manual(s, base.year, base.month, base.day)
        if dt > base + timedelta(hours=12):
            dt -= timedelta(days=1)
        return dt, True
    except (ValueError, IndexError):
        return None, False


def enforce_schema(df, schema):
    df = df.copy()
    n = len(df)
    faltantes = {}
    for col, dtype in schema.items():
        if col not in df.columns:
            if dtype == "datetime64[ns]":
                faltantes[col] = pd.array([pd.NaT] * n, dtype="datetime64[ns]")
            elif dtype == "string":
                faltantes[col] = pd.array([""] * n, dtype="string")
            elif dtype == "bool":
                faltantes[col] = pd.array([False] * n, dtype="bool")
            elif dtype.startswith("int"):
                faltantes[col] = pd.array([0] * n, dtype=dtype)
            else:
                faltantes[col] = pd.array([0.0] * n, dtype="float64")
    if faltantes:
        df = pd.concat([df, pd.DataFrame(faltantes, index=df.index)], axis=1)
    for col, dtype in schema.items():
        try:
            if dtype == "datetime64[ns]":
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    if getattr(df[col].dt, "tz", None) is not None:
                        df[col] = df[col].dt.tz_convert(TZ_BR).dt.tz_localize(None)
                    df[col] = df[col].astype("datetime64[ns]")
                else:
                    try:
                        s = pd.to_datetime(df[col], errors="coerce")
                    except Exception:
                        s = pd.to_datetime(df[col], errors="coerce", utc=True)
                        if getattr(s.dt, "tz", None) is not None:
                            s = s.dt.tz_convert(TZ_BR).dt.tz_localize(None)
                    if getattr(s.dt, "tz", None) is not None:
                        s = s.dt.tz_convert(TZ_BR).dt.tz_localize(None)
                    df[col] = s.astype("datetime64[ns]")
            elif dtype in ("int64", "int8"):
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(dtype)
            elif dtype == "float64":
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype("float64")
            elif dtype == "bool":
                if pd.api.types.is_object_dtype(df[col]):
                    df[col] = df[col].fillna(False).map(
                        lambda x: bool(str(x).lower() not in ("", "0", "false", "none"))
                    ).astype(bool)
                else:
                    df[col] = df[col].fillna(False).astype(bool)
            elif dtype == "string":
                df[col] = df[col].fillna("").astype("string")
            else:
                df[col] = df[col].astype(dtype)
        except Exception as e:
            logger.warning(f"[SCHEMA] Falha coluna '{col}' -> {dtype}: {e}")
            if dtype == "datetime64[ns]":
                df[col] = pd.NaT
            elif dtype == "string":
                df[col] = ""
            elif dtype == "bool":
                df[col] = False
            elif dtype.startswith("int"):
                df[col] = 0
            else:
                df[col] = 0.0
    return df[list(schema.keys())]


# ============================================================================
# PARQUET HELPERS
# ============================================================================

def _quarentena_arquivo(caminho, motivo):
    if not os.path.exists(caminho):
        return
    ts = int(time.time())
    pid = os.getpid()
    bak = f"{caminho}.{motivo}.{ts}.{pid}.bak"
    try:
        os.replace(caminho, bak)
        logger.warning(f"[PARQUET] Isolado: {caminho} -> {bak}")
    except Exception as e:
        logger.error(f"[PARQUET] Nao foi possivel isolar {caminho}: {e}")
        try:
            if os.path.getsize(caminho) == 0:
                os.remove(caminho)
        except Exception:
            pass


def _escrever_parquet_atomico(caminho, df):
    tmp = f"{caminho}.tmp.{os.getpid()}.{threading.get_ident()}.parquet"
    try:
        df.to_parquet(tmp, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
        os.replace(tmp, caminho)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def write_parquet_part(pasta, hora, df, schema):
    if df is None or len(df) == 0:
        return True
    try:
        os.makedirs(pasta, exist_ok=True)
    except Exception as e:
        logger.error(f"[PARQUET-PART] Nao foi possivel criar pasta: {e}")
        return False
    df = enforce_schema(df, schema)
    nome = f"{hora}.part_{time.time_ns()}_{os.getpid()}_{threading.get_ident()}.parquet"
    caminho = os.path.join(pasta, nome)
    try:
        _escrever_parquet_atomico(caminho, df)
        return True
    except Exception as e:
        fb = caminho.replace(".parquet", f"_ERRO_{int(time.time())}.parquet")
        logger.error(f"[PARQUET-PART] Falha em {caminho}: {e} -> fallback {fb}")
        try:
            _escrever_parquet_atomico(fb, df)
        except Exception as e2:
            logger.error(f"[PARQUET-PART] Fallback falhou: {e2}")
        return False


def consolidar_book_parquet(base_pasta, dia_str):
    import glob

    dt = datetime.strptime(dia_str, "%Y%m%d").date()
    pasta_dia = os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}"
    )
    padrao = os.path.join(pasta_dia, "sym=*", "tipo=BOOK", "*.part_*.parquet")
    partes = sorted(glob.glob(padrao))
    if not partes:
        print(f"[CONSOLIDA-BOOK] Nenhuma parte encontrada para {dia_str}")
        return

    grupos = {}
    for p in partes:
        pasta = os.path.dirname(p)
        hora = os.path.basename(p).split(".part_")[0]
        grupos.setdefault((pasta, hora), []).append(p)

    print(f"[CONSOLIDA-BOOK] {len(partes)} partes em {len(grupos)} particoes para {dia_str}.")
    total_regs = 0
    for (pasta, hora), arquivos in grupos.items():
        caminho_final = os.path.join(pasta, f"{hora}.parquet")
        dfs = []
        boas = []
        for a in arquivos:
            try:
                dfs.append(pd.read_parquet(a, engine=PARQUET_ENGINE))
                boas.append(a)
            except Exception as e:
                logger.error(f"[CONSOLIDA-BOOK] Parte corrompida {a}: {e}. Isolando.")
                _quarentena_arquivo(a, "corrompido")
        if os.path.exists(caminho_final):
            try:
                dfs.insert(0, pd.read_parquet(caminho_final, engine=PARQUET_ENGINE))
            except Exception as e:
                logger.error(f"[CONSOLIDA-BOOK] Consolidado existente corrompido {caminho_final}: {e}. Isolando.")
                _quarentena_arquivo(caminho_final, "corrompido")
        if not dfs:
            print(f"  [ERRO] {pasta}/{hora}: nenhuma parte legivel (todas isoladas)")
            continue
        try:
            df_final = pd.concat(dfs, ignore_index=True)
            if "time_ms" in df_final.columns:
                df_final = df_final.sort_values("time_ms").reset_index(drop=True)
            _escrever_parquet_atomico(caminho_final, df_final)
            for a in boas:
                os.remove(a)
            total_regs += len(df_final)
            print(f"  [OK] {os.path.relpath(caminho_final, pasta_dia)}: "
                  f"{len(boas)} partes -> {len(df_final):,} regs")
        except Exception as e:
            print(f"  [ERRO] {pasta}/{hora}: {e} (partes preservadas para investigacao)")
    print(f"[CONSOLIDA-BOOK] Concluido: {total_regs:,} registros consolidados para {dia_str}.")


# ============================================================================
# CONSOLIDA TT
# ============================================================================

def consolidar_tt_parquet(base_pasta, dia_str):
    import glob

    dt = datetime.strptime(dia_str, "%Y%m%d").date()
    pasta_dia = os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}"
    )
    padrao = os.path.join(pasta_dia, "sym=*", "tipo=TT", "*.part_*.parquet")
    partes = sorted(glob.glob(padrao))
    if not partes:
        print(f"[CONSOLIDA-TT] Nenhuma parte encontrada para {dia_str}")
        return

    grupos = {}
    for p in partes:
        pasta = os.path.dirname(p)
        hora = os.path.basename(p).split(".part_")[0]
        grupos.setdefault((pasta, hora), []).append(p)

    print(f"[CONSOLIDA-TT] {len(partes)} partes em {len(grupos)} particoes para {dia_str}.")
    total_regs = 0
    for (pasta, hora), arquivos in grupos.items():
        caminho_final = os.path.join(pasta, f"{hora}.parquet")
        dfs = []
        boas = []
        for a in arquivos:
            try:
                dfs.append(pd.read_parquet(a, engine=PARQUET_ENGINE))
                boas.append(a)
            except Exception as e:
                logger.error(f"[CONSOLIDA-TT] Parte corrompida {a}: {e}. Isolando.")
                _quarentena_arquivo(a, "corrompido")
        if os.path.exists(caminho_final):
            try:
                dfs.insert(0, pd.read_parquet(caminho_final, engine=PARQUET_ENGINE))
            except Exception as e:
                logger.error(f"[CONSOLIDA-TT] Consolidado existente corrompido {caminho_final}: {e}. Isolando.")
                _quarentena_arquivo(caminho_final, "corrompido")
        if not dfs:
            print(f"  [ERRO] {pasta}/{hora}: nenhuma parte legivel (todas isoladas)")
            continue
        try:
            df_final = pd.concat(dfs, ignore_index=True)
            if "time_ms" in df_final.columns:
                df_final = df_final.sort_values("time_ms").reset_index(drop=True)
            _escrever_parquet_atomico(caminho_final, df_final)
            for a in boas:
                os.remove(a)
            total_regs += len(df_final)
            print(f"  [OK] {os.path.relpath(caminho_final, pasta_dia)}: "
                  f"{len(boas)} partes -> {len(df_final):,} regs")
        except Exception as e:
            print(f"  [ERRO] {pasta}/{hora}: {e} (partes preservadas para investigacao)")
    print(f"[CONSOLIDA-TT] Concluido: {total_regs:,} registros consolidados para {dia_str}.")

# ============================================================================
# DIAG
# ============================================================================

VALORES_INVALIDOS = {
    "ferramenta inválida", "ferramenta invalida",
    "atributo inválido", "atributo invalido",
    "#n/a", "n/a",
}


def _topico_invalido(val):
    return sstr(val).strip().lower() in VALORES_INVALIDOS


def _diag():
    comtypes.CoInitialize()
    srv = None
    tids = []
    status = []
    try:
        srv, IRTDUpdateEvent = conectar_servidor()
        notify = threading.Event()
        disc = threading.Event()
        cb = _criar_callback(IRTDUpdateEvent, notify, disc)
        srv.ServerStart(cb)
        print("ServerStart OK")

        for a_idx, ativo in enumerate(ATIVOS):
            print(f"\n--- ATIVO {a_idx}: {ativo['simbolo']} (book={ativo['book']}, tt={ativo['tt']}) ---")
            st = {"book_info_ok": False, "tt_info_ok": False,
                  "book_ok": 0, "book_total": 0, "tt_ok": 0, "tt_total": 0}

            tid, val = _connect(srv, [ativo["book"], "INFO", "ATV"])
            tids.append(tid)
            st["book_info_ok"] = not _topico_invalido(val)
            print(f"  {ativo['book']} INFO/ATV -> tid={tid} simbolo_detectado={val!r}"
                  + ("" if st["book_info_ok"] else "  [JANELA/PAINEL BOOK NAO ENCONTRADO NO PROFIT]"))

            if ativo["tt"] is not None:
                tid, val = _connect(srv, [ativo["tt"], "INFO", "ATV"])
                tids.append(tid)
                st["tt_info_ok"] = not _topico_invalido(val)
            else:
                st["tt_info_ok"] = False
            print(f"  {ativo['tt']} INFO/ATV -> tid={tid} simbolo_detectado={val!r}"
                  + ("" if st["tt_info_ok"] else "  [JANELA T&T NAO ENCONTRADA NO PROFIT]"))

            for campo in ["OCP", "OVD", "VOC", "VOV", "ACP", "AVD"]:
                st["book_total"] += 1
                try:
                    tid, val = _connect(srv, [ativo["book"], campo, "0"])
                    tids.append(tid)
                    ok = not _topico_invalido(val)
                    st["book_ok"] += int(ok)
                    print(f"  {ativo['book']} {campo}/0 -> tid={tid} val={val!r}" + ("" if ok else "  [INVALIDO]"))
                except Exception as e:
                    print(f"  {ativo['book']} {campo}/0 -> ERRO: {e}")

            if ativo["tt"] is not None:
                for campo in ["PRE", "QUL", "DAT", "ACP", "AVD", "AGR", "AGAG"]:
                    st["tt_total"] += 1
                    try:
                        tid, val = _connect(srv, [ativo["tt"], campo, "0"])
                        tids.append(tid)
                        ok = not _topico_invalido(val)
                        st["tt_ok"] += int(ok)
                        print(f"  {ativo['tt']} {campo}/0 -> tid={tid} val={val!r}" + ("" if ok else "  [INVALIDO]"))
                    except Exception as e:
                        print(f"  {ativo['tt']} {campo}/0 -> ERRO: {e}")

            status.append(st)

        print("\nAguardando 3s por dados...")
        for _ in range(10):
            comtypes.client.PumpEvents(0.3)

        data = _refresh(srv)
        print(f"\nRefreshData: {len(data) if data else 0} itens")
        if data:
            print(f"Primeiros itens: {list(data)[:10]}")

        print("\n" + "=" * 70)
        print("RESUMO DO DIAGNOSTICO")
        print("=" * 70)
        algum_problema = False
        for a_idx, ativo in enumerate(ATIVOS):
            st = status[a_idx]
            simbolo = ativo["simbolo"]
            if st["book_info_ok"] and st["tt_info_ok"] and st["book_ok"] == st["book_total"] and st["tt_ok"] == st["tt_total"]:
                print(f"[OK] ATIVO {a_idx} ({simbolo}): book e T&T conectados corretamente.")
                continue
            algum_problema = True
            if not st["book_info_ok"] and not st["tt_info_ok"]:
                print(f"[FALHA] ATIVO {a_idx} ({simbolo}): NENHUMA janela RTD encontrada "
                      f"para {ativo['book']}/{ativo['tt']}. Abra uma janela de Book de Ofertas "
                      f"para {simbolo} no ProfitChart (esta deve ser a janela de indice {a_idx}, "
                      f"contando pela ordem de abertura).")
            else:
                if not st["book_info_ok"] or st["book_ok"] < st["book_total"]:
                    print(f"[FALHA] ATIVO {a_idx} ({simbolo}): janela existe, mas o painel de BOOK "
                          f"({ativo['book']}) nao esta exportando dados ({st['book_ok']}/{st['book_total']} "
                          f"campos validos). Verifique se o painel de Book de Ofertas esta visivel/ativo "
                          f"nessa janela do ProfitChart.")
                if not st["tt_info_ok"] or st["tt_ok"] < st["tt_total"]:
                    print(f"[FALHA] ATIVO {a_idx} ({simbolo}): janela existe, mas o painel de TIMES & TRADES "
                          f"({ativo['tt']}) nao esta exportando dados ({st['tt_ok']}/{st['tt_total']} "
                          f"campos validos). Verifique se o painel de Times & Trades esta visivel/ativo "
                          f"nessa janela do ProfitChart.")
        if not algum_problema:
            print("Todos os ativos conectados e prontos para captura.")
        print("=" * 70)
    finally:
        try:
            if srv is not None:
                for tid in tids:
                    try:
                        srv.DisconnectData(tid)
                    except Exception:
                        pass
                srv.ServerTerminate()
        except Exception:
            pass
        comtypes.CoUninitialize()
    print("\nDIAG concluido.")


# ============================================================================
# THREAD COM — CORRECAO VIRADA MEIA-NOITE + MULTI-ATIVO
# ============================================================================

def thread_com(filas_book, filas_tt, ativos, base_pasta, shutdown_event, stats_lock, live_stats):
    reconecta_delay = 1.0
    max_delay = 60.0
    primeira_vez = True
    estado = None
    # B2: watchdog thread — detecta hang COM no writer
    _ciclo_beat = [time.time()]  # mutable para thread watchdog
    _COM_HANG_TIMEOUT = 30.0
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
            "tt_assinaturas_cache": [[None] * LINHAS_TT for _ in range(n_ativos)],
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
    tt_sig_cache = estado["tt_assinaturas_cache"]

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

            # 1. Atualiza apenas as assinaturas que mudaram e conta a frequência
            current_counts = {}
            example_r = {}
            
            for idx in tt_sujas[a_idx]:
                r = tt_cur[a_idx][idx]
                pre = r.get("PRE")
                if pre and pre != 0:
                    tt_sig_cache[a_idx][idx] = (
                        r.get("DAT"), r.get("ACP"), pre,
                        r.get("QUL"), r.get("AVD"),
                        r.get("AGR"), r.get("AGAG")
                    )
                else:
                    tt_sig_cache[a_idx][idx] = None

            for sig in tt_sig_cache[a_idx]:
                if sig is None:
                    continue
                current_counts[sig] = current_counts.get(sig, 0) + 1
                if sig not in example_r:
                    # Precisamos de um exemplo para extrair os dados depois
                    # Buscamos no tt_cur usando o índice original se necessário, 
                    # ou guardamos a ref aqui.
                    example_r[sig] = sig # Otimização: a própria sig tem os dados brutos

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

def thread_escritora(fila, nome, prefixo, schema, base_pasta, shutdown_event, stats_lock, a_idx, live_stats):
    """Writer BOOK: particoes Parquet por hora e por ativo, com flush periodico."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    buffers = {}
    _BOOK_BUFFER_MAX = 500_000  # B4: limite total de rows em todos os buffers
    ultimo_flush = time.perf_counter()

    def _append_hour_file(key, rows):
        simbolo, ano, mes, dia, hora = key
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        if df.empty:
            return 0
        if "time_ms" not in df.columns:
            logger.error(f"[{nome}] Lote BOOK sem 'time_ms'.")
            return 0
        df = df.sort_values("time_ms").reset_index(drop=True)
        pasta = os.path.join(
            base_pasta, "RAW",
            f"ano={ano}", f"mes={mes}", f"dia={dia}",
            f"sym={simbolo}", "tipo=BOOK"
        )
        ok = write_parquet_part(pasta, hora, df, BOOK_SCHEMA)
        if ok:
            _registrar_book(base_pasta, str(df["simbolo"].iloc[0]), "gravados", stats_lock, n=len(df))
            _live_inc(live_stats, a_idx, "book_gravados", len(df))
            return len(df)
        else:
            _registrar_stat(base_pasta, "erros", stats_lock)
            _registrar_book(base_pasta, simbolo, "falhas_gravacao", stats_lock, n=len(df))
            _live_inc(live_stats, a_idx, "falhas_gravacao", len(df))
            return 0

    while not shutdown_event.is_set() or not fila.empty() or buffers:
        try:
            lote = fila.get(timeout=1.0)
            if lote:
                for row in lote:
                    try:
                        tms = int(row.get("time_ms", 0))
                        if tms <= 0:
                            logger.warning(f"[{nome}] BOOK ignorado: time_ms invalido ({tms}).")
                            continue
                        dt = pd.Timestamp(tms, unit="ms", tz="UTC").tz_convert(TZ_BR)
                        key = (
                            str(row.get("simbolo", "")),
                            dt.strftime("%Y"),
                            dt.strftime("%m"),
                            dt.strftime("%d"),
                            dt.strftime("%H"),
                        )
                        buffers.setdefault(key, []).append(row)
                        # B4: check overflow total
                        total_buf = sum(len(v) for v in buffers.values())
                        if total_buf > _BOOK_BUFFER_MAX:
                            logger.error(f"[{nome}] B4-BOOK overflow: {total_buf} rows — flush forcado")
                            break  # sai do for, flush abaixo
                    except Exception as e:
                        logger.warning(f"[{nome}] Falha ao classificar snapshot BOOK: {e}")
        except Empty:
            pass

        agora = time.perf_counter()
        deve_flush = (
            (agora - ultimo_flush >= INTERVALO_SALVAMENTO_S)
            or shutdown_event.is_set()
        )
        if not deve_flush:
            continue

        total_ok, total_tentado = flush_buffers_with_retry(buffers, _append_hour_file)

        ultimo_flush = agora
        if total_tentado:
            logger.info(f"[{nome}] BOOK flush: {total_ok}/{total_tentado} regs | arquivos=1/hora/ativo")

    if buffers:
        total_ok, total_tentado = flush_buffers_with_retry(buffers, _append_hour_file)
        logger.info(f"[{nome}] BOOK final: {total_ok}/{total_tentado} regs")

    logger.info(f"[{nome}] Finalizada. Formato: Parquet parts por hora/ativo.")



# ============================================================================
# THREAD ESCRITORA CSV (T&T)
# ============================================================================

def thread_escritora_tt(fila, nome, ativo_simbolo, schema, base_pasta, shutdown_event, stats_lock, a_idx, live_stats):
    """Writer T&T: particoes Parquet por hora e por ativo, com flush periodico."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    buffer = []
    _BUFFER_MAX = 1_000_000  # C3: limite maximo — previne OOM
    _buf_overflow_count = 0
    ultimo_salv = time.perf_counter()

    while not shutdown_event.is_set() or not fila.empty() or buffer:
        try:
            buffer.extend(fila.get(timeout=2.0))
        except Empty:
            pass
        # C3: se buffer estourou, logar e forcar flush
        if len(buffer) > _BUFFER_MAX:
            _buf_overflow_count += 1
            logger.error(f'[{nome}] C3-BUFFER overflow: {len(buffer)} rows (flush #{_buf_overflow_count})')
            # Forcar flush imediato (continua no loop, vai salvar abaixo)

        agora = time.perf_counter()
        vai_finalizar = shutdown_event.is_set()
        deve_salvar = bool(buffer) and (
            (agora - ultimo_salv >= INTERVALO_SALVAMENTO_S)
            or (len(buffer) > 500_000)
            or vai_finalizar
        )
        if not deve_salvar:
            continue

        df = pd.DataFrame(buffer)
        buffer = []
        ultimo_salv = agora
        if df.empty:
            continue
        if "time_ms" not in df.columns:
            logger.error(f"[{nome}] Lote T&T sem 'time_ms'. Descartando.")
            continue

        df = df.sort_values("time_ms").reset_index(drop=True)
        df["dt_br"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.tz_convert(TZ_BR)
        df["ano"] = df["dt_br"].dt.strftime("%Y")
        df["mes"] = df["dt_br"].dt.strftime("%m")
        df["dia"] = df["dt_br"].dt.strftime("%d")
        df["hora"] = df["dt_br"].dt.strftime("%H")

        total_tentado = len(df)
        total_ok = 0
        for (simbolo, ano, mes, dia, hora), g in df.groupby(
            ["simbolo", "ano", "mes", "dia", "hora"], sort=False
        ):
            g = g.drop(columns=["dt_br", "ano", "mes", "dia", "hora"], errors="ignore")
            pasta = os.path.join(
                base_pasta, "RAW",
                f"ano={ano}", f"mes={mes}", f"dia={dia}",
                f"sym={simbolo}", "tipo=TT",
            )
            ok = write_parquet_part(pasta, hora, g, TT_SCHEMA)
            if ok:
                total_ok += len(g)
                _registrar_tt(base_pasta, ativo_simbolo, "gravados", stats_lock, n=len(g))
                _live_inc(live_stats, a_idx, "tt_gravados", len(g))
            else:
                _registrar_stat(base_pasta, "erros", stats_lock)
                _registrar_tt(base_pasta, ativo_simbolo, "falhas_gravacao", stats_lock, n=len(g))
                _live_inc(live_stats, a_idx, "falhas_gravacao", len(g))

        if total_tentado > total_ok:
            _registrar_tt(base_pasta, ativo_simbolo, "nao_gravados", stats_lock, n=total_tentado - total_ok)
        logger.info(
            f"[{nome}] {total_ok}/{total_tentado} regs salvos em Parquet."
            + (" (final)" if vai_finalizar else "")
        )

    logger.info(f"[{nome}] Finalizada.")




# ============================================================================
# DASHBOARD WEB
# ============================================================================

def _start_web_dashboard(filas_book, filas_tt, live_stats, base_pasta, port=5000, open_browser=True):
    class _Server(ThreadingHTTPServer):
        allow_reuse_address=True
    server=_Server(('127.0.0.1',port), DashboardAPI)
    server.state=DashboardState(filas_book, filas_tt, live_stats, base_pasta, ATIVOS)
    threading.Thread(target=server.serve_forever,name='WebDashboard',daemon=True).start()
    url=f'http://127.0.0.1:{port}/'
    logger.info(f'[WEB] Dashboard: {url}')
    if open_browser:
        try:webbrowser.open(url,new=2)
        except Exception as e:logger.warning(f'[WEB] navegador: {e}')
    return server

# ============================================================================
# STATS
# ============================================================================

def _stats_dia_atual():
    return getattr(_thread_com_ciclo, "dia_replay_forcado", None) or agora_br().date()


def _stats_path(base_pasta, dt):
    return os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}",
        "_stats_captura.json",
    )


def _registrar_stat(base_pasta, campo, lock, dt=None, n=1):
    dt = dt or _stats_dia_atual()
    caminho = _stats_path(base_pasta, dt)
    with lock:
        stats = {}
        if os.path.exists(caminho):
            try:
                with open(caminho, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                stats = {}
        stats.setdefault("inicio", agora_br().isoformat())
        stats[campo] = stats.get(campo, 0) + n
        stats["ultima_atualizacao"] = agora_br().isoformat()
        try:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            tmp = f"{caminho}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            os.replace(tmp, caminho)
        except Exception as e:
            logger.warning(f"[STATS] Falha ao gravar {caminho}: {e}")


def _stat_chave_book(ativo_simbolo, etapa):
    """Chave padronizada para contadores acumulados de BOOK por ativo."""
    seguro = ''.join(c if c.isalnum() or c == '_' else '_' for c in str(ativo_simbolo))
    return f"book_{etapa}_{seguro}"


def _registrar_book(base_pasta, ativo_simbolo, etapa, lock, n=1, dt=None):
    _registrar_stat(base_pasta, _stat_chave_book(ativo_simbolo, etapa), lock, dt=dt, n=n)


def _stat_chave_tt(ativo_simbolo, etapa):
    """Chave padronizada para contadores acumulados de integridade T&T."""
    seguro = ''.join(c if c.isalnum() or c == '_' else '_' for c in str(ativo_simbolo))
    return f"tt_{etapa}_{seguro}"


def _registrar_tt(base_pasta, ativo_simbolo, etapa, lock, n=1, dt=None):
    _registrar_stat(base_pasta, _stat_chave_tt(ativo_simbolo, etapa), lock, dt=dt, n=n)


def _ler_stats(base_pasta, dt):
    caminho = _stats_path(base_pasta, dt)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================================
# MANIFESTO
# ============================================================================

def _sha256_arquivo(caminho, bloco=1024 * 1024):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for chunk in iter(lambda: f.read(bloco), b""):
            h.update(chunk)
    return h.hexdigest()


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

    for flag in ["--limpar", "--backup", "--dry-run", "--tudo", "--dia-replay", "--pasta-saida",
                 "--manifesto", "--sanidade", "--web-port"]:
        while flag in sys.argv:
            idx = sys.argv.index(flag)
            sys.argv.pop(idx)
            if idx < len(sys.argv) and not sys.argv[idx].startswith("--"):
                sys.argv.pop(idx)

    if "--diag" in sys.argv:
        _diag()
        return

    if "--duckdb" in sys.argv:
        _duckdb_shell(base_pasta)
        return

    if manifesto_dia:
        gerar_manifesto(base_pasta, manifesto_dia)
        return

    if sanidade_dia:
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

    preparar_ativos()
    if not ATIVOS:
        logger.error("Nenhum ativo RTD valido encontrado. Encerrando.")
        return

    print("=" * 70)
    print("motor_v23_web.py — ProfitChart RTD + Dashboard Web")
    print(f"Ativos: {[a['simbolo'] for a in ATIVOS]} ({len(ATIVOS)} ativos) | Pasta: {base_pasta}")
    print(f"Dashboard: http://127.0.0.1:{web_port} | BOOK=CSV/hora | TT=CSV/hora")
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