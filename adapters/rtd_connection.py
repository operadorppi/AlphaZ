# -*- coding: utf-8 -*-
"""
adapters/rtd_connection.py — Conexão COM com ProfitChart RTD.

Responsabilidades extraídas do motor_web.py:
  - Carregamento de interfaces COM (RTDTrading)
  - Criação de servidor RTD + callback
  - Descoberta automática de ativos (BOOK/T&T)
  - Assinatura de tópicos (ConnectData)
  - RefreshData (polling de dados)
  - Constantes de configuração RTD (NIVEIS_BOOK, LINHAS_TT, etc.)

Fluxo:
  ProfitChart RTD → rtd_connection → rtd_parser → rtd_writer → dashboard
"""

import os
import sys
import time
import math
import itertools
import logging
import threading
import importlib
from ctypes import c_int, c_long, byref
from datetime import datetime, timedelta

try:
    import comtypes
    import comtypes.client
except ImportError:
    comtypes = None
    comtypes_client = None

log = logging.getLogger(__name__)


# ============================================================================
# CONSTANTES RTD
# ============================================================================

PROG_ID = "RTDTrading.RTDServer"
NIVEIS_BOOK = 500
LINHAS_TT = 1000
POLL_S = 0.02
EVENT_PUMP_S = 0.005
MAX_JANELAS_RTD = 12

BOOK_FIELDS = ["HORC", "ACP", "VOC", "OCP", "OVD", "VOV", "AVD", "HORV"]
TT_FIELDS = ["DAT", "ACP", "PRE", "QUL", "AVD", "AGR", "AGAG"]

VALORES_INVALIDOS = {
    "ferramenta inválida", "ferramenta invalida",
    "atributo inválido", "atributo invalido",
    "#n/a", "n/a",
}

# Contadores de sequência globais (compatibilidade com motor_web)
_next_snapshot_id = itertools.count(1)
_next_event_id = itertools.count(1)
_next_capture_seq = itertools.count(1)
_next_tid = itertools.count(1)


# ============================================================================
# HELPERS BÁSICOS (exportados para backward compat)
# ============================================================================

def agora_br():
    """Retorna datetime atual no timezone de Brasília."""
    from zoneinfo import ZoneInfo
    return datetime.now(tz=ZoneInfo("America/Sao_Paulo"))


def fnum(v, d=0.0):
    """Converte valor RTD para float. Retorna d se inválido."""
    try:
        if v is None:
            return d
        s = str(v).strip()
        if not s:
            return d
        x = float(s.replace(",", "."))
        return d if not math.isfinite(x) else x
    except Exception:
        return d


def fint(v):
    """Converte valor RTD para int."""
    return int(fnum(v, 0))


def sstr(v):
    """Converte valor RTD para string stripada."""
    return "" if v is None else str(v).strip()


def _topico_invalido(val):
    """Verifica se o valor retornado indica tópico inválido/ausente."""
    return sstr(val).strip().lower() in VALORES_INVALIDOS


def _normalizar_simbolo(v):
    """Normaliza valor do RTD para símbolo comparável (uppercase, strip)."""
    s = sstr(v).upper()
    if s in ('', 'NONE', 'FERRAMENTA INVÁLIDA', 'FERRAMENTA INV' + chr(225) + 'LIDA'):
        return ''
    return s


# ============================================================================
# COM INTERFACES
# ============================================================================

def _carregar_interfaces():
    """Carrega módulos COM do RTDTrading (gera stubs se necessário)."""
    if comtypes is None:
        raise RuntimeError("comtypes não disponível")

    candidatos = [
        "comtypes.gen.RTDTrading",
        "comtypes.gen._EFCFBDCA_78A5_450B_8228_346C4F44D5B8_0_1_0",
    ]

    for nome in candidatos:
        try:
            mod = importlib.import_module(nome)
            log.info(f"Modulo importado (1a tentativa): {nome}")
            return mod
        except ImportError:
            continue

    log.warning("Modulos nao encontrados no cache. Tentando criar objeto COM...")
    try:
        srv = comtypes.client.CreateObject(PROG_ID)
        log.info(f"Objeto COM criado: {type(srv).__name__}")
        if "comtypes.gen" in sys.modules:
            importlib.reload(sys.modules["comtypes.gen"])
    except Exception as e:
        log.warning(f"Falha ao criar objeto COM: {e}")

    for nome in candidatos:
        try:
            mod = importlib.import_module(nome)
            log.info(f"Modulo importado (2a tentativa): {nome}")
            return mod
        except ImportError:
            continue

    raise RuntimeError(
        "Modulo RTDTrading nao encontrado. Verifique:\n"
        "  1. ProfitChart esta aberto?\n"
        "  2. A janela RTD esta visivel no ProfitChart?\n"
        "  3. Rode: python -c \"import comtypes.client; "
        "comtypes.client.CreateObject('RTDTrading.RTDServer')\""
    )


def _criar_callback(IRTDUpdateEvent, notify, disc):
    """Cria callback COM para UpdateNotify/Disconnect/Heartbeat."""
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


def conectar_servidor():
    """Cria servidor RTD e retorna (srv, IRTDUpdateEvent)."""
    mod = _carregar_interfaces()
    srv = comtypes.client.CreateObject(PROG_ID)
    log.info(f"Servidor criado: {type(srv).__name__}")
    return srv, mod.IRTDUpdateEvent


def _connect(srv, strings):
    """Conecta um tópico RTD (ConnectData). Retorna (tid, valor_inicial)."""
    tid = next(_next_tid)
    gnv = c_int(1)
    out = srv.ConnectData(tid, list(strings), byref(gnv))
    valor = out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else None
    return tid, valor


