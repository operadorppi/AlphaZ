# -*- coding: utf-8 -*-
"""
adapters/rtd_parser.py — Parsing de dados RTD do ProfitChart.

Responsabilidades extraídas do motor_web.py:
  - parse_refresh_data: extrai pares (tid, valor) do RefreshData
  - parse_dat: converte string de data/hora RTD para datetime
  - _parse_hora_manual: parser de hora com microsegundos
  - enforce_schema: garante schema do DataFrame antes de escrever Parquet
  - _is_iterable: helper para checar se objeto é iterável

Fluxo:
  ProfitChart RTD → rtd_connection → rtd_parser → rtd_writer → dashboard
"""

import math
import logging
from datetime import datetime, timedelta

import pandas as pd

log = logging.getLogger(__name__)

# Timezone de Brasília (usado em enforce_schema)
try:
    from zoneinfo import ZoneInfo
    TZ_BR = ZoneInfo("America/Sao_Paulo")
except ImportError:
    TZ_BR = None


# ============================================================================
# PARSER REFRESHDATA
# ============================================================================

def _is_iterable(obj):
    """Verifica se o objeto é iterável (exclui str e bytes)."""
    try:
        return hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes))
    except Exception:
        return False


def parse_refresh_data(data):
    """Extrai pares (topic_id, valor) do retorno do RefreshData().

    O formato do RTD pode variar entre versões do ProfitChart.
    Esta função normaliza todos os formatos conhecidos em uma lista de pares.

    Args:
        data: retorno do srv.RefreshData()

    Returns:
        lista de tuplas [(tid: int, valor: any), ...]
    """
    if data is None:
        return []
    flat = list(data)
    if not flat:
        return []
    pairs = []

    # Formato 1: ((tids_array, values_array),) — comum em versões recentes
    if len(flat) == 2 and isinstance(flat[1], tuple) and len(flat[1]) == 2:
        tids, vals = flat[1]
        if _is_iterable(tids) and _is_iterable(vals):
            try:
                for tid, val in zip(tids, vals):
                    pairs.append((int(tid), val))
                return pairs
            except Exception:
                pass

    # Formato 2: [[tid, val], [tid, val], ...]
    if flat and isinstance(flat[0], (list, tuple)):
        for r in flat:
            try:
                if r is not None and len(r) >= 2:
                    pairs.append((int(r[0]), r[1]))
            except Exception:
                continue
        return pairs

    # Formato 3: [tid1, val1, tid2, val2, ...]
    if len(flat) % 2 == 0:
        for i in range(0, len(flat), 2):
            try:
                pairs.append((int(flat[i]), flat[i + 1]))
            except Exception:
                continue
        return pairs

    log.warning(f"Formato RefreshData nao reconhecido: {flat!r}")
    return []


# ============================================================================
# PARSER DE DATA/HORA
# ============================================================================

def _parse_hora_manual(hora_str, ano, mes, dia):
    """Parse manual de string de hora (HH:MM:SS.mmm) com microsegundos."""
    principal, _, frac = hora_str.partition(".")
    h_str, m_str, sec_str = principal.split(":")
    h, m, sec = int(h_str), int(m_str), int(sec_str)
    micro = 0
    if frac:
        micro = int((frac + "000000")[:6])
    return datetime(ano, mes, dia, h, m, sec, micro)


def parse_dat(s, dia_ref):
    """Converte string de data/hora RTD para datetime.

    Suporta formatos:
      - "DD/MM/AAAA HH:MM:SS.mmm"
      - "AAAA-MM-DD HH:MM:SS.mmm"
      - "HH:MM:SS.mmm" (usa dia_ref como data)

    Args:
        s: string de data/hora do RTD
        dia_ref: datetime de referência para data

    Returns:
        (datetime, inferida: bool) — inferida=True quando data veio do dia_ref
    """
    from adapters.rtd_connection import sstr
    s = sstr(s)
    if not s:
        return None, False
    base = dia_ref.replace(tzinfo=None) if getattr(dia_ref, "tzinfo", None) is not None else dia_ref

    try:
        # Formato DD/MM/AAAA HH:MM:SS
        if "/" in s:
            data_str, _, hora_str = s.partition(" ")
            if not hora_str:
                return None, False
            dia_s, mes_s, ano_s = data_str.split("/")
            return _parse_hora_manual(hora_str, int(ano_s), int(mes_s), int(dia_s)), False

        # Formato AAAA-MM-DD HH:MM:SS
        if "-" in s:
            data_str, _, hora_str = s.partition(" ")
            if not hora_str:
                return None, False
            ano_s, mes_s, dia_s = data_str.split("-")
            return _parse_hora_manual(hora_str, int(ano_s), int(mes_s), int(dia_s)), False

        # Formato HH:MM:SS (apenas hora — data inferida)
        dt = _parse_hora_manual(s, base.year, base.month, base.day)
        if dt > base + timedelta(hours=12):
            dt -= timedelta(days=1)
        return dt, True

    except (ValueError, IndexError):
        return None, False


# parse_hms_ms: wrapper para uso no profit_rtd.py
def parse_hms_ms(hora_str):
    """Converte HH:MM:SS.mmm para milliseconds desde midnight."""
    from adapters.rtd_connection import sstr
    s = sstr(hora_str)
    if not s:
        return 0
    try:
        principal, _, frac = s.partition(".")
        h_str, m_str, sec_str = principal.split(":")
        ms = int(h_str) * 3600000 + int(m_str) * 60000 + int(sec_str) * 1000
        if frac:
            ms += int((frac + "000")[:3])
        return ms
    except Exception:
        return 0


# ============================================================================
# ENFORCE SCHEMA
# ============================================================================

def enforce_schema(df, schema):
    """Garante que o DataFrame possui todas as colunas do schema com os tipos corretos.

    Colunas faltantes são preenchidas com valores padrão.
    Tipos inconsistentes são convertidos.

    Args:
        df: DataFrame de entrada
        schema: dict {nome_coluna: dtype_string}

    Returns:
        DataFrame com schema garantido, apenas com colunas do schema
    """
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
            log.warning(f"[SCHEMA] Falha coluna '{col}' -> {dtype}: {e}")
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
