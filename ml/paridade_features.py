# -*- coding: utf-8 -*-
"""
ml/paridade_features.py — CONTRATO UNICO de features + paridade tripla
(P0-A31, v15.27).

O ScorerML mantem estado temporal proprio (vwaps/ctx/vol/ret/vps/mig/vrels/
inter) em paralelo ao restante do motor. Risco estrutural: a MESMA feature
conceitual calculada de forma diferente em cada pema.

Legs (mesmos eventos deterministicos):
  A = REALTIME (heuristico) — FeatureEngine/MarketState, buckets de 1s
      (nomes: aggr_imb, cvd_total, delta_preco, vol_total, ...)
  B = OFFLINE/GRID — GeradorJanelas no grid do master clock (100ms p/ o
      dataset de treino; 1000ms p/ backtest). Rows: preco_ultimo, vp{...},
      aggr_imb, cvd_*, vol_total, ...
  C = SCORER (model-side) — trackers do ScorerML (vol/ret/vps/mig/vwaps...)
      consumindo o MESMO fluxo trade-a-trade.

O modelo e treinado nas features de B (grid 100ms, transform pandas do
features_expansao) e inferido com o row de C (flatten do snap + snapshots
dos trackers). A paridade critica e B_offline ≈ C_scorer para os nomes de
mesma definicao; A alimenta a heuristica (agregacao por segundo, DIVERGENTE
por design das janelas do modelo).

CONTRATO: catalogo nome-a-nome — quem produz, definicao e classe:
  'MESMA_DEFINICAO'   -> numericamente comparavel (assert <= tolerancia)
  'AGR_DIFERENTE'     -> mesmo nome, agregacao diferente POR DESIGN (o batch
                         e por segundo-bucket; o grid/model usa janela do
                         master clock) — reportado, nunca comparado como igual
  'VP_PARALELO'       -> volume profile: B embute vp{} no snap; C expoe o
                         mesmo via VolumeProfileTracker (validado p/ igualdade)
"""

from typing import Dict, List, Tuple

# nomes vol/ret (C = trackers; offline = transform pandas sobre o grid B)
_HORIZONTES_VOL = [(1, '100ms'), (5, '500ms'), (10, '1s'), (50, '5s'),
                   (150, '15s'), (600, '1min'), (3000, '5min')]
_RETORNOS = [1, 5, 10, 50, 100, 150, 300, 500]

# VP: B usa chaves aninhadas em snap['vp']; C expoe plano. Mapa nome-a-nome.
VP_B2C = {'poc_dist': 'poc_dist', 'vah_dist': 'vah_dist',
          'val_dist': 'val_dist', 'vp_total': 'vp_total',
          'poc_acima': 'poc_acima'}

# ============================================================
# Contrato formal (fonte unica do A31)
# ============================================================

def _contrato_vol_ret():
    nomes = {}
    for _, nome in _HORIZONTES_VOL:
        nomes[f'vol_{nome}'] = 'MESMA_DEFINICAO'
    for n in _RETORNOS:
        nomes[f'retorno_{n}x100ms'] = 'MESMA_DEFINICAO'
    return nomes


CONTRATO: Dict[str, str] = {
    # mesmos nomes presentes em A/B/C com agregacao por segundo (heuristico)
    # vs janela do modelo — divergentes POR DESIGN (FASE13 ja documentava).
    # v15.28: validacao do lag do corte (nao e feature nomeada, e semantica
    # validada: snap do corte = perfil causal as-of, ultimo trade ts < corte)
    'vp_lag_1_trade': 'VP_LAG_1_TRADE',
    'aggr_imb': 'AGR_DIFERENTE',
    'cvd_total': 'AGR_DIFERENTE',
    'vol_total': 'AGR_DIFERENTE',
    'vol_compra': 'AGR_DIFERENTE',
    'vol_venda': 'AGR_DIFERENTE',
    'delta_preco': 'AGR_DIFERENTE',
    'realized_vol_bps': 'AGR_DIFERENTE',
    'range_vol_bps': 'AGR_DIFERENTE',
    # vol/ret: C (tracker incremental no grid) vs offline (pandas EWMA sobre
    # o grid de B) — mesma definicao, validada numericamente.
    **_contrato_vol_ret(),
    # VP: B embute vp{} no snap do grid; C = VolumeProfileTracker — o mesmo
    # contrato de janela (validado por igualdade nos testes).
    **{c: 'VP_PARALELO' for c in VP_B2C.values()},
}