def _refresh(srv):
    """Chama RefreshData no servidor RTD."""
    try:
        return srv.RefreshData()
    except Exception:
        try:
            n = c_long(0)
            return srv.RefreshData(n)
        except Exception:
            n = c_long(0)
            return srv.RefreshData(byref(n))


# ============================================================================
# DESCOBERTA DE ATIVOS
# ============================================================================

def descobrir_ativos_rtd(ativos_config, max_janelas=MAX_JANELAS_RTD):
    """Descobre BOOKn/T&Tn validos usando INFO/ATV + RefreshData.

    CRITICO: BOOK e T&T sao sequencias INDEPENDENTES no ProfitChart RTD.
    BOOK0 nao necessariamente pertence ao mesmo ativo que T&T0.
    Fazemos o pareamento por SIMBOLO (INFO/ATV), nunca por indice.

    Args:
        ativos_config: lista de símbolos esperados (ex: ["INDV26", "WINV26"])
        max_janelas: número máximo de janelas RTD para testar

    Returns:
        lista de dicts [{"simbolo": "WINV26", "book": "BOOK1", "tt": "T&T0"}, ...]
    """
    from adapters.rtd_parser import parse_refresh_data

    comtypes.CoInitialize()
    srv = None
    tids = []
    info_map = {}
    book_map = {}
    tt_map = {}
    try:
        srv, IRTDUpdateEvent = conectar_servidor()
        notify = threading.Event()
        disc = threading.Event()
        cb = _criar_callback(IRTDUpdateEvent, notify, disc)
        srv.ServerStart(cb)

        for i in range(max_janelas):
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

        prioridade = {s.upper(): idx for idx, s in enumerate(ativos_config)}
        saida = []
        vistos = set()

        for simbolo_conf in ativos_config:
            sym_upper = simbolo_conf.upper()
            if sym_upper in vistos:
                continue
            vistos.add(sym_upper)

            book_idx = None
            for idx, sym in book_map.items():
                if sym == sym_upper:
                    book_idx = idx
                    break

            tt_idx = None
            for idx, sym in tt_map.items():
                if sym == sym_upper:
                    tt_idx = idx
                    break

            if book_idx is None and tt_idx is None:
                log.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: nenhuma janela RTD encontrada.")
                continue

            if book_idx is not None and tt_idx is not None and book_idx != tt_idx:
                log.info(
                    f"[AUTO-DISCOVERY] {simbolo_conf}: BOOK{book_idx} + T&T{tt_idx} "
                    f"(indices diferentes — pareamento por simbolo OK)"
                )
            elif book_idx is None:
                log.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: somente T&T{tt_idx} encontrado (sem BOOK).")
            elif tt_idx is None:
                log.warning(f"[AUTO-DISCOVERY] {simbolo_conf}: somente BOOK{book_idx} encontrado (sem T&T).")
            else:
                log.info(f"[AUTO-DISCOVERY] {simbolo_conf}: BOOK{book_idx} + T&T{tt_idx}")

            saida.append({
                "simbolo": sym_upper,
                "book": f"BOOK{book_idx}" if book_idx is not None else None,
                "tt": f"T&T{tt_idx}" if tt_idx is not None else None,
            })

        saida.sort(key=lambda a: prioridade.get(a["simbolo"], 10_000))
        log.info(f"[AUTO-DISCOVERY] {len(saida)} ativo(s) mapeado(s) de {max_janelas} janelas testadas")
        for a in saida:
            b = a["book"] or "N/A"
            t = a["tt"] or "N/A"
            log.info(f"[AUTO-DISCOVERY]   -> {a['simbolo']}: book={b}, tt={t}")
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


def preparar_ativos(ativos_config, auto_discover=True):
    """Prepara lista de ativos (auto-discover ou fallback manual)."""
    if auto_discover:
        encontrados = descobrir_ativos_rtd(ativos_config)
        if encontrados:
            log.info(f"[AUTO-DISCOVERY] {len(encontrados)} ativo(s) selecionado(s).")
            return encontrados
    fallback = [{"simbolo": s, "book": f"BOOK{i}", "tt": f"T&T{i}"} for i, s in enumerate(ativos_config)]
    log.warning("[AUTO-DISCOVERY] Fallback para configuracao manual.")
    return fallback


# ============================================================================
# DIAGNÓSTICO
# ============================================================================

def diagnosticar_rtd(ativos):
    """Roda diagnóstico completo das janelas RTD."""
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

        for a_idx, ativo in enumerate(ativos):
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
        for a_idx, ativo in enumerate(ativos):
            st = status[a_idx]
            simbolo = ativo["simbolo"]
            if st["book_info_ok"] and st["tt_info_ok"] and st["book_ok"] == st["book_total"] and st["tt_ok"] == st["tt_total"]:
                print(f"[OK] ATIVO {a_idx} ({simbolo}): book e T&T conectados corretamente.")
                continue
            algum_problema = True
            if not st["book_info_ok"] and not st["tt_info_ok"]:
                print(f"[FALHA] ATIVO {a_idx} ({simbolo}): NENHUMA janela RTD encontrada "
                      f"para {ativo['book']}/{ativo['tt']}. Abra uma janela de Book de Ofertas "
                      f"para {simbolo} no ProfitChart.")
            else:
                if not st["book_info_ok"] or st["book_ok"] < st["book_total"]:
                    print(f"[FALHA] ATIVO {a_idx} ({simbolo}): painel de BOOK "
                          f"({ativo['book']}) nao exportando dados ({st['book_ok']}/{st['book_total']}).")
                if not st["tt_info_ok"] or st["tt_ok"] < st["tt_total"]:
                    print(f"[FALHA] ATIVO {a_idx} ({simbolo}): painel de T&T "
                          f"({ativo['tt']}) nao exportando dados ({st['tt_ok']}/{st['tt_total']}).")
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
