# -*- coding: utf-8 -*-
"""
adapters/rtd_writer.py — Escrita de dados RTD em Parquet.

Responsabilidades extraídas do motor_web.py:
  - thread_escritora: writer de BOOK (Parquet parts por hora/ativo)
  - thread_escritora_tt: writer de T&T (Parquet parts por hora/ativo)
  - Schemas (BOOK_SCHEMA, TT_SCHEMA)
  - Parquet helpers (_quarentena, _escrever_atomico, write_parquet_part)
  - Consolidação (consolidar_book_parquet, consolidar_tt_parquet)
  - Estatísticas (_registrar_stat, _registrar_book, _registrar_tt)
  - Limpeza (limpar_pasta)

Fluxo:
  ProfitChart RTD → rtd_connection → rtd_parser → rtd_writer → dashboard
"""

import os
import sys
import time
import json
import glob
import shutil
import logging
import threading
import multiprocessing
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty

import pandas as pd

try:
    import pyarrow
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

from adapters.rtd_connection import NIVEIS_BOOK, TT_FIELDS, agora_br
from adapters.rtd_parser import enforce_schema

# ============================================================================
# VALIDAÇÃO DE TIMESTAMP (v11.2)
# ============================================================================

# Pregão B3: 09:00 - 18:30 (horário de Brasília)
_PREGAO_INICIO_HORA = 9
_PREGAO_FIM_HORA = 18
_PREGAO_FIM_MINUTO = 30

# Tolerância máxima: timestamp não pode estar mais de 30s no futuro
# ou 5min no passado (relativo ao relógio do sistema)
_TS_FUTURO_MAX_S = 30
_TS_PASSADO_MAX_S = 300


def _validar_timestamp_ms(tms, nome_thread=""):
    """Valida um timestamp em milissegundos antes de gravar no Parquet.

    Rejeita:
      - time_ms = 0 ou negativo
      - time_ms com diferença > 30s do futuro (clock corrompido)
      - time_ms com diferença > 5min do passado (replay/dado antigo)
      - time_ms fora do horário de pregão (09:00-18:30 BRT)

    Args:
        tms: timestamp em milissegundos (epoch ou time-of-day)
        nome_thread: nome da thread para logging

    Returns:
        True se válido, False se deve ser rejeitado
    """
    # 1. Zero ou negativo
    if tms <= 0:
        log.warning(f"[{nome_thread}] TS rejeitado: time_ms={tms} (zero/negativo)")
        return False

    # 2. Converter para datetime BRT para validar horário
    try:
        dt = datetime.fromtimestamp(tms / 1000.0)
    except (OSError, OverflowError, ValueError):
        log.warning(f"[{nome_thread}] TS rejeitado: time_ms={tms} (conversão impossível)")
        return False

    # 3. Muito no futuro (> 30s do relógio do sistema)
    agora_epoch_s = time.time()
    tms_epoch_s = tms / 1000.0
    if tms_epoch_s > agora_epoch_s + _TS_FUTURO_MAX_S:
        log.warning(
            f"[{nome_thread}] TS rejeitado: time_ms={tms} "
            f"({dt}) está {tms_epoch_s - agora_epoch_s:.0f}s no futuro"
        )
        return False

    # 4. Muito no passado (> 5min do relógio do sistema)
    if tms_epoch_s < agora_epoch_s - _TS_PASSADO_MAX_S:
        log.warning(
            f"[{nome_thread}] TS rejeitado: time_ms={tms} "
            f"({dt}) está {agora_epoch_s - tms_epoch_s:.0f}s no passado"
        )
        return False

    # 5. Fora do horário de pregão (09:00 - 18:30 BRT)
    #    Nota: só valida se o timestamp for epoch (não time-of-day)
    #    time-of-day tem valor < 86400000 (24h em ms)
    if tms > 86400000:  # Provavelmente epoch
        hora = dt.hour
        minuto = dt.minute
        hora_min = hora * 60 + minuto
        inicio = _PREGAO_INICIO_HORA * 60
        fim = _PREGAO_FIM_HORA * 60 + _PREGAO_FIM_MINUTO
        if hora_min < inicio or hora_min > fim:
            # Em replay pode ter dados fora do pregão — só avisa, não rejeita
            log.debug(
                f"[{nome_thread}] TS fora do pregão: time_ms={tms} "
                f"({dt.strftime('%H:%M')}) — mantido (pode ser replay)"
            )
            # Não rejeita — replay pode ter dados antes/depois do pregão

    return True