# ============================================================
# Legs
# ============================================================

def gerar_stream(seed=7, segundos=30, trades_por_seg=4, burst_de=12,
                 burst_ate=16):
    """Stream deterministico: trades a cada 250ms + rajada no meio + book
    a cada segundo. Retorna (trades, books) no formato dos testes."""
    import random
    rng = random.Random(seed)
    S = 1_787_000_000
    trades, books = [], []
    p = 150000.0
    i = 0
    for s in range(segundos):
        n = 8 if burst_de <= s < burst_ate else trades_por_seg
        for k in range(n):
            ts = S * 1000 + s * 1000 + k * (1000 // n)
            # tendencia + ruido; rajada = volume alto
            qtd = rng.choice([1, 2, 5]) if not (burst_de <= s < burst_ate) \
                else rng.choice([5, 10, 20])
            p += 2 if s % 2 == 0 else -2
            p += rng.uniform(-0.5, 0.5)
            trades.append({
                'ts_ms': ts, 'preco': p, 'qtd': qtd,
                'agressor': 'Comprador' if (s + k) % 2 == 0 else 'Vendedor',
                'compradora': 'XP', 'vendedora': 'BTG',
            })
            i += 1
        bp = [round(p - j * 5, 2) for j in range(5, 0, -1)]
        ap = [round(p + j * 5, 2) for j in range(1, 6)]
        books.append({'ts_ms': S * 1000 + s * 1000,
                      'bid_preco': bp, 'ask_preco': ap,
                      'bid_vol': [40 + (s % 3) * 10] * 5,
                      'ask_vol': [35 + ((s + 1) % 3) * 10] * 5})
    return trades, books


def leg_a_realtime(trades):
    """Pema A: FeatureEngine/MarketState por segundo-bucket."""
    from core.market_state import MarketState
    from features.feature_engine import FeatureEngine
    state = MarketState(config={'save_dir': '.', 'ativo_principal': 'WINV26'})
    fe = FeatureEngine(state, config={})
    out = {}
    for t in trades:
        seg = int(t['ts_ms'] // 1000)
        f = fe.processar_lote('WINV26', [t], seg)
        if f:
            out[seg] = f
    return out


def leg_b_grid(trades, janela_ms=100):
    """Pema B: GeradorJanelas no grid do master clock. Retorna dict
    {ts_corte: snap} (ultimo snap por corte)."""
    from features.trade_features import GeradorJanelas
    g = GeradorJanelas(instrumentos=['WINV26'], janela_ms=janela_ms,
                       passo_ms=janela_ms)
    out = {}
    for t in trades:
        for a, snap in g.processar_evento(
                'WINV26', t['ts_ms'], t['preco'], t['qtd'], t['agressor'],
                t['compradora'], t['vendedora']):
            out[int(snap['ts_ms'])] = snap
    return out


def vp_identidade_fim(trades):
    """P0-A31: o VP do scorer (self.vps) e o do grid/dataset (vp_trackers
    do GeradorJanelas) sao a MESMA classe (VolumeProfileTracker) alimentada
    pelos mesmos trades — o perfil de fim de stream TEM que ser identico.
    A divergencia historica nao era o perfil, e sim o INSTANTE: o snap do
    corte embute o perfil ANTES do trade que cruza a borda (lag de 1 trade,
    semantica do dataset_100ms), enquanto o scorer lia self.vps no instante
    do trade (sem lag) — corrigido em v15.27 (fonte = snap do gerador).
    """
    from features.trade_features import GeradorJanelas
    from features.volume_profile import VolumeProfileTracker
    g = GeradorJanelas(instrumentos=['WINV26'], janela_ms=100, passo_ms=100)
    vps = VolumeProfileTracker(tick=5.0, value_area=0.70)
    for t in trades:
        g.processar_evento('WINV26', t['ts_ms'], t['preco'], t['qtd'],
                           t['agressor'], t['compradora'], t['vendedora'])
        vps.atualizar(t['ts_ms'], t['preco'], t['qtd'], t['agressor'])
    b_vol = getattr(g.vp_trackers['WINV26'], 'volumes', {})
    c_vol = vps.volumes
    iguais = b_vol == c_vol
    return {'igual': iguais, 'b_volumes': dict(b_vol), 'c_volumes': c_vol}


def _gerador_lag(instrumentos):
    """GeradorJanelas instrumentado: registra, por corte emitido, o vp do
    snap e o ts do trade que cruzou a borda (para medir o lag)."""
    from features.trade_features import GeradorJanelas as _GJ

    class _G(_GJ):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.emissões = []

        def processar_evento(self, ativo, ts_ms, preco, qtd, agressor,
                             comp, vend):
            snaps = super().processar_evento(ativo, ts_ms, preco, qtd,
                                             agressor, comp, vend)
            for a, snap in snaps:
                vp = snap.get('vp')
                if vp:
                    self.emissões.append({'ts_corte': snap['ts_ms'],
                                          'ts_trade': ts_ms, 'vp': dict(vp)})
            return snaps

    return _G(instrumentos=instrumentos, janela_ms=100, passo_ms=100)


def vp_lag_1_trade(trades, burst_de=12, burst_ate=16, segundos=60):
    """P0-A31/v15.28: valida a semantica VP_LAG_1_TRADE em rajadas reais.

    O snap do corte embute o perfil causal AS-OF (ultimo trade ESTRITAMENTE
    antes do corte). O scorer p/ v15.27 lia o perfil no instante do trade que
    cruzou a borda (pos-adicao) — divergencia de ~1 trade, o bug A31. Aqui:
    para CADA corte c emitido, um tracker INDEPENDENTE (mesma classe, mesma
    alimentacao — simulando o scorer-side) e avancado ate o ultimo trade
    com ts < c e comparado campo-a-campo com o vp do snap.

    Esperado: max_diff == 0 em 100% dos cortes (inclusive rajadas) — prova
    que o lag do corte NAO desvia o VP do dataset do VP causal real.
    """
    from features.volume_profile import VolumeProfileTracker
    g = _gerador_lag(['WINV26'])
    for t in trades:
        g.processar_evento('WINV26', t['ts_ms'], t['preco'], t['qtd'],
                           t['agressor'], t['compradora'], t['vendedora'])
    cortes = sorted(g.emissões, key=lambda e: e['ts_corte'])
    tsorted = sorted(trades, key=lambda t: t['ts_ms'])
    campos = list(VP_B2C.values())
    indep = VolumeProfileTracker(tick=5.0, value_area=0.70)
    idx = 0
    stats = {c: {'n': 0, 'n_diff': 0, 'max_diff': 0.0, 'n_burst': 0,
                 'max_diff_burst': 0.0} for c in campos}
    seg_base = tsorted[0]['ts_ms'] // 1000
    for e in cortes:
        c = e['ts_corte']
        while idx < len(tsorted) and tsorted[idx]['ts_ms'] < c:
            t = tsorted[idx]
            indep.atualizar(t['ts_ms'], t['preco'], t['qtd'], t['agressor'])
            idx += 1
        if idx == 0:
            continue  # corte antes do 1o trade — sem estado (não emitido)
        preco_ref = tsorted[idx - 1]['preco']
        calc = indep.calcular(preco_ref)
        em_burst = burst_de <= (e['ts_trade'] // 1000 - seg_base) < burst_ate
        for f in campos:
            bv = e['vp'].get(f)
            cv = calc.get(f)
            if bv is None or cv is None:
                continue
            d = abs(float(bv) - float(cv))
            st = stats[f]
            st['n'] += 1
            if d > 1e-9:
                st['n_diff'] += 1
            st['max_diff'] = max(st['max_diff'], d)
            if em_burst:
                st['n_burst'] += 1
                st['max_diff_burst'] = max(st['max_diff_burst'], d)
    return {'campos': stats, 'n_cortes': len(cortes)}


def leg_c_scorer(trades):
    """Pema C: a pilha de trackers que o ScorerML usa, consumindo o MESMO
    fluxo. Retorna dict {segundo: snapshot combinado apos o ultimo trade do
    segundo} — equivale ao estado que o scorer le no corte seguinte."""
    from features.volatility import VolatilityTracker
    from features.returns import ReturnsTracker
    from features.volume_profile import VolumeProfileTracker
    from features.poc_migration import PocMigrationTracker
    from features.vwap_tracker import VWAPTracker
    vt, rt = VolatilityTracker(), ReturnsTracker()
    vps = VolumeProfileTracker(tick=5.0)
    mig = PocMigrationTracker()
    vw = VWAPTracker('WINV26', tick=5.0)
    out = {}
    for t in trades:
        ts, pr, qtd = t['ts_ms'], t['preco'], t['qtd']
        agr = t['agressor']
        vt.update(ts, pr)
        rt.update(ts, pr)
        vps.atualizar(ts, pr, qtd, agr)
        vp_snap = vps.calcular(pr)
        mig.update(ts, pr, pr + vp_snap['poc_dist'])
        vw.update(ts, pr, qtd)
        seg = ts // 1000
        snap = dict(vt.snapshot())
        snap.update(rt.snapshot())
        snap.update(vw.snapshot())
        snap.update(vp_snap)
        snap.update(mig.snapshot())
        out[seg] = snap  # ultimo trade do segundo = estado do corte seguinte
    return out


def _grid_denso_ffill(grid: Dict[int, dict]):
    """Reindexa os cortes de B para a grade DENSA de 100ms (mesmo do
    dataset_100ms de treino) com forward-fill do preco_ultimo — elimina o
    artefato de cortes esparsos (so cortes com trade) na referencia pandas."""
    if not grid:
        return {}
    ts = sorted(int(t) for t in grid
                if grid[t].get('preco_ultimo') is not None
                and grid[t]['preco_ultimo'] > 0)
    if not ts:
        return {}
    denso = {}
    ultimo_p = None
    inicio = (ts[0] // 100) * 100
    fim = ts[-1]
    grid_p = {int(t): float(grid[t]['preco_ultimo']) for t in ts}
    t = inicio
    while t <= fim:
        if t in grid_p:
            ultimo_p = grid_p[t]
        if ultimo_p is not None:
            denso[t] = ultimo_p
        t += 100
    return denso


def offline_vol_ret(grid: Dict[int, dict], cobertura: int = 0) -> Dict[str, float]:
    """Referencia OFFLINE das features vol_*/retorno_*: o transform pandas do
    features_expansao (pct_change(n).abs().ewm(alpha=2/(n+1)) p/ vol;
    pct_change(n) p/ retorno) aplicado sobre a grade DENSA forward-filled de
    100ms derivada de B — exatamente o que o dataset_100ms de treino contem.

    Retorna valores + sufixo de cobertura: nome sem horizonte coberto recebe
    None (nao confundir com zero legitimo).
    """
    import math
    import pandas as pd
    denso = _grid_denso_ffill(grid)
    out = {}
    if not denso:
        return out
    s = pd.Series(list(denso.values()))
    for n, nome in _HORIZONTES_VOL:
        if len(s) > n:
            ret = s.pct_change(n).abs()
            val = float(ret.ewm(alpha=2.0 / (n + 1), adjust=False).mean().iloc[-1])
            out[f'vol_{nome}'] = val if math.isfinite(val) else 0.0
        else:
            out[f'vol_{nome}'] = None  # sem cobertura temporal no stream
    for n in _RETORNOS:
        if len(s) > n:
            val = float(s.pct_change(n).iloc[-1])
            out[f'retorno_{n}x100ms'] = val if math.isfinite(val) else 0.0
        else:
            out[f'retorno_{n}x100ms'] = None
    return out


def rodar_paridade(segundos=30):
    """Roda as 3 pemas + referencia offline no mesmo stream deterministico.

    Returns:
        (legA, legB_grid, legC, offline, trades, books, vp_id)
    """
    trades, books = gerar_stream(segundos=segundos)
    grid = leg_b_grid(trades)
    return (leg_a_realtime(trades), grid,
            leg_c_scorer(trades), offline_vol_ret(grid),
            trades, books, vp_identidade_fim(trades),
            vp_lag_1_trade(trades, segundos=segundos))


def relatorio(leg_a, leg_b_grid, leg_c, offline=None, verbose=True,
              vp_id=None, vp_lag=None):
    """Compara nome-a-nome segundo o CONTRATO. Retorna lista de linhas
    {'feature', 'classe', 'legs', 'max_diff', 'status'}."""
    linhas = []
    segs_c = sorted(leg_c)

    # 1. vol/ret: C (estado apos o ultimo trade) vs offline (referencia pandas
    #    sobre a grade densa de B ate o MESMO instante). None = sem cobertura
    #    temporal no stream (horizonte maior que a historia) — nunca comparar.
    for nome, classe in sorted(CONTRATO.items()):
        if classe == 'MESMA_DEFINICAO' and offline and segs_c:
            c_val = leg_c[segs_c[-1]].get(nome)
            o_val = offline.get(nome)
            if o_val is None:
                status = 'SEM_COBERTURA'
                diff = None
            elif c_val is None:
                status = 'C_AUSENTE'
                diff = None
            else:
                diff = abs(float(c_val) - float(o_val))
                # Tolerancia = borda de <= 1 linha do grid (instante final
                # alinhado ao ultimo corte/trade): vol ~5e-7, retorno ~3e-5
                # medidos no stream; regressao semantica real e ordens maior.
                if nome.startswith('vol_'):
                    tol = 1e-5 + 1e-2 * abs(float(o_val))
                else:
                    tol = 1e-4 + 1e-2 * abs(float(o_val))
                status = 'OK' if diff <= tol else 'DIVERGE'
            linhas.append({
                'feature': nome, 'classe': classe,
                'legs': 'B_offline x C', 'max_diff': diff, 'status': status})
        elif classe == 'VP_PARALELO':
            if vp_id is None:
                continue
            linhas.append({
                'feature': 'vp (perfil fim de stream)',
                'classe': classe, 'legs': 'B(gerador) x C(scorer)',
                'max_diff': 0 if vp_id['igual'] else None,
                'status': 'OK' if vp_id['igual'] else 'DIVERGE'})
        elif classe == 'VP_LAG_1_TRADE' and vp_lag:
            st = vp_lag['campos'].get('poc_dist')
            if st is None:
                continue
            ok = st['n'] > 0 and st['n_diff'] == 0
            linhas.append({
                'feature': 'vp as-of corte x indep (lag 1 trade)',
                'classe': classe,
                'legs': 'B(snap) x indep(as-of ts<c)',
                'max_diff': st['max_diff'],
                'status': 'OK' if ok else 'DIVERGE'})
    # VP_LAG_1_TRADE campo a campo (prova: snap == estado causal as-of)
    if vp_lag is not None:
        for f, st in vp_lag['campos'].items():
            if st['n'] == 0:
                continue
            linhas.append({
                'feature': f'vp.{f} (as-of vs snap)',
                'classe': 'VP_LAG_1_TRADE', 'legs': 'B(snap) x indep',
                'max_diff': st['max_diff'],
                'status': 'OK' if st['n_diff'] == 0 else 'DIVERGE'})
        b0 = next(iter(vp_lag['campos'].values()))
        linhas.append({
            'feature': f"vp lag — rajadas ({b0['n_burst']} cortes)",
            'classe': 'VP_LAG_1_TRADE', 'legs': 'burst only',
            'max_diff': b0['max_diff_burst'],
            'status': 'OK' if b0['n_burst'] > 0 and b0['max_diff_burst'] == 0
            else 'DIVERGE'})

    # 2. nomes AGR_DIFERENTE: reportar presenca (nunca comparar como igual)
    if leg_a and segs_c:
        seg_a = sorted(leg_a)[-1]
        seg_c = segs_c[-1]
        for nome in [k for k, v in CONTRATO.items() if v == 'AGR_DIFERENTE']:
            a_val = leg_a[seg_a].get(nome)
            c_val = leg_c[seg_c].get(nome)
            linhas.append({
                'feature': nome, 'classe': 'AGR_DIFERENTE',
                'legs': 'A(1s) x C(grid)',
                'max_diff': None if (a_val is None or c_val is None)
                else abs(float(a_val) - float(c_val)),
                'status': 'PRESENTE_AMBOS' if a_val is not None
                and c_val is not None else 'PARCIAL'})
    if verbose:
        for l in linhas:
            d = 'n/a' if l['max_diff'] is None else f"{l['max_diff']:.6g}"
            print(f"[{l['status']:<12}] {l['feature']:<26} {l['legs']:<18} "
                  f"diff={d}")
    return linhas


if __name__ == '__main__':  # pragma: no cover
    A, B, C, off, trades, books, vp_id, vp_lag = rodar_paridade(segundos=60)
    print(f'trades={len(trades)} | seg_realtime={len(A)} | cortes_grid={len(B)}')
    relatorio(A, B, C, off, vp_id=vp_id, vp_lag=vp_lag)
