#!/usr/bin/env python3
"""
labeler_core.py — Implementacao de referencia pura do Triple Barrier labeling.

Usado como fonte canônica da verdade e para testes formais de invariantes.
"""

from enum import IntEnum
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any


class LabelOutcome(IntEnum):
    TP = 1
    SL = -1
    TIMEOUT = 0
    AMBIGUOUS = -99


AMBIGUOUS = LabelOutcome.AMBIGUOUS


@dataclass
class LabelResult:
    outcome: LabelOutcome
    preco_saida: float
    duracao_ms: int
    retorno_pts: float
    tp_atingido: bool
    sl_atingido: bool
    ambiguous: bool


def label_ponto_ref(precos: np.ndarray, i: int,
                    tp_pts: float, sl_pts: float,
                    max_holding_ms: int, ts_ms: Optional[np.ndarray] = None,
                    tick_ms: int = 100,
                    seg_fim: Optional[int] = None,
                    direction: int = 1) -> LabelResult:
    """Calcula o label canônico para um unico ponto i.

    P0-A33 (v15.30): quando `ts_ms` e fornecido, o horizonte de holding e
    TEMPO REAL (ts[i] + max_holding_ms) e os eventos dentro desse intervalo
    sao varridos — duracao_ms e o delta REAL de timestamps, nunca
    "linhas * tick_ms" (dados RAW sao irregulares; N linhas != N*100ms).
    Quando `ts_ms` e None, mantem a semantica LEGACY por contagem de linhas
    (usada pelas APIs puras por indice nos testes de invariante) — nunca
    usar em pipeline real.
    """
    P0 = float(precos[i])
    n = len(precos)
    limite = n if seg_fim is None else min(n, seg_fim)

    # ----------------------------------------------------------
    # SEM ts_ms: legado por contagem de linhas (regras puras por indice)
    # ----------------------------------------------------------
    if ts_ms is None:
        ahead_ticks = max_holding_ms // tick_ms
        max_dt = min(ahead_ticks, limite - i - 1)

        if max_dt <= 0:
            return LabelResult(
                outcome=LabelOutcome.TIMEOUT,
                preco_saida=P0,
                duracao_ms=0 if max_holding_ms == 0 else min(
                    max_holding_ms, max(0, max_dt * tick_ms)),
                retorno_pts=0.0,
                tp_atingido=False,
                sl_atingido=False,
                ambiguous=False,
            )

        if direction == 1:
            tp_barrier = P0 + tp_pts
            sl_barrier = P0 - sl_pts
        else:
            tp_barrier = P0 - tp_pts
            sl_barrier = P0 + sl_pts

        tick_tp = None
        tick_sl = None
        preco_tp = P0
        preco_sl = P0

        for dt in range(1, max_dt + 1):
            P = float(precos[i + dt])
            if direction == 1:
                if tick_tp is None and P >= tp_barrier:
                    tick_tp = dt
                    preco_tp = P
                if tick_sl is None and P <= sl_barrier:
                    tick_sl = dt
                    preco_sl = P
            else:
                if tick_tp is None and P <= tp_barrier:
                    tick_tp = dt
                    preco_tp = P
                if tick_sl is None and P >= sl_barrier:
                    tick_sl = dt
                    preco_sl = P

            if tick_tp is not None and tick_sl is not None:
                break

        if tick_tp is not None and tick_sl is not None:
            if tick_tp == tick_sl:
                return LabelResult(
                    outcome=LabelOutcome.AMBIGUOUS,
                    preco_saida=(preco_tp + preco_sl) / 2.0,
                    duracao_ms=tick_tp * tick_ms,
                    retorno_pts=0.0,
                    tp_atingido=False,
                    sl_atingido=False,
                    ambiguous=True,
                )
            elif tick_tp < tick_sl:
                ret = (preco_tp - P0) if direction == 1 else (P0 - preco_tp)
                return LabelResult(
                    outcome=LabelOutcome.TP,
                    preco_saida=preco_tp,
                    duracao_ms=tick_tp * tick_ms,
                    retorno_pts=ret,
                    tp_atingido=True,
                    sl_atingido=False,
                    ambiguous=False,
                )
            else:
                ret = (preco_sl - P0) if direction == 1 else (P0 - preco_sl)
                return LabelResult(
                    outcome=LabelOutcome.SL,
                    preco_saida=preco_sl,
                    duracao_ms=tick_sl * tick_ms,
                    retorno_pts=ret,
                    tp_atingido=False,
                    sl_atingido=True,
                    ambiguous=False,
                )
        elif tick_tp is not None:
            ret = (preco_tp - P0) if direction == 1 else (P0 - preco_tp)
            return LabelResult(
                outcome=LabelOutcome.TP,
                preco_saida=preco_tp,
                duracao_ms=tick_tp * tick_ms,
                retorno_pts=ret,
                tp_atingido=True,
                sl_atingido=False,
                ambiguous=False,
            )
        elif tick_sl is not None:
            ret = (preco_sl - P0) if direction == 1 else (P0 - preco_sl)
            return LabelResult(
                outcome=LabelOutcome.SL,
                preco_saida=preco_sl,
                duracao_ms=tick_sl * tick_ms,
                retorno_pts=ret,
                tp_atingido=False,
                sl_atingido=True,
                ambiguous=False,
            )
        else:
            return LabelResult(
                outcome=LabelOutcome.TIMEOUT,
                preco_saida=P0,
                duracao_ms=max_dt * tick_ms,
                retorno_pts=0.0,
                tp_atingido=False,
                sl_atingido=False,
                ambiguous=False,
            )

    # ----------------------------------------------------------
    # COM ts_ms: horizonte por TIMESTAMP REAL (P0-A33)
    # ----------------------------------------------------------
    t0 = int(ts_ms[i])
    horizonte_ts = t0 + max_holding_ms

    if direction == 1:
        tp_barrier = P0 + tp_pts
        sl_barrier = P0 - sl_pts
    else:
        tp_barrier = P0 - tp_pts
        sl_barrier = P0 + sl_pts

    idx_tp = None
    idx_sl = None
    preco_tp = P0
    preco_sl = P0
    j = i + 1
    while j < limite and int(ts_ms[j]) <= horizonte_ts:
        P = float(precos[j])
        if direction == 1:
            if idx_tp is None and P >= tp_barrier:
                idx_tp = j
                preco_tp = P
            if idx_sl is None and P <= sl_barrier:
                idx_sl = j
                preco_sl = P
        else:
            if idx_tp is None and P <= tp_barrier:
                idx_tp = j
                preco_tp = P
            if idx_sl is None and P >= sl_barrier:
                idx_sl = j
                preco_sl = P
        if idx_tp is not None and idx_sl is not None:
            break
        j += 1

    def _dur(idx):
        return max(0, int(ts_ms[idx]) - t0)

    if idx_tp is not None and idx_sl is not None:
        if idx_tp == idx_sl:
            return LabelResult(
                outcome=LabelOutcome.AMBIGUOUS,
                preco_saida=(preco_tp + preco_sl) / 2.0,
                duracao_ms=_dur(idx_tp),
                retorno_pts=0.0,
                tp_atingido=False,
                sl_atingido=False,
                ambiguous=True,
            )
        elif idx_tp < idx_sl:
            ret = (preco_tp - P0) if direction == 1 else (P0 - preco_tp)
            return LabelResult(
                outcome=LabelOutcome.TP,
                preco_saida=preco_tp,
                duracao_ms=_dur(idx_tp),
                retorno_pts=ret,
                tp_atingido=True,
                sl_atingido=False,
                ambiguous=False,
            )
        else:
            ret = (preco_sl - P0) if direction == 1 else (P0 - preco_sl)
            return LabelResult(
                outcome=LabelOutcome.SL,
                preco_saida=preco_sl,
                duracao_ms=_dur(idx_sl),
                retorno_pts=ret,
                tp_atingido=False,
                sl_atingido=True,
                ambiguous=False,
            )
    elif idx_tp is not None:
        ret = (preco_tp - P0) if direction == 1 else (P0 - preco_tp)
        return LabelResult(
            outcome=LabelOutcome.TP,
            preco_saida=preco_tp,
            duracao_ms=_dur(idx_tp),
            retorno_pts=ret,
            tp_atingido=True,
            sl_atingido=False,
            ambiguous=False,
        )
    elif idx_sl is not None:
        ret = (preco_sl - P0) if direction == 1 else (P0 - preco_sl)
        return LabelResult(
            outcome=LabelOutcome.SL,
            preco_saida=preco_sl,
            duracao_ms=_dur(idx_sl),
            retorno_pts=ret,
            tp_atingido=False,
            sl_atingido=True,
            ambiguous=False,
        )
    else:
        # TIMEOUT: sem barreira dentro do holding real. Duracao = tempo REAL
        # ate o ultimo evento dentro da janela (ou 0 sem eventos futuros).
        ultimo = j - 1
        dur = _dur(ultimo) if ultimo > i else 0
        return LabelResult(
            outcome=LabelOutcome.TIMEOUT,
            preco_saida=P0,
            duracao_ms=dur,
            retorno_pts=0.0,
            tp_atingido=False,
            sl_atingido=False,
            ambiguous=False,
        )