log = logging.getLogger(__name__)


# ============================================================================
# CONFIGURAÇÕES DE ESCRITA
# ============================================================================

INTERVALO_SALVAMENTO_S = 60
FSYNC_INTERVALO_S = 300
PARQUET_ENGINE = "pyarrow"
PARQUET_COMPRESSION = "snappy"
MAX_FILA = 1_000_000

# Contadores de live stats
LIVE_FIELDS = ("book_capturados", "book_gravados", "tt_detectados", "tt_gravados", "drops", "falhas_gravacao")
LIVE_FIELD_INDEX = {name: i for i, name in enumerate(LIVE_FIELDS)}


def _live_inc(live_stats, a_idx, campo, n=1):
    """Incrementa contador de live stats (memória compartilhada entre processos)."""
    if live_stats is None or a_idx is None:
        return
    try:
        live_stats[a_idx * len(LIVE_FIELDS) + LIVE_FIELD_INDEX[campo]] += int(n)
    except Exception:
        pass


def _live_get(live_stats, a_idx, campo):
    """Lê contador de live stats."""
    if live_stats is None:
        return 0
    try:
        return int(live_stats[a_idx * len(LIVE_FIELDS) + LIVE_FIELD_INDEX[campo]])
    except Exception:
        return 0


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


# ============================================================================
# PARQUET HELPERS
# ============================================================================

def _quarentena_arquivo(caminho, motivo):
    """Isola arquivo corrompido renomeando para .bak."""
    if not os.path.exists(caminho):
        return
    ts = int(time.time())
    pid = os.getpid()
    bak = f"{caminho}.{motivo}.{ts}.{pid}.bak"
    try:
        os.replace(caminho, bak)
        log.warning(f"[PARQUET] Isolado: {caminho} -> {bak}")
    except Exception as e:
        log.error(f"[PARQUET] Nao foi possivel isolar {caminho}: {e}")
        try:
            if os.path.getsize(caminho) == 0:
                os.remove(caminho)
        except Exception:
            pass


def _escrever_parquet_atomico(caminho, df):
    """Escreve DataFrame em Parquet de forma atômica (tmp + rename)."""
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
    """Escreve uma partição Parquet (uma hora, um ativo, um tipo).

    Returns:
        True se escreveu com sucesso, False caso contrário.
    """
    if df is None or len(df) == 0:
        return True
    try:
        os.makedirs(pasta, exist_ok=True)
    except Exception as e:
        log.error(f"[PARQUET-PART] Nao foi possivel criar pasta: {e}")
        return False
    df = enforce_schema(df, schema)
    nome = f"{hora}.part_{time.time_ns()}_{os.getpid()}_{threading.get_ident()}.parquet"
    caminho = os.path.join(pasta, nome)
    try:
        _escrever_parquet_atomico(caminho, df)
        return True
    except Exception as e:
        fb = caminho.replace(".parquet", f"_ERRO_{int(time.time())}.parquet")
        log.error(f"[PARQUET-PART] Falha em {caminho}: {e} -> fallback {fb}")
        try:
            _escrever_parquet_atomico(fb, df)
        except Exception as e2:
            log.error(f"[PARQUET-PART] Fallback falhou: {e2}")
        return False


# ============================================================================
# FLUSH TRANACIONAL COM RETRY (B4)
# ============================================================================

def flush_buffers_with_retry(buffers, write_fn, logger=None):
    """Flush dos buffers para disco com retry em falha.

    Para cada buffer, grava e só remove as rows que foram escritas.
    Rows que falharam voltam ao buffer para retry no próximo ciclo.

    Args:
        buffers: dict {key: [rows...]} — modificado in-place
        write_fn: callable(key, rows) -> int (número de rows escritas)

    Returns:
        (total_ok, total_tentado)
    """
    total_ok = 0
    total_tentado = 0
    for key in list(buffers.keys()):
        rows = buffers.pop(key)
        n = write_fn(key, rows)
        total_tentado += len(rows)
        total_ok += n
        if n < len(rows):
            buffers.setdefault(key, []).extend(rows[n:])
    return total_ok, total_tentado


