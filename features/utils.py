# -*- coding: utf-8 -*-
"""
features/utils.py — Funções puras partilhadas por todos os módulos de features.
"""

import math
import re
from datetime import date, datetime


def ewma_update(anterior, valor, alpha):
    """Média móvel exponencial de um passo — sem estado próprio."""
    return alpha * valor + (1 - alpha) * anterior


def hhi(volumes):
    """Herfindahl-Hirschman Index de concentração. 0 = pulverizado, 1 = monopólio."""
    total = sum(volumes)
    if total <= 0:
        return 0.0
    return sum((v / total) ** 2 for v in volumes)


def entropia(volumes):
    """Entropia de Shannon da distribuição de volume."""
    total = sum(volumes)
    if total <= 0:
        return 0.0
    h = 0.0
    for v in volumes:
        if v > 0:
            p = v / total
            h -= p * math.log(p)
    return h


def idade_ms(ts_referencia, ts_fonte):
    """Diferença em ms entre timestamps (para asof join)."""
    if ts_fonte is None:
        return None
    return max(0, ts_referencia - ts_fonte)


MESES_B3 = {'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6, 'N': 7,
            'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12}


def dias_ate_vencimento(simbolo, hoje=None):
    """Proxy de dias até o vencimento do contrato B3 (WIN/WDO)."""
    m = re.search(r'([FGHJKMNQUVXZ])(\d{2})', str(simbolo or '').upper())
    if not m:
        return None
    mes = MESES_B3[m.group(1)]
    ano = 2000 + int(m.group(2))
    venc = date(ano, mes, 15)
    hoje = hoje or date.today()
    return (venc - hoje).days


def fase_sessao(tod_ms, abertura_fim=(10, 0), almoco_inicio=(12, 0),
                almoco_fim=(13, 30), fechamento=(16, 30)):
    """Fase da sessão B3 a partir de time-of-day em ms."""
    h = (tod_ms // 3600000) % 24
    mi = (tod_ms % 3600000) // 60000
    t = h * 60 + mi
    ab = abertura_fim[0] * 60 + abertura_fim[1]
    ai = almoco_inicio[0] * 60 + almoco_inicio[1]
    af = almoco_fim[0] * 60 + almoco_fim[1]
    fc = fechamento[0] * 60 + fechamento[1]
    if t < ab:
        return 'abertura'
    if t < ai:
        return 'meio'
    if t < af:
        return 'almoco'
    if t < fc:
        return 'meio'
    return 'fechamento'


_OFFSET_LOCAL_UTC_MS = None


def _offset_local_utc_ms():
    """Deslocamento (ms) que a hora local adianta da UTC."""
    global _OFFSET_LOCAL_UTC_MS
    if _OFFSET_LOCAL_UTC_MS is None:
        ag = datetime.now().astimezone()
        _OFFSET_LOCAL_UTC_MS = int(ag.utcoffset().total_seconds() * 1000) if ag.utcoffset() else 0
    return _OFFSET_LOCAL_UTC_MS


def _sanitize(v):
    """Substitui NaN/Inf por 0.0."""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return 0.0
    return v


def _tod_de_ts(ts_ms):
    """Normaliza timestamp para time-of-day em ms (hora local da B3)."""
    if ts_ms and ts_ms > 1e11:
        utc_tod = ts_ms % 86400000
        return (utc_tod + _offset_local_utc_ms()) % 86400000
    return ts_ms or 0


INSTITUCIONAIS = {
    'UBS', 'JPM', 'CITI', 'BRADESKIM', 'MERRILL', 'BofA', 'FAST',
    'SUSQUEHANNA', 'IMC', 'OPTIVER', 'FLOW', 'AKuna', 'VIRTU', 'BGC',
    'BNP', 'BARCLAYS', 'GOLDMAN', 'MORGAN', 'HSBC', 'DEUTSCHE',
    'CREDIT', 'NOMURA', 'BB', 'BTG', 'XP', 'SAFRA', 'SANTANDER',
    'ITAU', 'BRADESCO', 'CAIXA', 'JCG', 'AGORAC', 'TREAURY',
    'MSIF', 'UBSBB', 'BREL', 'WON', 'TICKER', 'B3', 'CMA',
    'BRASIL', 'BNDES', 'MORGAN ST', 'BESI', 'PLAU', 'PLAU'
}


def classificar_corretora(broker):
    """Classifica corretora como 'inst' ou 'varejo' baseado no nome."""
    if not broker:
        return 'varejo'
    broker_upper = str(broker).upper().strip()
    for inst in INSTITUCIONAIS:
        if inst in broker_upper:
            return 'inst'
    return 'varejo'


def asof_join_linhas(linhas_principal, linhas_contexto, tolerancia_ms=100):
    """Alinhamento WIN × WDO por proximidade temporal (sem pandas)."""
    resultado = []
    i_ctx = 0
    ultimo_ctx = None
    n_ctx = len(linhas_contexto)

    for linha in linhas_principal:
        ts = linha['ts_ms']
        while i_ctx < n_ctx and linhas_contexto[i_ctx]['ts_ms'] <= ts:
            ultimo_ctx = linhas_contexto[i_ctx]
            i_ctx += 1

        saida = dict(linha)
        if ultimo_ctx is not None and (ts - ultimo_ctx['ts_ms']) <= tolerancia_ms:
            for k, v in ultimo_ctx.items():
                if k == 'ts_ms':
                    continue
                saida[f'{k}_ctx'] = v
            saida['ctx_idade_ms'] = idade_ms(ts, ultimo_ctx['ts_ms'])
        else:
            saida['ctx_idade_ms'] = None
        resultado.append(saida)

    return resultado