def _segmentos(ts_ms: np.ndarray, ativos: np.ndarray) -> List[int]:
    inicios = [0]
    dias = ts_ms // 86400000
    n = len(ts_ms)
    for i in range(1, n):
        if ativos[i] != ativos[i - 1] or dias[i] != dias[i - 1]:
            inicios.append(i)
    if not inicios or inicios[-1] != n:
        inicios.append(n)
    return inicios


def label_array_ref(precos: np.ndarray, ts_ms: np.ndarray, ativos: np.ndarray,
                    tp_pts: float = 100.0, sl_pts: float = 50.0,
                    max_holding_s: int = 30, tick_ms: int = 100,
                    purge_s: int = 0, min_vol: Optional[np.ndarray] = None) -> Dict[str, np.ndarray]:
    """Processamento de referencia para arrays completos."""
    n = len(precos)
    max_holding_ms = max_holding_s * 1000
    purge_ms = purge_s * 1000

    labels = np.full(n, int(LabelOutcome.TIMEOUT), dtype=np.int32)
    outcome_raw = np.full(n, int(LabelOutcome.TIMEOUT), dtype=np.int32)
    preco_saida = precos.copy().astype(np.float64)
    duracao_ms = np.zeros(n, dtype=np.int64)
    retorno_pts = np.zeros(n, dtype=np.float64)
    tp_atingido = np.zeros(n, dtype=bool)
    sl_atingido = np.zeros(n, dtype=bool)
    ambiguous = np.zeros(n, dtype=bool)

    segs = _segmentos(ts_ms, ativos)

    for s in range(len(segs) - 1):
        seg_ini = segs[s]
        seg_fim = segs[s + 1]

        for i in range(seg_ini, seg_fim):
            res = label_ponto_ref(precos, i, tp_pts=tp_pts, sl_pts=sl_pts,
                                  max_holding_ms=max_holding_ms, ts_ms=ts_ms,
                                  tick_ms=tick_ms, seg_fim=seg_fim)
            outcome_raw[i] = int(res.outcome)
            labels[i] = 0 if res.outcome == LabelOutcome.AMBIGUOUS else int(res.outcome)
            preco_saida[i] = res.preco_saida
            duracao_ms[i] = res.duracao_ms
            retorno_pts[i] = res.retorno_pts
            tp_atingido[i] = res.tp_atingido
            sl_atingido[i] = res.sl_atingido
            ambiguous[i] = res.ambiguous

    if purge_s > 0:
        ultimo_fim_ts = -999999999
        seg_inicios = set(segs)
        for i in range(n):
            if i in seg_inicios:
                ultimo_fim_ts = -999999999
            ts = ts_ms[i]
            if ts - ultimo_fim_ts < purge_ms:
                labels[i] = int(LabelOutcome.TIMEOUT)
                outcome_raw[i] = int(LabelOutcome.TIMEOUT)
                duracao_ms[i] = 0
                preco_saida[i] = precos[i]
                sl_atingido[i] = False
                tp_atingido[i] = False
                ambiguous[i] = False
                continue
            if labels[i] != 0 or sl_atingido[i]:
                ultimo_fim_ts = ts + duracao_ms[i]

    return {
        'ts_ms': ts_ms,
        'label': labels,
        'outcome_raw': outcome_raw,
        'preco_entrada': precos,
        'preco_saida': preco_saida,
        'duracao_ms': duracao_ms,
        'retorno_pts': retorno_pts,
        'ativo': ativos,
        'tp_atingido': tp_atingido,
        'sl_atingido': sl_atingido,
        'ambiguous': ambiguous,
    }