# ============================================================================
# THREAD ESCRITORA (BOOK)
# ============================================================================

def thread_escritora(fila, nome, prefixo, schema, base_pasta, shutdown_event, stats_lock, a_idx, live_stats):
    """Writer BOOK: particoes Parquet por hora e por ativo, com flush periodico."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    buffers = {}
    _BOOK_BUFFER_MAX = 500_000
    ultimo_flush = time.perf_counter()

    def _append_hour_file(key, rows):
        simbolo, ano, mes, dia, hora = key
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        if df.empty:
            return 0
        if "time_ms" not in df.columns:
            log.error(f"[{nome}] Lote BOOK sem 'time_ms'.")
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
                        # v11.2: Validação completa de timestamp
                        if not _validar_timestamp_ms(tms, nome):
                            _registrar_book(base_pasta, row.get("simbolo", "?"), "ts_rejeitados", stats_lock)
                            continue
                        dt = pd.Timestamp(tms, unit="ms", tz="UTC").tz_convert("America/Sao_Paulo")
                        key = (
                            str(row.get("simbolo", "")),
                            dt.strftime("%Y"), dt.strftime("%m"),
                            dt.strftime("%d"), dt.strftime("%H"),
                        )
                        buffers.setdefault(key, []).append(row)
                        total_buf = sum(len(v) for v in buffers.values())
                        if total_buf > _BOOK_BUFFER_MAX:
                            log.error(f"[{nome}] B4-BOOK overflow: {total_buf} rows — flush forcado")
                            break
                    except Exception as e:
                        log.warning(f"[{nome}] Falha ao classificar snapshot BOOK: {e}")
        except Empty:
            pass

        agora = time.perf_counter()
        deve_flush = (agora - ultimo_flush >= INTERVALO_SALVAMENTO_S) or shutdown_event.is_set()
        if not deve_flush:
            continue

        total_ok, total_tentado = flush_buffers_with_retry(buffers, _append_hour_file)
        ultimo_flush = agora
        if total_tentado:
            log.info(f"[{nome}] BOOK flush: {total_ok}/{total_tentado} regs")

    if buffers:
        total_ok, total_tentado = flush_buffers_with_retry(buffers, _append_hour_file)
        log.info(f"[{nome}] BOOK final: {total_ok}/{total_tentado} regs")

    log.info(f"[{nome}] Finalizada. Formato: Parquet parts por hora/ativo.")


# ============================================================================
# THREAD ESCRITORA (T&T)
# ============================================================================

def thread_escritora_tt(fila, nome, ativo_simbolo, schema, base_pasta, shutdown_event, stats_lock, a_idx, live_stats):
    """Writer T&T: particoes Parquet por hora e por ativo, com flush periodico."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    buffer = []
    _BUFFER_MAX = 1_000_000
    _buf_overflow_count = 0
    ultimo_salv = time.perf_counter()

    while not shutdown_event.is_set() or not fila.empty() or buffer:
        try:
            buffer.extend(fila.get(timeout=2.0))
        except Empty:
            pass
        if len(buffer) > _BUFFER_MAX:
            _buf_overflow_count += 1
            log.error(f'[{nome}] C3-BUFFER overflow: {len(buffer)} rows (flush #{_buf_overflow_count})')

        agora = time.perf_counter()
        vai_finalizar = shutdown_event.is_set()
        deve_salvar = bool(buffer) and (
            (agora - ultimo_salv >= INTERVALO_SALVAMENTO_S)
            or (len(buffer) > 500_000)
            or vai_finalizar
        )
        if not deve_salvar:
            continue

        # v11.2: Filtrar timestamps inválidos ANTES de criar DataFrame
        buffer_validos = []
        ts_rejeitados = 0
        for row in buffer:
            tms = int(row.get("time_ms", 0))
            if _validar_timestamp_ms(tms, nome):
                buffer_validos.append(row)
            else:
                ts_rejeitados += 1
        if ts_rejeitados > 0:
            _registrar_tt(base_pasta, ativo_simbolo, "ts_rejeitados", stats_lock, n=ts_rejeitados)
            _live_inc(live_stats, a_idx, "drops", ts_rejeitados)
        
        df = pd.DataFrame(buffer_validos)
        buffer = []
        ultimo_salv = agora
        if df.empty:
            continue
        if "time_ms" not in df.columns:
            log.error(f"[{nome}] Lote T&T sem 'time_ms'. Descartando.")
            continue

        df = df.sort_values("time_ms").reset_index(drop=True)
        df["dt_br"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.tz_convert("America/Sao_Paulo")
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
        log.info(
            f"[{nome}] {total_ok}/{total_tentado} regs salvos em Parquet."
            + (" (final)" if vai_finalizar else "")
        )

    log.info(f"[{nome}] Finalizada.")


# ============================================================================
# STATS (escrita em disco)
# ============================================================================

def _stats_dia_atual(dia_replay_forcado=None):
    """Retorna o dia atual para stats (replay ou live)."""
    return dia_replay_forcado or agora_br().date()


def _stats_path(base_pasta, dt):
    """Caminho do arquivo de stats do dia."""
    return os.path.join(
        base_pasta, "RAW", f"ano={dt.year:04d}", f"mes={dt.month:02d}", f"dia={dt.day:02d}",
        "_stats_captura.json",
    )


def _registrar_stat(base_pasta, campo, lock, dt=None, n=1):
    """Registra uma estatística no arquivo _stats_captura.json do dia."""
    dt = dt or agora_br().date()
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
            log.warning(f"[STATS] Falha ao gravar {caminho}: {e}")


def _stat_chave_book(ativo_simbolo, etapa):
    seguro = ''.join(c if c.isalnum() or c == '_' else '_' for c in str(ativo_simbolo))
    return f"book_{etapa}_{seguro}"


def _registrar_book(base_pasta, ativo_simbolo, etapa, lock, n=1, dt=None):
    _registrar_stat(base_pasta, _stat_chave_book(ativo_simbolo, etapa), lock, dt=dt, n=n)


def _stat_chave_tt(ativo_simbolo, etapa):
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
# CONSOLIDAÇÃO
# ============================================================================

def consolidar_book_parquet(base_pasta, dia_str):
    """Consolida partições Parquet de BOOK em arquivos únicos por hora."""
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
                log.error(f"[CONSOLIDA-BOOK] Parte corrompida {a}: {e}. Isolando.")
                _quarentena_arquivo(a, "corrompido")
        if os.path.exists(caminho_final):
            try:
                dfs.insert(0, pd.read_parquet(caminho_final, engine=PARQUET_ENGINE))
            except Exception as e:
                log.error(f"[CONSOLIDA-BOOK] Consolidado existente corrompido: {e}. Isolando.")
                _quarentena_arquivo(caminho_final, "corrompido")
        if not dfs:
            print(f"  [ERRO] {pasta}/{hora}: nenhuma parte legivel")
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
            print(f"  [ERRO] {pasta}/{hora}: {e}")
    print(f"[CONSOLIDA-BOOK] Concluido: {total_regs:,} registros consolidados para {dia_str}.")


def consolidar_tt_parquet(base_pasta, dia_str):
    """Consolida partições Parquet de T&T em arquivos únicos por hora."""
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
                log.error(f"[CONSOLIDA-TT] Parte corrompida {a}: {e}. Isolando.")
                _quarentena_arquivo(a, "corrompido")
        if os.path.exists(caminho_final):
            try:
                dfs.insert(0, pd.read_parquet(caminho_final, engine=PARQUET_ENGINE))
            except Exception as e:
                log.error(f"[CONSOLIDA-TT] Consolidado existente corrompido: {e}. Isolando.")
                _quarentena_arquivo(caminho_final, "corrompido")
        if not dfs:
            print(f"  [ERRO] {pasta}/{hora}: nenhuma parte legivel")
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
            print(f"  [ERRO] {pasta}/{hora}: {e}")
    print(f"[CONSOLIDA-TT] Concluido: {total_regs:,} registros consolidados para {dia_str}.")


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
    """Limpa arquivos temporários e invalidos da pasta de dados."""
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
    print("=" * 60)
    print(f"[LIMPEZA] Pasta base: {base}")
    print(f"  Lixo: {len(lixo)} arquivos ({_tamanho_human_readable(total_lixo)})")
    print(f"  Parquet validos: {len(parquet_validos)} ({_tamanho_human_readable(total_parquet)})")
    print("=" * 60)
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