def validar_equivalencia(precos: np.ndarray, ts_ms: np.ndarray, ativos: np.ndarray,
                         tp_pts: float, sl_pts: float,
                         max_holding_s: int = 30) -> Tuple[bool, List[str]]:
    """Valida equivalencia entre label_array_ref e label_vectorizado."""
    from labeler_vectorizado import label_vectorizado
    ref = label_array_ref(precos, ts_ms, ativos, tp_pts=tp_pts, sl_pts=sl_pts, max_holding_s=max_holding_s)
    vec = label_vectorizado(precos, ts_ms, ativos, tp_pts=tp_pts, sl_pts=sl_pts, max_holding_s=max_holding_s)

    divergencias = []
    for k in ['label', 'outcome_raw', 'duracao_ms', 'tp_atingido', 'sl_atingido', 'ambiguous']:
        if not np.array_equal(ref[k], vec[k]):
            diff_idx = np.where(ref[k] != vec[k])[0]
            divergencias.append(f"Chave '{k}' diverge nos indices {diff_idx[:5]}: ref={ref[k][diff_idx[:5]]} vs vec={vec[k][diff_idx[:5]]}")

    for k in ['preco_saida', 'retorno_pts']:
        if not np.allclose(ref[k], vec[k], atol=1e-5):
            diff_idx = np.where(~np.isclose(ref[k], vec[k], atol=1e-5))[0]
            divergencias.append(f"Chave '{k}' diverge nos indices {diff_idx[:5]}: ref={ref[k][diff_idx[:5]]} vs vec={vec[k][diff_idx[:5]]}")

    return len(divergencias) == 0, divergencias
